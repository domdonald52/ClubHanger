from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.db import transaction
from datetime import datetime, timedelta, time
from .models import (Club, ClubMember, Booking, Aircraft, Role, FlightType, BlockOutType,
                     SlotWatch, InstructorGrade, AircraftSurchargeType,
                     Aerodrome, FuelSurchargeRate)
from .availability import find_available_slots, get_date_range


def _aware(dt):
    """Make a naive datetime timezone-aware in the active timezone."""
    if dt is not None and timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt


def _audit(booking, user, event_type, notes='', field_name='', old_value='', new_value=''):
    """Write a booking audit log entry, swallowing errors so it never blocks an action."""
    try:
        from .models import BookingAuditLog
        BookingAuditLog.objects.create(
            booking=booking, user=user, event_type=event_type,
            notes=notes, field_name=field_name, old_value=str(old_value), new_value=str(new_value),
        )
    except Exception as e:
        print(f"audit log failed: {e}")


def _blockout_check(club, aircraft, instructor, start_dt, end_dt, actor, override, exclude_booking_id=None):
    """
    Scope-aware block-out conflict check for a prospective booking.
    Returns (blocked: bool, message: str, hits: list, is_soft: bool).

    Hard block-outs (BlockOutType.is_hard=True, or no type): members are blocked outright;
      staff can override with confirmation.
    Soft block-outs (BlockOutType.is_hard=False): everyone gets a warning and can confirm
      to proceed — no staff-only gate.
    """
    from .models import BlockOut

    class _Probe:
        pass
    probe = _Probe()
    probe.club = club
    probe.aircraft_id = aircraft.id if aircraft else None
    probe.instructor_id = instructor.id if instructor else None
    probe.scheduled_start = start_dt
    probe.scheduled_end = end_dt

    hits = [bo for bo in BlockOut.objects.filter(club=club).prefetch_related('aircraft', 'instructors', 'blockout_type')
            if bo.overlaps_booking(probe)]
    if not hits:
        return (False, '', [], False)

    def _name(h):
        return h.blockout_type.name if h.blockout_type else (h.label or 'block-out')

    # Aircraft block-out types are always hard; instructor types respect is_hard
    hard_hits = [h for h in hits if not h.blockout_type or h.blockout_type.effective_is_hard]
    soft_hits = [h for h in hits if h.blockout_type and not h.blockout_type.effective_is_hard]
    is_staff = actor and (actor.is_admin or actor.is_instructor)

    if hard_hits:
        names = ', '.join(sorted({_name(h) for h in hard_hits}))
        if is_staff and override:
            return (False, names, hits, False)
        if is_staff:
            return (True, f"This overlaps a block-out ({names}). Override?", hits, False)
        return (True, f"This time is blocked ({names}) and can't be booked.", hits, False)

    # Soft block-outs only — anyone can confirm and proceed
    names = ', '.join(sorted({_name(h) for h in soft_hits}))
    if override:
        return (False, names, hits, True)
    return (True, f"Advisory: {names} is in effect. Book anyway?", hits, True)


@login_required
def index(request):
    club_member = ClubMember.objects.filter(user=request.user).first()
    if club_member:
        return redirect('core:gantt_day', club_slug=club_member.club.slug)
    return render(request, 'core/no_access.html')


def get_config(club):
    """Return the club's config, creating a default if missing."""
    from .models import ClubConfig
    config, _ = ClubConfig.objects.get_or_create(club=club)
    return config


@login_required
def gantt_day(request, club_slug, year=None, month=None, day=None):
    club = get_object_or_404(Club, slug=club_slug)
    try:
        club_member = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')

    config = get_config(club)

    if not year:
        today = timezone.localdate()
        year, month, day = today.year, today.month, today.day
    
    selected_date = datetime(year, month, day).date()
    day_start = _aware(datetime.combine(selected_date, config.operating_hours_start))
    day_end = _aware(datetime.combine(selected_date, config.operating_hours_end))
    slot_minutes = config.time_slot_interval
    
    # All instructors including inactive — ghost rows keep conflicted bookings visible.
    instructors = ClubMember.objects.filter(
        club=club, role__name__iexact='instructor'
    ).select_related('user').order_by('standing', 'user__last_name')
    
    aircraft_list = Aircraft.objects.filter(club=club, status='online').order_by('registration')
    all_aircraft = Aircraft.objects.filter(club=club).order_by('registration')
    
    # Get all bookings for day
    bookings = Booking.objects.filter(
        club=club,
        scheduled_start__gte=day_start,
        scheduled_start__lt=day_end + timedelta(days=1)
    ).exclude(status='cancelled').select_related('member__user', 'aircraft', 'instructor', 'confirmed_by', 'flight_type')
    
    # Pixel geometry for absolute-positioned pills
    px_per_min = float(request.GET.get('zoom') or 2)
    # Atypical-hours boundaries in pixels from day_start (for calendar shading)
    typ_start_dt = _aware(datetime.combine(selected_date, config.typical_hours_start))
    typ_end_dt = _aware(datetime.combine(selected_date, config.typical_hours_end))
    total_minutes = int((day_end - day_start).total_seconds() // 60)
    track_width = int(total_minutes * px_per_min)
    typical_start_px = max(0, int((typ_start_dt - day_start).total_seconds() / 60 * px_per_min))
    typical_end_px = min(track_width, int((typ_end_dt - day_start).total_seconds() / 60 * px_per_min))

    # Column ticks (one per slot interval) for the header + gridlines
    ticks = []
    t = day_start
    while t <= day_end:
        offset = int((t - day_start).total_seconds() // 60 * px_per_min)
        ticks.append({'label': t.strftime('%H:%M'), 'left': offset})
        t += timedelta(minutes=slot_minutes)

    PILL_GAP = 2  # px gap either side so adjacent pills don't butt up

    def _check_live_conflict(b):
        """Live check for all conflict types. Returns (has_conflict, reason_str, issue_types_list)."""
        issues = []

        if not b.blockout_override:
            if b.blockout_conflict:
                issues.append(('blockout', b.blockout_conflict_reason or 'Block-out conflict'))
            else:
                for bo in day_blockouts:
                    if not bo.affects_booking(b):
                        continue
                    iv = bo.interval_on(selected_date)
                    if iv and iv[0] < b.scheduled_end and iv[1] > b.scheduled_start:
                        tname = bo.blockout_type.name if bo.blockout_type else (bo.label or 'block-out')
                        issues.append(('blockout', f"Overlaps {tname}"))
                        break

        if b.member and not b.member.is_current:
            issues.append(('member', f"Member {b.member.get_standing_display()}"))

        if b.aircraft and b.aircraft.status == 'retired':
            issues.append(('aircraft', f"Aircraft {b.aircraft.registration} is retired"))

        if not issues:
            return False, '', []
        return True, '; '.join(r for _, r in issues), [t for t, _ in issues]

    def booking_geometry(b):
        start_min = max(0, int((b.scheduled_start - day_start).total_seconds() // 60))
        dur_min = max(slot_minutes, int((b.scheduled_end - b.scheduled_start).total_seconds() // 60))
        left = int(start_min * px_per_min)
        width = int(dur_min * px_per_min)
        local_start = timezone.localtime(b.scheduled_start)
        local_end = timezone.localtime(b.scheduled_end)
        desc = getattr(b, 'description', '') or getattr(b, 'notes', '') or ''
        member_name = ''
        if b.member and b.member.user:
            member_name = f"{b.member.user.first_name} {b.member.user.last_name}".strip()
        in_conflict, conflict_reason, issue_types = _check_live_conflict(b)
        return {
            'id': b.id,
            'left': left + PILL_GAP,
            'width': max(width - PILL_GAP * 2, 4),
            'status': b.status,
            'member_name': member_name or 'Unknown',
            'member_user_id': b.member.user_id if b.member else '',
            'description': desc,
            'aircraft_id': b.aircraft_id,
            'aircraft_reg': b.aircraft.registration if b.aircraft else '',
            'instructor_id': b.instructor_id,
            'instructor_name': (f"{b.instructor.first_name} {b.instructor.last_name}".strip()
                                if b.instructor else ''),
            'start_label': local_start.strftime('%H:%M'),
            'end_label': local_end.strftime('%H:%M'),
            'start_iso': b.scheduled_start.isoformat(),
            'duration_min': dur_min,
            'conflict': in_conflict,
            'override': b.blockout_override,
            'conflict_reason': conflict_reason,
            'issue_types': ','.join(issue_types),
            'flight_type_id': b.flight_type_id or '',
            'flight_type_name': b.flight_type.name if b.flight_type else '',
            'member_not_current': b.member is not None and not b.member.is_current,
            'member_standing': b.member.standing if b.member else '',
        }

    # Apply filters — suppress when arriving via booking deep-link (?book=1) because
    # that URL shares 'aircraft' and 'instructor' params with the modal pre-fill,
    # which would otherwise silently filter out all other bookings from the view.
    is_deeplink = request.GET.get('book') == '1'
    show_pending = request.GET.get('pending_only') == 'on'
    filter_instructor = None if is_deeplink else request.GET.get('instructor')
    filter_aircraft = None if is_deeplink else request.GET.get('aircraft')

    # Block-outs for this day, as geometry bands per resource
    from .models import BlockOut
    day_blockouts = [
        bo for bo in BlockOut.objects.filter(club=club).prefetch_related('aircraft', 'instructors', 'blockout_type')
        if bo.applies_on(selected_date)
    ]

    def band_geometry(bo):
        if bo.all_day or not (bo.start_time and bo.end_time):
            bo_start, bo_end = day_start, day_end
        else:
            bo_start = _aware(datetime.combine(selected_date, bo.start_time))
            bo_end = _aware(datetime.combine(selected_date, bo.end_time))
        # clamp to operating window
        bo_start = max(bo_start, day_start)
        bo_end = min(bo_end, day_end)
        if bo_end <= bo_start:
            return None
        left = int((bo_start - day_start).total_seconds() // 60 * px_per_min)
        width = int((bo_end - bo_start).total_seconds() // 60 * px_per_min)
        is_hard = (not bo.blockout_type) or bo.blockout_type.is_hard
        return {
            'left': left, 'width': width,
            'label': (bo.blockout_type.name if bo.blockout_type else (bo.label or 'Blocked')),
            'color': (bo.blockout_type.color if bo.blockout_type else '#9aa3ad'),
            'start_label': bo_start.strftime('%H:%M'),
            'end_label': bo_end.strftime('%H:%M'),
            'is_hard': is_hard,
        }

    def bands_for_aircraft(ac):
        out = []
        for bo in day_blockouts:
            if bo.affects_aircraft(ac):
                g = band_geometry(bo)
                if g:
                    out.append(g)
        return out

    def bands_for_instructor(user):
        out = []
        for bo in day_blockouts:
            if bo.affects_instructor(user):
                g = band_geometry(bo)
                if g:
                    out.append(g)
        return out

    # Include inactive instructors so ghost rows can show conflicted bookings.
    # Re-query without is_active filter (the base query already has all instructors).
    from .models import InstructorAvailability
    _av_cache = {}  # club_member_id → bool | None
    for instr in instructors:
        windows = list(InstructorAvailability.objects.filter(club_member=instr))
        if not windows:
            _av_cache[instr.id] = None  # no schedule declared — treated as always available
        else:
            _av_cache[instr.id] = any(w.applies_on(selected_date) for w in windows)

    # Build grid data
    instructor_rows = []
    for instr in instructors:
        instr_bookings = bookings.filter(instructor=instr.user)
        if show_pending:
            instr_bookings = instr_bookings.filter(status='pending')
        if filter_aircraft:
            instr_bookings = instr_bookings.filter(aircraft_id=filter_aircraft)

        on_roster = _av_cache.get(instr.id)   # None / True / False
        has_bookings = instr_bookings.exists()
        # Operationally active: not resigned (standing may be suspended/lapsed but
        # could still have bookings to honour; resigned is definitive departure)
        is_active = instr.standing not in ('resigned',)

        normal_show = is_active and on_roster is not False
        ghost = (not normal_show) and has_bookings
        if not normal_show and not ghost:
            continue

        if instr.standing in ('resigned', 'lapsed'):
            ghost_reason = 'inactive'
        elif on_roster is False:
            ghost_reason = 'off_roster'
        else:
            ghost_reason = None

        bands = bands_for_instructor(instr.user)
        instructor_rows.append({
            'type': 'instructor',
            'label': f"{instr.user.first_name} {instr.user.last_name}".strip() or instr.user.username,
            'row_key': f"instructor:{instr.user.id}",
            'resource_id': instr.user.id,
            'pills': [booking_geometry(b) for b in instr_bookings],
            'bands': bands,
            'is_current_user': instr.user == request.user,
            'on_roster': on_roster,
            'ghost': ghost,
            'ghost_reason': ghost_reason,  # 'inactive' | 'off_roster' | None
            'has_hard_blockout': any(b['is_hard'] for b in bands),
            'has_soft_blockout': any(not b['is_hard'] for b in bands),
        })

    # Ghost rows for users who had the instructor role removed but still have bookings today
    shown_instructor_ids = {row['resource_id'] for row in instructor_rows}
    ex_instructor_ids = {
        b.instructor_id for b in bookings
        if b.instructor_id and b.instructor_id not in shown_instructor_ids
    }
    if ex_instructor_ids:
        from .models import User as _User
        ex_users = {u.id: u for u in _User.objects.filter(id__in=ex_instructor_ids)}
        for user_id, user in ex_users.items():
            ex_bookings = bookings.filter(instructor_id=user_id)
            if show_pending:
                ex_bookings = ex_bookings.filter(status='pending')
            if filter_aircraft:
                ex_bookings = ex_bookings.filter(aircraft_id=filter_aircraft)
            if not ex_bookings.exists():
                continue
            instructor_rows.append({
                'type': 'instructor',
                'label': f"{user.first_name} {user.last_name}".strip() or user.username,
                'row_key': f"instructor:{user.id}",
                'resource_id': user.id,
                'pills': [booking_geometry(b) for b in ex_bookings],
                'bands': [],
                'is_current_user': user == request.user,
                'on_roster': None,
                'ghost': True,
                'ghost_reason': 'role_changed',
                'has_hard_blockout': False,
                'has_soft_blockout': False,
            })

    aircraft_rows = []
    for ac in all_aircraft:
        is_online = ac.status == 'online'
        ac_bookings = bookings.filter(aircraft=ac)
        if show_pending:
            ac_bookings = ac_bookings.filter(status='pending')
        if filter_instructor:
            ac_bookings = ac_bookings.filter(instructor_id=filter_instructor)

        has_bookings = ac_bookings.exists()
        if not is_online and not has_bookings:
            continue  # retired with nothing booked today: omit entirely

        ghost = not is_online
        ac_bands = bands_for_aircraft(ac) if is_online else []
        aircraft_rows.append({
            'type': 'aircraft',
            'label': f"{ac.registration} ({ac.aircraft_type})",
            'row_key': f"aircraft:{ac.id}",
            'resource_id': ac.id,
            'pills': [booking_geometry(b) for b in ac_bookings],
            'bands': ac_bands,
            'has_blockout': bool(ac_bands),
            'ghost': ghost,
            'ghost_reason': 'retired' if ghost else None,
        })

    # Navigation
    prev_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)
    today = timezone.localdate()

    _members_qs = (ClubMember.objects
                   .filter(club=club, user__isnull=False)
                   .select_related('user')
                   .prefetch_related('account'))
    members_data = []
    for m in _members_qs:
        try:
            acct_warning = m.account.has_warning
        except Exception:
            acct_warning = False
        # Badge shown next to the member name in the booking modal
        if m.is_current:
            badge = 'current'
        elif m.standing == 'non_member':
            badge = 'non_member'
        else:
            badge = 'lapsed'   # suspended / lapsed / resigned / pending
        members_data.append({
            'id': m.user.id,
            'name': f"{m.user.first_name} {m.user.last_name}".strip() or m.user.username,
            'badge': badge,
            'acct_warning': acct_warning,
        })
    aircraft_data = [
        {'id': a.id, 'reg': a.registration, 'type': a.aircraft_type} for a in aircraft_list
    ]
    instructors_data = [
        {'id': i.user.id, 'name': f"{i.user.first_name} {i.user.last_name}".strip()} for i in instructors
    ]
    flight_types_data = [
        {'id': ft.id, 'name': ft.name, 'code': ft.code, 'is_solo': ft.is_solo}
        for ft in FlightType.objects.filter(club=club)
    ]
    blockout_types_data = [
        {'id': bt.id, 'name': bt.name, 'target': bt.target}
        for bt in BlockOutType.objects.filter(club=club)
    ]

    zoom_param = request.GET.get('zoom', '')
    can_manage = club_member.is_instructor or club_member.is_admin
    context = {
        'club': club,
        'club_member': club_member,
        'is_instructor': club_member.is_instructor,
        'can_book': True,  # all authenticated club members may create bookings
        'can_manage': can_manage,
        'current_user_id': request.user.id,
        'selected_date': selected_date,
        'today': today,
        'prev_date': prev_date,
        'next_date': next_date,
        'instructor_rows': instructor_rows,
        'aircraft_rows': aircraft_rows,
        'ticks': ticks,
        'track_width': track_width,
        'px_per_min': px_per_min,
        'slot_minutes': slot_minutes,
        'instructors': instructors,
        'aircraft_list': aircraft_list,
        'default_duration': config.default_booking_duration,
        'day_start_iso': day_start.isoformat(),
        'typical_hours_start': config.typical_hours_start.strftime('%H:%M'),
        'typical_hours_end': config.typical_hours_end.strftime('%H:%M'),
        'typical_start_px': typical_start_px,
        'typical_end_px': typical_end_px,
        'total_minutes': total_minutes,
        'zoom_param': zoom_param,
        'members_json': members_data,
        'aircraft_json': aircraft_data,
        'instructors_json': instructors_data,
        'flight_types_json': flight_types_data,
        'blockout_types_json': blockout_types_data,
        'watched_ids': list(
            SlotWatch.objects.filter(club_member=club_member)
            .values_list('booking_id', flat=True)
        ),
    }

    return render(request, 'core/gantt_day.html', context)


@login_required
@require_POST
@transaction.atomic
def reschedule_booking(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)
    club = booking.club
    
    try:
        club_member = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return JsonResponse({'error': 'Not authorized'}, status=403)
    
    # Allow admin and instructor to reschedule
    if not (club_member.is_admin or club_member.is_instructor):
        return JsonResponse({'error': 'Only instructors and admins can reschedule'}, status=403)
    
    new_start = request.POST.get('new_start')
    aircraft_id = request.POST.get('aircraft_id')
    instructor_id = request.POST.get('instructor_id')
    duration_param = request.POST.get('duration')
    
    if not new_start:
        return JsonResponse({'error': 'Missing new_start'}, status=400)
    
    try:
        new_start_dt = _aware(datetime.fromisoformat(new_start))
        if duration_param:
            duration = int(duration_param)
        else:
            duration = (booking.scheduled_end - booking.scheduled_start).total_seconds() / 60
        new_end_dt = new_start_dt + timedelta(minutes=duration)
        
        # Determine aircraft to check
        aircraft = booking.aircraft
        if aircraft_id:
            aircraft = Aircraft.objects.filter(id=aircraft_id, club=club).first()
            if not aircraft:
                return JsonResponse({'error': 'Aircraft not found'}, status=404)
        
        # Aircraft conflict (ignore cancelled, exclude self)
        if Booking.objects.filter(
            club=club, aircraft=aircraft,
            scheduled_start__lt=new_end_dt, scheduled_end__gt=new_start_dt,
        ).exclude(id=booking.id).exclude(status='cancelled').exists():
            return JsonResponse({'error': 'Aircraft not available at new time'}, status=409)
        
        # Instructor conflict if specified
        target_instructor = booking.instructor
        if instructor_id:
            from .models import User
            instructor = User.objects.filter(id=instructor_id).first()
            if instructor:
                if Booking.objects.filter(
                    club=club, instructor=instructor,
                    scheduled_start__lt=new_end_dt, scheduled_end__gt=new_start_dt,
                ).exclude(id=booking.id).exclude(status='cancelled').exists():
                    return JsonResponse({'error': 'Instructor not available at new time'}, status=409)
                target_instructor = instructor

        # Block-out check (scope-aware)
        override = request.POST.get('override') in ('1', 'true', 'on')
        blocked, msg, hits, is_soft = _blockout_check(club, aircraft, target_instructor,
                                                      new_start_dt, new_end_dt, club_member, override,
                                                      exclude_booking_id=booking.id)
        if blocked:
            can_override = is_soft or bool(club_member.is_admin or club_member.is_instructor)
            return JsonResponse({'error': msg, 'blockout': True,
                                 'can_override': can_override, 'soft': is_soft}, status=409)

        if instructor_id and target_instructor:
            booking.instructor = target_instructor
        booking.scheduled_start = new_start_dt
        booking.scheduled_end = new_end_dt
        if aircraft_id:
            booking.aircraft = aircraft
        booking.blockout_override = bool(hits and override)

        booking.save()

        # Recompute conflict flag against current block-outs after the move
        from .models import recompute_blockout_conflict
        recompute_blockout_conflict(booking)

        _audit(booking, request.user, 'field_changed', notes='Rescheduled')
        if hits and override:
            _audit(booking, request.user, 'warning_acknowledged',
                   notes=f"Staff override of block-out on reschedule: {msg}")

        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_POST
def confirm_booking(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)
    club = booking.club
    
    try:
        club_member = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return JsonResponse({'error': 'Not authorized'}, status=403)
    
    if not (club_member.is_admin or club_member.is_instructor):
        return JsonResponse({'error': 'Only instructors and admins can confirm'}, status=403)

    booking.status = 'confirmed'
    booking.confirmed_by = request.user
    booking.confirmed_at = timezone.now()
    booking.save()
    
    return JsonResponse({'success': True})


@login_required
@require_POST
def toggle_watch(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)
    try:
        member = ClubMember.objects.get(user=request.user, club=booking.club)
    except ClubMember.DoesNotExist:
        return JsonResponse({'error': 'Not a member'}, status=403)
    if booking.member == member:
        return JsonResponse({'error': "Can't watch your own booking"}, status=400)
    watch, created = SlotWatch.objects.get_or_create(booking=booking, club_member=member)
    if not created:
        watch.delete()
        return JsonResponse({'watching': False})
    return JsonResponse({'watching': True})


@login_required
@require_POST
def reject_booking(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)
    club = booking.club

    try:
        club_member = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return JsonResponse({'error': 'Not authorized'}, status=403)

    is_own = booking.member == club_member
    if not (club_member.is_admin or club_member.is_instructor or is_own):
        return JsonResponse({'error': 'Not authorized'}, status=403)

    booking.status = 'cancelled'

    if request.POST.get('release') == '1':
        booking.slot_released = True
        booking.slot_released_at = timezone.now()
        booking.slot_released_by = request.user

    booking.save()
    return JsonResponse({'success': True})


@login_required
@require_POST
@transaction.atomic
def create_booking(request):
    try:
        actor = ClubMember.objects.filter(user=request.user).first()
        if not actor:
            return JsonResponse({'error': 'Not a club member'}, status=403)

        club = actor.club
        config = get_config(club)
        aircraft_id = request.POST.get('aircraft_id')
        start_time = request.POST.get('start_time')
        duration = int(request.POST.get('duration') or config.default_booking_duration)
        instructor_id = request.POST.get('instructor_id')
        member_id = request.POST.get('member_id')
        description = request.POST.get('description', '')

        if not aircraft_id or not start_time:
            return JsonResponse({'error': 'Missing aircraft or start time'}, status=400)

        # Who is the booking FOR? Staff can book on another member's behalf.
        booking_member = actor
        if member_id and (actor.is_admin or actor.is_instructor):
            from .models import User
            target_user = User.objects.filter(id=member_id).first()
            if target_user:
                booking_member = ClubMember.objects.filter(user=target_user, club=club).first() or actor

        aircraft = Aircraft.objects.get(id=aircraft_id, club=club)

        start_dt = _aware(datetime.fromisoformat(start_time))
        end_dt = start_dt + timedelta(minutes=duration)

        # Aircraft conflict
        if Booking.objects.filter(
            club=club, aircraft=aircraft,
            scheduled_start__lt=end_dt, scheduled_end__gt=start_dt,
        ).exclude(status='cancelled').exists():
            return JsonResponse({'error': 'Aircraft already booked at that time'}, status=409)

        instructor = None
        if instructor_id:
            from .models import User
            instructor = User.objects.filter(id=instructor_id).first()
            # Instructor conflict
            if instructor and Booking.objects.filter(
                club=club, instructor=instructor,
                scheduled_start__lt=end_dt, scheduled_end__gt=start_dt,
            ).exclude(status='cancelled').exists():
                return JsonResponse({'error': 'Instructor already booked at that time'}, status=409)

        # Block-out check: hard blocks need staff override; soft blocks anyone can confirm.
        override = request.POST.get('override') in ('1', 'true', 'on')
        blocked, msg, hits, is_soft = _blockout_check(club, aircraft, instructor, start_dt, end_dt, actor, override)
        if blocked:
            can_override = is_soft or bool(actor.is_admin or actor.is_instructor)
            return JsonResponse({'error': msg, 'blockout': True,
                                 'can_override': can_override, 'soft': is_soft}, status=409)

        flight_type_id = request.POST.get('flight_type_id')
        if flight_type_id:
            flight_type = FlightType.objects.filter(club=club, id=flight_type_id).first()
        elif not instructor:
            flight_type = (FlightType.objects.filter(club=club, is_solo=True, code='student_solo').first()
                           or FlightType.objects.filter(club=club, is_solo=True).first())
        else:
            flight_type = (FlightType.objects.filter(club=club, code='student_dual').first()
                           or FlightType.objects.filter(club=club, is_solo=False).first())
        if not flight_type:
            flight_type = FlightType.objects.filter(club=club).first()
        if not flight_type:
            return JsonResponse({'error': 'No flight types configured'}, status=400)

        # Solo flight types must not have an instructor
        if flight_type.is_solo:
            instructor = None

        booking = Booking.objects.create(
            club=club,
            member=booking_member,
            aircraft=aircraft,
            scheduled_start=start_dt,
            scheduled_end=end_dt,
            created_by=request.user,
            instructor=instructor,
            status='pending',
            flight_type=flight_type,
            description=description,
            blockout_override=bool(hits and override),
        )

        _audit(booking, request.user, 'created', notes='Booking created')
        if hits and override:
            _audit(booking, request.user, 'warning_acknowledged',
                   notes=f"Staff override of block-out: {msg}")

        return JsonResponse({'success': True, 'booking_id': booking.id})

    except Aircraft.DoesNotExist:
        return JsonResponse({'error': 'Aircraft not found'}, status=404)
    except ValueError as e:
        return JsonResponse({'error': f'Invalid data: {str(e)}'}, status=400)
    except Exception as e:
        print(f"Error creating booking: {e}")
        return JsonResponse({'error': f'Server error: {str(e)}'}, status=500)


@login_required
@require_POST
@transaction.atomic
def edit_booking(request, booking_id):
    """Full edit from the modal: member, aircraft, instructor, time, duration, description."""
    booking = get_object_or_404(Booking, id=booking_id)
    club = booking.club
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return JsonResponse({'error': 'Not authorized'}, status=403)
    if not (actor.is_admin or actor.is_instructor):
        return JsonResponse({'error': 'Only instructors and admins can edit bookings'}, status=403)

    try:
        config = get_config(club)
        aircraft_id = request.POST.get('aircraft_id')
        start_time = request.POST.get('start_time')
        duration = int(request.POST.get('duration') or config.default_booking_duration)
        instructor_id = request.POST.get('instructor_id')
        member_id = request.POST.get('member_id')
        description = request.POST.get('description', '')

        aircraft = Aircraft.objects.get(id=aircraft_id, club=club) if aircraft_id else booking.aircraft
        start_dt = _aware(datetime.fromisoformat(start_time)) if start_time else booking.scheduled_start
        end_dt = start_dt + timedelta(minutes=duration)

        # Conflicts (exclude self, ignore cancelled)
        if Booking.objects.filter(
            club=club, aircraft=aircraft,
            scheduled_start__lt=end_dt, scheduled_end__gt=start_dt,
        ).exclude(id=booking.id).exclude(status='cancelled').exists():
            return JsonResponse({'error': 'Aircraft already booked at that time'}, status=409)

        instructor = None
        if instructor_id:
            from .models import User
            instructor = User.objects.filter(id=instructor_id).first()
            if instructor and Booking.objects.filter(
                club=club, instructor=instructor,
                scheduled_start__lt=end_dt, scheduled_end__gt=start_dt,
            ).exclude(id=booking.id).exclude(status='cancelled').exists():
                return JsonResponse({'error': 'Instructor already booked at that time'}, status=409)

        if member_id:
            from .models import User
            tu = User.objects.filter(id=member_id).first()
            if tu:
                booking.member = ClubMember.objects.filter(user=tu, club=club).first() or booking.member

        # Block-out check (scope-aware)
        override = request.POST.get('override') in ('1', 'true', 'on')
        blocked, msg, hits, is_soft = _blockout_check(club, aircraft, instructor, start_dt, end_dt,
                                                      actor, override, exclude_booking_id=booking.id)
        if blocked:
            can_override = is_soft or bool(actor.is_admin or actor.is_instructor)
            return JsonResponse({'error': msg, 'blockout': True,
                                 'can_override': can_override, 'soft': is_soft}, status=409)

        flight_type_id = request.POST.get('flight_type_id')
        if flight_type_id:
            ft = FlightType.objects.filter(club=club, id=flight_type_id).first()
            if ft:
                booking.flight_type = ft

        # Solo flight types must not carry an instructor
        if booking.flight_type and booking.flight_type.is_solo:
            instructor = None

        booking.aircraft = aircraft
        booking.instructor = instructor
        booking.scheduled_start = start_dt
        booking.scheduled_end = end_dt
        booking.description = description
        booking.blockout_override = bool(hits and override)
        booking.save()

        from .models import recompute_blockout_conflict
        recompute_blockout_conflict(booking)

        _audit(booking, request.user, 'field_changed', notes='Booking edited')
        if hits and override:
            _audit(booking, request.user, 'warning_acknowledged',
                   notes=f"Staff override of block-out on edit: {msg}")

        return JsonResponse({'success': True})

    except Aircraft.DoesNotExist:
        return JsonResponse({'error': 'Aircraft not found'}, status=404)
    except ValueError as e:
        return JsonResponse({'error': f'Invalid data: {str(e)}'}, status=400)


@login_required
def availability_search(request, club_slug):
    club = get_object_or_404(Club, slug=club_slug)
    try:
        club_member = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    
    config = get_config(club)
    instructors = ClubMember.objects.filter(club=club, role__name__iexact='instructor').select_related('user')
    aircraft_list = Aircraft.objects.filter(club=club, status='online')
    aircraft_types = sorted(set(a.aircraft_type for a in aircraft_list))
    
    results = []
    search_performed = False
    filters_applied = {}
    result_count = 0
    
    if request.method == 'POST' or request.GET.get('search'):
        search_performed = True
        range_type = request.POST.get('range_type') or request.GET.get('range_type', 'this_week')
        aircraft_filter = request.POST.get('aircraft') or request.GET.get('aircraft', '')
        aircraft_type_filter = request.POST.get('aircraft_type') or request.GET.get('aircraft_type', '')
        instructor_filter = request.POST.get('instructor') or request.GET.get('instructor', '')
        booking_kind = request.POST.get('booking_kind') or request.GET.get('booking_kind', 'dual')
        duration = int(request.POST.get('duration') or request.GET.get('duration') or config.default_booking_duration)

        # --- Reconcile aircraft type vs specific aircraft (specific wins) ---
        specific_aircraft = Aircraft.objects.filter(club=club, id=aircraft_filter).first() if aircraft_filter else None
        if specific_aircraft:
            # A specific tail implies its type; ignore any conflicting type filter.
            aircraft_type_filter = specific_aircraft.aircraft_type
        elif aircraft_type_filter:
            # Type chosen but no specific aircraft: leave as type-only filter.
            pass

        # --- Solo vs dual is the master switch ---
        is_solo = (booking_kind == 'solo')

        specific_instructor = None
        if not is_solo and instructor_filter and instructor_filter not in ('any', 'none', ''):
            from .models import User
            specific_instructor = User.objects.filter(id=instructor_filter).first()

        filters_applied = {
            'range_type': range_type,
            'aircraft': aircraft_filter,
            'aircraft_type': aircraft_type_filter,
            'instructor': instructor_filter,
            'booking_kind': booking_kind,
            'duration': duration,
        }

        date_start, date_end = get_date_range(range_type)

        typ_start = config.typical_hours_start
        typ_end = config.typical_hours_end

        def mark_span(s, st_dt, en_dt):
            s['start_label'] = st_dt.strftime('%H:%M')
            s['end_label'] = en_dt.strftime('%H:%M')
            s['atypical'] = (st_dt.time() < typ_start) or (en_dt.time() > typ_end)
            s['start_iso'] = s['start'].isoformat()

        by_day = {}

        if is_solo:
            # Aircraft-only spans; no instructor.
            from .availability import find_free_spans
            raw = find_free_spans(
                club=club, date_start=date_start, date_end=date_end,
                aircraft=specific_aircraft, aircraft_type=aircraft_type_filter or None,
                min_minutes=config.time_slot_interval,
            )
            for entry in raw:
                d = entry['date']; ac = entry['aircraft']
                for s in entry['spans']:
                    st = timezone.localtime(s['start']); en = timezone.localtime(s['end'])
                    mark_span(s, st, en)
                    s['aircraft_id'] = ac.id
                    s['instructor_id'] = ''  # solo
                by_day.setdefault(d, []).append({
                    'aircraft': ac,
                    'instructor_rows': [{'instructor': None, 'instructor_name': 'Solo (no instructor)', 'spans': entry['spans']}],
                })
        else:
            # Dual: aircraft AND instructor both free.
            from .availability import find_free_spans_with_instructors
            raw = find_free_spans_with_instructors(
                club=club, date_start=date_start, date_end=date_end,
                aircraft=specific_aircraft, aircraft_type=aircraft_type_filter or None,
                instructor=specific_instructor, min_minutes=config.time_slot_interval,
            )
            for entry in raw:
                d = entry['date']; ac = entry['aircraft']
                instr_rows = []
                for ir in entry['instructor_rows']:
                    instr = ir['instructor']
                    for s in ir['spans']:
                        st = timezone.localtime(s['start']); en = timezone.localtime(s['end'])
                        mark_span(s, st, en)
                        s['aircraft_id'] = ac.id
                        s['instructor_id'] = instr.id
                    instr_rows.append({
                        'instructor': instr,
                        'instructor_name': f"{instr.first_name} {instr.last_name}".strip() or instr.username,
                        'spans': ir['spans'],
                    })
                by_day.setdefault(d, []).append({'aircraft': ac, 'instructor_rows': instr_rows})

        results = []
        for d in sorted(by_day.keys()):
            results.append({
                'date': d,
                'weekday': d.strftime('%A'),
                'date_label': d.strftime('%a, %-d %b'),
                'is_weekend': d.weekday() >= 5,
                'rows': by_day[d],
                'year': d.year, 'month': d.month, 'day': d.day,
            })

        result_count = sum(
            len(ir['spans'])
            for day in results for r in day['rows'] for ir in r['instructor_rows']
        )
    
    # Aircraft -> type map for the live JS reconciliation
    import json as _json
    aircraft_type_map = _json.dumps({str(a.id): a.aircraft_type for a in aircraft_list})

    context = {
        'club': club,
        'club_member': club_member,
        'is_instructor': club_member.is_instructor,
        'instructors': instructors,
        'aircraft_list': aircraft_list,
        'aircraft_types': aircraft_types,
        'aircraft_type_map': aircraft_type_map,
        'duration_choices': config.duration_choices(),
        'default_duration': config.default_booking_duration,
        'results': results,
        'search_performed': search_performed,
        'filters_applied': filters_applied,
        'result_count': result_count,
    }
    
    return render(request, 'core/availability_search.html', context)


@login_required
def reschedule_options(request, booking_id):
    """Get available alternatives for rescheduling a booking."""
    booking = get_object_or_404(Booking, id=booking_id)
    club = booking.club
    
    try:
        club_member = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return JsonResponse({'error': 'Not authorized'}, status=403)
    
    if not (club_member.is_admin or club_member.is_instructor):
        return JsonResponse({'error': 'Only instructors and admins can reschedule'}, status=403)
    
    # Instructors busy during this booking's window
    busy_instructors = Booking.objects.filter(
        club=club,
        instructor__isnull=False,
        scheduled_start__lt=booking.scheduled_end,
        scheduled_end__gt=booking.scheduled_start
    ).exclude(id=booking.id).values_list('instructor_id', flat=True)
    
    available_instructors = ClubMember.objects.filter(
        club=club,
        role__name__iexact='instructor',
    ).exclude(user_id__in=busy_instructors).select_related('user')
    
    # Get available aircraft of same type
    busy_aircraft = Booking.objects.filter(
        club=club,
        scheduled_start__lt=booking.scheduled_end,
        scheduled_end__gt=booking.scheduled_start
    ).exclude(id=booking.id).values_list('aircraft_id', flat=True)
    
    available_aircraft = Aircraft.objects.filter(
        club=club,
        aircraft_type=booking.aircraft.aircraft_type,
        status='online'
    ).exclude(id__in=busy_aircraft)
    
    instructors_list = [
        {'id': i.user.id, 'name': f"{i.user.first_name} {i.user.last_name}"}
        for i in available_instructors
    ]
    
    aircraft_list = [
        {'id': a.id, 'registration': a.registration}
        for a in available_aircraft
    ]
    
    return JsonResponse({
        'instructors': instructors_list,
        'aircraft': aircraft_list,
        'current_instructor_id': booking.instructor_id if booking.instructor else None,
        'current_aircraft_id': booking.aircraft_id,
    })


@login_required
@require_POST
def update_booking(request, booking_id):
    """Update booking with new instructor or aircraft."""
    booking = get_object_or_404(Booking, id=booking_id)
    club = booking.club
    
    try:
        club_member = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return JsonResponse({'error': 'Not authorized'}, status=403)
    
    if not (club_member.is_admin or club_member.is_instructor):
        return JsonResponse({'error': 'Only instructors and admins can update'}, status=403)
    
    instructor_id = request.POST.get('instructor_id')
    aircraft_id = request.POST.get('aircraft_id')
    
    if instructor_id:
        from .models import User
        booking.instructor = User.objects.filter(id=instructor_id).first()
    
    if aircraft_id:
        new_aircraft = Aircraft.objects.filter(id=aircraft_id, club=club).first()
        if new_aircraft:
            booking.aircraft = new_aircraft
    
    booking.save()
    return JsonResponse({'success': True})


@login_required
def club_settings(request, club_slug):
    """Admin-only page to configure theme colours and booking defaults."""
    club = get_object_or_404(Club, slug=club_slug)
    try:
        member = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not member.is_admin:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    config = get_config(club)
    saved = False

    ft_error = None
    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'upload_logo':
            if request.FILES.get('logo'):
                config.logo = request.FILES['logo']
                config.save(update_fields=['logo'])
            elif request.POST.get('remove_logo'):
                config.logo.delete(save=True)
            from django.shortcuts import redirect as _redirect
            return _redirect('core:club_settings', club_slug=club_slug)

        elif action == 'add_flight_type':
            ft_name = request.POST.get('ft_name', '').strip()
            ft_is_solo = request.POST.get('ft_is_solo') == 'on'
            ft_is_training = request.POST.get('ft_is_training') == 'on'
            if ft_name:
                from django.utils.text import slugify
                code = slugify(ft_name).replace('-', '_')[:20]
                if FlightType.objects.filter(club=club, code=code).exists():
                    code = code[:18] + '_2'
                FlightType.objects.create(
                    club=club, name=ft_name, code=code,
                    is_solo=ft_is_solo, is_training=ft_is_training,
                )
            else:
                ft_error = "Name is required."

        elif action == 'set_flight_type_solo':
            ft_id = request.POST.get('ft_id')
            ft_is_solo = request.POST.get('ft_is_solo') == '1'
            ft = FlightType.objects.filter(club=club, id=ft_id).first()
            if ft:
                ft.is_solo = ft_is_solo
                ft.save(update_fields=['is_solo'])
            from django.shortcuts import redirect as _redirect
            return _redirect('core:club_settings', club_slug=club_slug)

        elif action == 'delete_flight_type':
            ft_id = request.POST.get('ft_id')
            ft = FlightType.objects.filter(club=club, id=ft_id).first()
            if ft:
                if Booking.objects.filter(club=club, flight_type=ft).exists():
                    ft_error = f"Cannot delete '{ft.name}' — it has existing bookings."
                else:
                    ft.delete()

        # ── BlockOutType management ──────────────────────────────────────
        elif action == 'add_blockout_type':
            bot_name = request.POST.get('bot_name', '').strip()
            bot_color = request.POST.get('bot_color', '#9aa3ad').strip()
            bot_target = request.POST.get('bot_target', 'instructor')
            bot_is_hard = request.POST.get('bot_is_hard') != '0'
            if bot_name:
                BlockOutType.objects.create(club=club, name=bot_name, color=bot_color,
                                            target=bot_target, is_hard=bot_is_hard)
            return redirect('core:club_settings', club_slug=club_slug)

        elif action == 'set_blockout_type_hard':
            bot_id = request.POST.get('bot_id')
            is_hard = request.POST.get('is_hard') == '1'
            bt = BlockOutType.objects.filter(club=club, id=bot_id).first()
            if bt:
                bt.is_hard = is_hard
                bt.save(update_fields=['is_hard'])
            return redirect('core:club_settings', club_slug=club_slug)

        elif action == 'delete_blockout_type':
            bot_id = request.POST.get('bot_id')
            bt = BlockOutType.objects.filter(club=club, id=bot_id).first()
            if bt:
                bt.delete()
            return redirect('core:club_settings', club_slug=club_slug)

        # ── Instructor grade management ──────────────────────────────────────
        elif action == 'add_instructor_grade':
            ig_name = request.POST.get('ig_name', '').strip()
            ig_rate = request.POST.get('ig_rate', '').strip()
            ig_order = request.POST.get('ig_order', '0').strip()
            if ig_name and ig_rate:
                try:
                    InstructorGrade.objects.get_or_create(
                        club=club, name=ig_name,
                        defaults={'hourly_rate': ig_rate,
                                  'display_order': int(ig_order) if ig_order.isdigit() else 0}
                    )
                except Exception:
                    pass
            return redirect('core:club_settings', club_slug=club_slug)

        elif action == 'delete_instructor_grade':
            InstructorGrade.objects.filter(club=club, id=request.POST.get('ig_id')).delete()
            return redirect('core:club_settings', club_slug=club_slug)

        elif action == 'edit_instructor_grade':
            ig = InstructorGrade.objects.filter(club=club, id=request.POST.get('ig_id')).first()
            if ig:
                ig.hourly_rate = request.POST.get('ig_rate', ig.hourly_rate)
                ig.save(update_fields=['hourly_rate'])
            return redirect('core:club_settings', club_slug=club_slug)

        # ── Aircraft surcharge type management ───────────────────────────────
        elif action == 'add_surcharge_type':
            st_name = request.POST.get('st_name', '').strip()
            st_amount = request.POST.get('st_amount', '').strip()
            st_desc = request.POST.get('st_desc', '').strip()
            if st_name and st_amount:
                try:
                    AircraftSurchargeType.objects.get_or_create(
                        club=club, name=st_name,
                        defaults={'amount': st_amount, 'description': st_desc}
                    )
                except Exception:
                    pass
            return redirect('core:club_settings', club_slug=club_slug)

        elif action == 'delete_surcharge_type':
            AircraftSurchargeType.objects.filter(club=club, id=request.POST.get('st_id')).delete()
            return redirect('core:club_settings', club_slug=club_slug)

        elif action == 'edit_surcharge_type':
            st = AircraftSurchargeType.objects.filter(club=club, id=request.POST.get('st_id')).first()
            if st:
                st.amount = request.POST.get('st_amount', st.amount)
                st.save(update_fields=['amount'])
            return redirect('core:club_settings', club_slug=club_slug)

        else:
            club_name = request.POST.get('club_name', '').strip()
            if club_name:
                club.name = club_name
                club.save(update_fields=['name'])
            for field in ['theme_banner', 'theme_primary', 'theme_accent',
                          'theme_confirmed', 'theme_pending', 'theme_weekend', 'theme_atypical']:
                val = request.POST.get(field, '').strip()
                if val:
                    setattr(config, field, val)
            dd = request.POST.get('default_booking_duration')
            if dd and dd.isdigit():
                config.default_booking_duration = int(dd)
            do = request.POST.get('duration_options', '').strip()
            if do:
                config.duration_options = do
            tsi = request.POST.get('time_slot_interval')
            if tsi and tsi.isdigit():
                config.time_slot_interval = int(tsi)
            oh_start = request.POST.get('operating_hours_start')
            oh_end = request.POST.get('operating_hours_end')
            if oh_start:
                config.operating_hours_start = oh_start
            if oh_end:
                config.operating_hours_end = oh_end
            typ_start = request.POST.get('typical_hours_start')
            typ_end = request.POST.get('typical_hours_end')
            if typ_start:
                config.typical_hours_start = typ_start
            if typ_end:
                config.typical_hours_end = typ_end
            config.save()
            saved = True

    color_fields = [
        ('theme_banner', 'Banner', config.theme_banner),
        ('theme_primary', 'Primary (buttons, links)', config.theme_primary),
        ('theme_accent', 'Accent', config.theme_accent),
        ('theme_confirmed', 'Confirmed booking', config.theme_confirmed),
        ('theme_pending', 'Pending booking', config.theme_pending),
        ('theme_weekend', 'Weekend shade', config.theme_weekend),
        ('theme_atypical', 'Outside typical hours', config.theme_atypical),
    ]

    return render(request, 'core/club_settings.html', {
        'club': club,
        'config': config,
        'color_fields': color_fields,
        'all_blockout_types': BlockOutType.objects.filter(club=club, target='all'),
        'instructor_blockout_types': BlockOutType.objects.filter(club=club, target='instructor'),
        'aircraft_blockout_types': BlockOutType.objects.filter(club=club, target='aircraft'),
        'flight_types': FlightType.objects.filter(club=club),
        'instructor_grades': InstructorGrade.objects.filter(club=club),
        'surcharge_types': AircraftSurchargeType.objects.filter(club=club),
        'saved': saved,
        'ft_error': ft_error,
    })


@login_required
def manage_bookings(request, club_slug):
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not (actor.is_admin or actor.is_instructor):
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    from datetime import date as _date
    from django.db.models import Q
    today = _date.today()

    _conflict_q = (
        Q(blockout_conflict=True) |
        Q(member__standing__in=['suspended', 'lapsed', 'resigned']) |
        Q(member__standing='active', member__subscription_expires__lt=today) |
        Q(aircraft__status='retired')
    )

    if request.method == 'POST':
        action = request.POST.get('action', '')
        ids = [int(i) for i in request.POST.getlist('booking_ids') if i.isdigit()]
        qs = Booking.objects.filter(club=club, id__in=ids).exclude(status='cancelled')
        if action == 'confirm':
            qs.filter(status='pending').update(
                status='confirmed', confirmed_by=request.user, confirmed_at=timezone.now()
            )
        elif action == 'cancel':
            qs.update(status='cancelled')
        elif action == 'move_aircraft':
            ac = Aircraft.objects.filter(club=club, id=request.POST.get('target_aircraft_id'), status='online').first()
            if ac:
                qs.update(aircraft=ac)
        elif action == 'move_instructor':
            from .models import User as _User
            instr = _User.objects.filter(id=request.POST.get('target_instructor_id')).first()
            if instr:
                qs.update(instructor=instr)
        return redirect(request.get_full_path())

    view = request.GET.get('view', 'conflicts')
    f_aircraft = request.GET.get('aircraft', '')
    f_instructor = request.GET.get('instructor', '')
    f_member = request.GET.get('member', '')
    f_status = request.GET.get('status', '')
    f_date_from = request.GET.get('date_from', '')
    f_date_to = request.GET.get('date_to', '')

    qs = (Booking.objects
          .filter(club=club)
          .exclude(status='cancelled')
          .select_related('member__user', 'aircraft', 'instructor', 'flight_type')
          .order_by('scheduled_start'))

    if view == 'conflicts':
        qs = qs.filter(_conflict_q)
    if f_aircraft:
        qs = qs.filter(aircraft_id=f_aircraft)
    if f_instructor:
        qs = qs.filter(instructor_id=f_instructor)
    if f_member:
        qs = qs.filter(member__user_id=f_member)
    if f_status:
        qs = qs.filter(status=f_status)
    if f_date_from:
        try:
            qs = qs.filter(scheduled_start__date__gte=f_date_from)
        except Exception:
            pass
    if f_date_to:
        try:
            qs = qs.filter(scheduled_start__date__lte=f_date_to)
        except Exception:
            pass

    def conflict_reasons(b):
        r = []
        if b.blockout_conflict:
            r.append(b.blockout_conflict_reason or 'Block-out conflict')
        if b.member:
            if b.member.standing in ('suspended', 'lapsed', 'resigned'):
                r.append(f'Member {b.member.get_standing_display()}')
            elif b.member.standing == 'active' and b.member.subscription_expires and b.member.subscription_expires < today:
                r.append('Subscription expired')
        if b.aircraft and b.aircraft.status == 'retired':
            r.append('Aircraft retired')
        return r

    bookings_data = [
        {'b': b, 'reasons': conflict_reasons(b)}
        for b in qs
    ]

    aircraft_list = Aircraft.objects.filter(club=club, status='online').order_by('registration')
    instructors = ClubMember.objects.filter(club=club, role__name__iexact='instructor').select_related('user')
    members_qs = ClubMember.objects.filter(club=club).select_related('user').order_by('user__last_name')
    conflict_count = Booking.objects.filter(club=club).exclude(status='cancelled').filter(_conflict_q).count()

    return render(request, 'core/manage_bookings.html', {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'bookings_data': bookings_data, 'view': view, 'conflict_count': conflict_count,
        'f_aircraft': f_aircraft, 'f_instructor': f_instructor, 'f_member': f_member,
        'f_status': f_status, 'f_date_from': f_date_from, 'f_date_to': f_date_to,
        'aircraft_list': aircraft_list, 'instructors': instructors, 'members_qs': members_qs,
    })


@login_required
def manage_member_detail(request, club_slug, member_id):
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not (actor.is_admin or actor.is_instructor):
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    member = get_object_or_404(ClubMember, club=club, id=member_id)

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'avatar_upload':
            if request.FILES.get('avatar'):
                member.avatar = request.FILES['avatar']
                member.save(update_fields=['avatar'])
            return redirect('core:manage_member_detail', club_slug=club_slug, member_id=member_id)
        elif action == 'save_contact':
            u = member.user
            u.first_name = request.POST.get('first_name', '').strip()
            u.last_name = request.POST.get('last_name', '').strip()
            u.email = request.POST.get('email', '').strip()
            u.save(update_fields=['first_name', 'last_name', 'email'])
            member.phone_mobile = request.POST.get('phone_mobile', '').strip()
            member.phone_home = request.POST.get('phone_home', '').strip()
            member.phone_work = request.POST.get('phone_work', '').strip()
            member.address_line1 = request.POST.get('address_line1', '').strip()
            member.address_line2 = request.POST.get('address_line2', '').strip()
            member.suburb = request.POST.get('suburb', '').strip()
            member.postcode = request.POST.get('postcode', '').strip()
            member.save()
        elif action == 'save_membership' and actor.is_admin:
            standing = request.POST.get('standing')
            if standing in dict(ClubMember.STANDING_CHOICES):
                member.standing = standing
            sub_exp = request.POST.get('subscription_expires')
            member.subscription_expires = sub_exp or None
            role_id = request.POST.get('role_id')
            member.role = Role.objects.filter(club=club, id=role_id).first() if role_id else None
            member.has_admin_access = request.POST.get('has_admin_access') == 'on'
            member.save()
        elif action == 'save_notes' and actor.is_admin:
            pass  # notes field not yet on model — placeholder
        return redirect('core:manage_member_detail', club_slug=club_slug, member_id=member_id)

    from .models import MemberCredential
    from datetime import datetime as _dt, time as _time, date as _date
    _today_start = timezone.make_aware(_dt.combine(_date.today(), _time.min))
    upcoming_bookings = (Booking.objects
                         .filter(club=club, member=member, scheduled_start__gte=_today_start)
                         .exclude(status='cancelled')
                         .select_related('aircraft', 'instructor', 'flight_type')
                         .order_by('scheduled_start')[:10])
    past_bookings = (Booking.objects
                     .filter(club=club, member=member, scheduled_start__lt=_today_start)
                     .exclude(status='cancelled')
                     .select_related('aircraft', 'instructor', 'flight_type')
                     .order_by('-scheduled_start')[:20])
    credentials = MemberCredential.objects.filter(club_member=member).order_by('expiry_date')
    roles = Role.objects.filter(club=club)

    try:
        account = member.account
    except Exception:
        account = None

    return render(request, 'core/manage_member_detail.html', {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'member': member, 'upcoming_bookings': upcoming_bookings, 'past_bookings': past_bookings,
        'credentials': credentials, 'account': account, 'roles': roles,
        'standing_choices': ClubMember.STANDING_CHOICES,
    })


@login_required
def my_profile(request, club_slug):
    club = get_object_or_404(Club, slug=club_slug)
    try:
        member = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')

    if request.method == 'POST':
        action = request.POST.get('action', 'save_contact')
        if action == 'avatar_upload':
            if request.FILES.get('avatar'):
                member.avatar = request.FILES['avatar']
                member.save(update_fields=['avatar'])
            return redirect('core:my_profile', club_slug=club_slug)
        elif action == 'save_notifications':
            from .models import NotificationPreference
            pref, _ = NotificationPreference.objects.get_or_create(club_member=member)
            pref.aircraft.set(request.POST.getlist('notify_aircraft'))
            pref.instructors.set(request.POST.getlist('notify_instructors'))
            raw_days = request.POST.get('max_days_ahead', '').strip()
            pref.max_days_ahead = int(raw_days) if raw_days.isdigit() else None
            pref.save()
            return redirect('core:my_profile', club_slug=club_slug)
        elif action == 'delete_notifications':
            from .models import NotificationPreference
            NotificationPreference.objects.filter(club_member=member).delete()
            return redirect('core:my_profile', club_slug=club_slug)
        else:
            u = request.user
            u.first_name = request.POST.get('first_name', '').strip()
            u.last_name = request.POST.get('last_name', '').strip()
            u.email = request.POST.get('email', '').strip()
            u.save(update_fields=['first_name', 'last_name', 'email'])
            member.phone_mobile = request.POST.get('phone_mobile', '').strip()
            member.phone_home = request.POST.get('phone_home', '').strip()
            member.address_line1 = request.POST.get('address_line1', '').strip()
            member.suburb = request.POST.get('suburb', '').strip()
            member.postcode = request.POST.get('postcode', '').strip()
            member.save()
            return redirect('core:my_profile', club_slug=club_slug)

    from datetime import datetime as _dt, time as _time, date as _date
    _today_start = timezone.make_aware(_dt.combine(_date.today(), _time.min))
    upcoming = (Booking.objects
                .filter(club=club, member=member, scheduled_start__gte=_today_start)
                .exclude(status='cancelled')
                .select_related('aircraft', 'instructor', 'flight_type')
                .order_by('scheduled_start')[:10])
    past = (Booking.objects
            .filter(club=club, member=member, scheduled_start__lt=_today_start)
            .exclude(status='cancelled')
            .select_related('aircraft', 'instructor', 'flight_type')
            .order_by('-scheduled_start')[:20])

    try:
        account = member.account
    except Exception:
        account = None

    from .models import NotificationPreference
    try:
        notification_pref = member.notification_prefs
    except NotificationPreference.DoesNotExist:
        notification_pref = None

    club_aircraft = Aircraft.objects.filter(club=club, status='online').order_by('registration')
    club_instructors = ClubMember.objects.filter(
        club=club, role__name__iexact='instructor'
    ).select_related('user').order_by('user__last_name')

    return render(request, 'core/my_profile.html', {
        'club': club, 'club_member': member, 'is_instructor': member.is_instructor,
        'member': member, 'upcoming': upcoming, 'past': past, 'account': account,
        'notification_pref': notification_pref,
        'club_aircraft': club_aircraft,
        'club_instructors': club_instructors,
    })


def _create_blockout_from_post(request, club, scope, aircraft=None, instructor_user=None):
    """Create a BlockOut from POST data. scope, aircraft/instructor_user pre-determined by caller."""
    from .models import BlockOut, BlockOutType
    bot_id = request.POST.get('bot_id')
    blockout_type = BlockOutType.objects.filter(club=club, id=bot_id).first() if bot_id else None
    label = request.POST.get('label', '').strip()
    recurrence = request.POST.get('recurrence', 'one_off')
    all_day = request.POST.get('all_day') in ('on', '1', 'true')
    date_str = request.POST.get('date', '')
    weekday_str = request.POST.get('weekday', '0')

    bo = BlockOut(club=club, blockout_type=blockout_type, label=label,
                  scope=scope, recurrence=recurrence, all_day=all_day,
                  created_by=request.user)
    if recurrence == 'one_off':
        from datetime import date as _date
        try:
            bo.date = _date.fromisoformat(date_str) if date_str else None
        except ValueError:
            bo.date = None
    elif recurrence == 'weekly':
        bo.weekday = int(weekday_str) if weekday_str.isdigit() else 0

    if not all_day:
        bo.start_time = request.POST.get('start_time') or None
        bo.end_time = request.POST.get('end_time') or None

    bo.save()

    if scope == 'aircraft' and aircraft:
        bo.aircraft.set([aircraft])
    elif scope == 'instructors' and instructor_user:
        bo.instructors.set([instructor_user])


@login_required
def manage_blockouts(request, club_slug):
    """All-resource block-outs (scope='all') and overview."""
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not (actor.is_admin or actor.is_instructor):
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    from .models import BlockOut, BlockOutType
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'add_blockout':
            _create_blockout_from_post(request, club, scope='all')
        elif action == 'delete_blockout':
            bo_id = request.POST.get('bo_id')
            BlockOut.objects.filter(club=club, id=bo_id).delete()
        return redirect('core:manage_blockouts', club_slug=club_slug)

    from django.db.models import Q
    from datetime import date as _date
    _today = _date.today()
    _active_q = (
        Q(recurrence='one_off', date__gte=_today) |
        Q(recurrence__in=['weekly', 'daily'], active_until__isnull=True) |
        Q(recurrence__in=['weekly', 'daily'], active_until__gte=_today)
    )
    all_blockouts = (BlockOut.objects
                     .filter(club=club, scope='all')
                     .filter(_active_q)
                     .select_related('blockout_type')
                     .order_by('recurrence', 'date', 'weekday', 'start_time'))
    past_count = BlockOut.objects.filter(club=club, scope='all').exclude(_active_q).count()
    blockout_types = BlockOutType.objects.filter(club=club, target='all')
    return render(request, 'core/manage_blockouts.html', {
        'club': club,
        'club_member': actor,
        'is_instructor': actor.is_instructor,
        'blockouts': all_blockouts,
        'past_count': past_count,
        'blockout_types': blockout_types,
    })


@login_required
def manage_members(request, club_slug):
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not actor.is_admin:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    if request.method == 'POST':
        action = request.POST.get('action', '')
        cm_id = request.POST.get('cm_id')
        cm = ClubMember.objects.filter(club=club, id=cm_id).first() if cm_id else None
        if cm:
            if action == 'set_standing':
                standing = request.POST.get('standing')
                if standing in dict(ClubMember.STANDING_CHOICES):
                    cm.standing = standing
                    cm.save(update_fields=['standing'])
            elif action == 'set_role':
                role_id = request.POST.get('role_id')
                role = Role.objects.filter(club=club, id=role_id).first() if role_id else None
                cm.role = role
                cm.save(update_fields=['role'])
        return redirect('core:manage_members', club_slug=club_slug)

    q = request.GET.get('q', '').strip()
    members = (ClubMember.objects
               .filter(club=club)
               .select_related('user', 'role', 'membership_category')
               .order_by('user__last_name', 'user__first_name'))
    if q:
        from django.db.models import Q as _Q
        members = members.filter(
            _Q(user__first_name__icontains=q) |
            _Q(user__last_name__icontains=q) |
            _Q(user__email__icontains=q)
        )
    roles = Role.objects.filter(club=club)
    return render(request, 'core/manage_members.html', {
        'club': club,
        'club_member': actor,
        'is_instructor': actor.is_instructor,
        'members': members,
        'roles': roles,
        'standing_choices': ClubMember.STANDING_CHOICES,
        'q': q,
    })


@login_required
def manage_aircraft(request, club_slug):
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not (actor.is_admin or actor.is_instructor):
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    from .models import AircraftStatus, BlockOut, BlockOutType
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'set_status' and actor.is_admin:
            ac_id = request.POST.get('ac_id')
            status = request.POST.get('status')
            ac = Aircraft.objects.filter(club=club, id=ac_id).first()
            if ac and status in [s.value for s in AircraftStatus]:
                ac.status = status
                ac.save(update_fields=['status'])
        elif action == 'add_aircraft' and actor.is_admin:
            reg = request.POST.get('registration', '').strip().upper()
            ac_type = request.POST.get('aircraft_type', '').strip()
            if reg and ac_type:
                Aircraft.objects.get_or_create(
                    club=club, registration=reg,
                    defaults={'aircraft_type': ac_type}
                )
        elif action == 'add_aircraft_blockout':
            ac_id = request.POST.get('ac_id')
            ac = Aircraft.objects.filter(club=club, id=ac_id).first()
            if ac:
                _create_blockout_from_post(request, club, scope='aircraft', aircraft=ac)
        elif action == 'delete_blockout':
            bo_id = request.POST.get('bo_id')
            BlockOut.objects.filter(club=club, id=bo_id).delete()
        return redirect('core:manage_aircraft', club_slug=club_slug)

    aircraft_list = Aircraft.objects.filter(club=club).order_by('registration')
    # Attach aircraft-scoped block-outs to each aircraft (active/upcoming only)
    from .models import BlockOut
    from django.db.models import Q
    from datetime import date as _date
    _today = _date.today()
    _active_q = (
        Q(recurrence='one_off', date__gte=_today) |
        Q(recurrence__in=['weekly', 'daily'], active_until__isnull=True) |
        Q(recurrence__in=['weekly', 'daily'], active_until__gte=_today)
    )
    ac_blockouts_qs = (BlockOut.objects
                       .filter(club=club, scope='aircraft')
                       .filter(_active_q)
                       .prefetch_related('aircraft', 'blockout_type')
                       .order_by('recurrence', 'date', 'weekday', 'start_time'))
    bo_by_ac = {}
    for bo in ac_blockouts_qs:
        for a in bo.aircraft.all():
            bo_by_ac.setdefault(a.id, []).append(bo)

    past_ac_bos = (BlockOut.objects
                   .filter(club=club, scope='aircraft')
                   .exclude(_active_q)
                   .prefetch_related('aircraft'))
    past_bo_count_by_ac = {}
    for bo in past_ac_bos:
        for a in bo.aircraft.all():
            past_bo_count_by_ac[a.id] = past_bo_count_by_ac.get(a.id, 0) + 1

    for ac in aircraft_list:
        ac.bo_list = bo_by_ac.get(ac.id, [])
        ac.past_bo_count = past_bo_count_by_ac.get(ac.id, 0)

    aircraft_blockout_types = BlockOutType.objects.filter(club=club, target='aircraft')
    return render(request, 'core/manage_aircraft.html', {
        'club': club,
        'club_member': actor,
        'is_instructor': actor.is_instructor,
        'aircraft_list': aircraft_list,
        'status_choices': AircraftStatus.choices,
        'aircraft_blockout_types': aircraft_blockout_types,
    })


@login_required
def manage_instructors(request, club_slug):
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not (actor.is_admin or actor.is_instructor):
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    from .models import InstructorAvailability, BlockOut, BlockOutType
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'add_instructor_availability':
            cm_id = request.POST.get('av_member_id')
            cm = ClubMember.objects.filter(club=club, id=cm_id).first()
            if cm:
                recurrence = request.POST.get('av_recurrence', 'weekly')
                all_day = request.POST.get('av_all_day') == 'on'
                av = InstructorAvailability(club_member=cm, recurrence=recurrence, all_day=all_day)
                if recurrence == 'weekly':
                    wd = request.POST.get('av_weekday', '0')
                    av.weekday = int(wd) if wd.isdigit() else 0
                else:
                    av.date = request.POST.get('av_date') or None
                if not all_day:
                    av.start_time = request.POST.get('av_start_time') or None
                    av.end_time = request.POST.get('av_end_time') or None
                av.active_from = request.POST.get('av_active_from') or None
                av.active_until = request.POST.get('av_active_until') or None
                av.notes = request.POST.get('av_notes', '').strip()
                av.save()
        elif action == 'delete_instructor_availability':
            av_id = request.POST.get('av_id')
            InstructorAvailability.objects.filter(club_member__club=club, id=av_id).delete()
        elif action == 'add_instructor_blockout':
            from .models import User as _User
            instr_user_id = request.POST.get('instr_user_id')
            instr_user = _User.objects.filter(id=instr_user_id).first()
            if instr_user:
                _create_blockout_from_post(request, club, scope='instructors', instructor_user=instr_user)
        elif action == 'delete_blockout':
            bo_id = request.POST.get('bo_id')
            BlockOut.objects.filter(club=club, id=bo_id).delete()
        return redirect('core:manage_instructors', club_slug=club_slug)

    instructors = (ClubMember.objects
                   .filter(club=club, role__name__iexact='instructor')
                   .select_related('user')
                   .order_by('user__last_name'))
    from django.db.models import Q
    from datetime import date as _date
    _today = _date.today()
    _active_q = (
        Q(recurrence='one_off', date__gte=_today) |
        Q(recurrence='weekly', active_until__isnull=True) |
        Q(recurrence='weekly', active_until__gte=_today)
    )
    av_by_member = {}
    for av in (InstructorAvailability.objects
               .filter(club_member__club=club)
               .filter(_active_q)
               .select_related('club_member')):
        av_by_member.setdefault(av.club_member_id, []).append(av)

    # Attach instructor-scoped block-outs to each instructor (active/upcoming only)
    _active_q = (
        Q(recurrence='one_off', date__gte=_today) |
        Q(recurrence__in=['weekly', 'daily'], active_until__isnull=True) |
        Q(recurrence__in=['weekly', 'daily'], active_until__gte=_today)
    )
    instr_blockouts_qs = (BlockOut.objects
                          .filter(club=club, scope='instructors')
                          .filter(_active_q)
                          .prefetch_related('instructors', 'blockout_type')
                          .order_by('recurrence', 'date', 'weekday', 'start_time'))
    bo_by_user = {}
    for bo in instr_blockouts_qs:
        for u in bo.instructors.all():
            bo_by_user.setdefault(u.id, []).append(bo)

    # Past counts — availability windows
    _av_active_q = (
        Q(recurrence='one_off', date__gte=_today) |
        Q(recurrence='weekly', active_until__isnull=True) |
        Q(recurrence='weekly', active_until__gte=_today)
    )
    past_av_count_by_member = {}
    for av in InstructorAvailability.objects.filter(club_member__club=club).exclude(_av_active_q):
        past_av_count_by_member[av.club_member_id] = past_av_count_by_member.get(av.club_member_id, 0) + 1

    # Past counts — block-outs
    past_instr_bos = (BlockOut.objects
                      .filter(club=club, scope='instructors')
                      .exclude(_active_q)
                      .prefetch_related('instructors'))
    past_bo_count_by_user = {}
    for bo in past_instr_bos:
        for u in bo.instructors.all():
            past_bo_count_by_user[u.id] = past_bo_count_by_user.get(u.id, 0) + 1

    for instr in instructors:
        instr.av_windows = av_by_member.get(instr.id, [])
        instr.blockouts = bo_by_user.get(instr.user.id, [])
        instr.past_av_count = past_av_count_by_member.get(instr.id, 0)
        instr.past_bo_count = past_bo_count_by_user.get(instr.user.id, 0)

    instructor_blockout_types = BlockOutType.objects.filter(club=club).exclude(target='aircraft')
    return render(request, 'core/manage_instructors.html', {
        'club': club,
        'club_member': actor,
        'is_instructor': actor.is_instructor,
        'instructors': instructors,
        'instructor_blockout_types': instructor_blockout_types,
    })


@login_required
@require_POST
@transaction.atomic
def create_blockout(request):
    """Create a block-out from the calendar UI. Staff/admin only."""
    from .models import BlockOut, User as _User

    actor = ClubMember.objects.filter(user=request.user).first()
    if not actor or not (actor.is_admin or actor.is_instructor):
        return JsonResponse({'error': 'Not authorized'}, status=403)

    club = actor.club

    bot_id = request.POST.get('blockout_type_id')
    scope = request.POST.get('scope', 'all')
    label = request.POST.get('label', '').strip()
    recurrence = request.POST.get('recurrence', 'one_off')
    all_day = request.POST.get('all_day') in ('1', 'true', 'on')
    date_str = request.POST.get('date', '')
    weekday_str = request.POST.get('weekday', '')
    start_time_str = request.POST.get('start_time', '') or None
    end_time_str = request.POST.get('end_time', '') or None
    aircraft_ids = request.POST.getlist('aircraft_ids')
    instructor_ids = request.POST.getlist('instructor_ids')

    blockout_type = BlockOutType.objects.filter(club=club, id=bot_id).first() if bot_id else None

    bo = BlockOut(
        club=club, blockout_type=blockout_type, label=label,
        scope=scope, recurrence=recurrence, all_day=all_day,
        created_by=request.user,
    )

    if recurrence == 'one_off':
        try:
            from datetime import date as _date
            bo.date = _date.fromisoformat(date_str) if date_str else None
        except ValueError:
            return JsonResponse({'error': 'Invalid date'}, status=400)
    elif recurrence == 'weekly':
        bo.weekday = int(weekday_str) if weekday_str.isdigit() else 0

    if not all_day:
        bo.start_time = start_time_str
        bo.end_time = end_time_str

    bo.save()

    if scope == 'aircraft' and aircraft_ids:
        bo.aircraft.set(Aircraft.objects.filter(club=club, id__in=aircraft_ids))
    elif scope == 'instructors' and instructor_ids:
        bo.instructors.set(_User.objects.filter(id__in=instructor_ids))

    return JsonResponse({'success': True, 'id': bo.id})


@login_required
def manage_aerodromes(request, club_slug):
    from .models import AerodromeFeeType
    club = get_object_or_404(Club, slug=club_slug)
    try:
        member = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not member.is_staff:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    error = None
    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'add_aerodrome':
            icao = request.POST.get('icao', '').strip().upper()
            name = request.POST.get('name', '').strip()
            notes = request.POST.get('notes', '').strip()
            if icao and name:
                Aerodrome.objects.get_or_create(
                    club=club, icao_code=icao,
                    defaults={'name': name, 'notes': notes}
                )
            else:
                error = "ICAO code and name are required."

        elif action == 'edit_aerodrome':
            ae = Aerodrome.objects.filter(club=club, id=request.POST.get('ae_id')).first()
            if ae:
                ae.name = request.POST.get('name', ae.name).strip()
                ae.notes = request.POST.get('notes', '').strip()
                ae.save(update_fields=['name', 'notes'])

        elif action == 'toggle_aerodrome':
            ae = Aerodrome.objects.filter(club=club, id=request.POST.get('ae_id')).first()
            if ae:
                ae.is_active = not ae.is_active
                ae.save(update_fields=['is_active'])

        elif action == 'delete_aerodrome':
            Aerodrome.objects.filter(club=club, id=request.POST.get('ae_id')).delete()

        elif action == 'add_fee_type':
            ae = Aerodrome.objects.filter(club=club, id=request.POST.get('ae_id')).first()
            ft_name = request.POST.get('ft_name', '').strip()
            ft_amount = request.POST.get('ft_amount', '0').strip()
            if ae and ft_name:
                AerodromeFeeType.objects.get_or_create(
                    aerodrome=ae, name=ft_name,
                    defaults={'default_amount': ft_amount or 0}
                )

        elif action == 'edit_fee_type':
            ft = AerodromeFeeType.objects.filter(
                aerodrome__club=club, id=request.POST.get('ft_id')
            ).first()
            if ft:
                ft.name = request.POST.get('ft_name', ft.name).strip()
                ft.default_amount = request.POST.get('ft_amount', ft.default_amount)
                ft.save()

        elif action == 'delete_fee_type':
            AerodromeFeeType.objects.filter(
                aerodrome__club=club, id=request.POST.get('ft_id')
            ).delete()

        return redirect('core:manage_aerodromes', club_slug=club_slug)

    aerodromes = Aerodrome.objects.filter(club=club).prefetch_related('fee_types').order_by('icao_code')
    return render(request, 'core/manage_aerodromes.html', {
        'club': club, 'club_member': member, 'aerodromes': aerodromes, 'error': error,
    })


@login_required
def manage_rates(request, club_slug):
    club = get_object_or_404(Club, slug=club_slug)
    try:
        member = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not member.is_staff:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    error = None
    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'add_rate':
            rate = request.POST.get('rate', '').strip()
            effective_from = request.POST.get('effective_from', '').strip()
            notes = request.POST.get('notes', '').strip()
            if rate and effective_from:
                FuelSurchargeRate.objects.get_or_create(
                    club=club, effective_from=effective_from,
                    defaults={'rate': rate, 'notes': notes}
                )
            else:
                error = "Rate and effective date are required."

        elif action == 'delete_rate':
            FuelSurchargeRate.objects.filter(club=club, id=request.POST.get('rate_id')).delete()

        return redirect('core:manage_rates', club_slug=club_slug)

    rates = FuelSurchargeRate.objects.filter(club=club).order_by('-effective_from')
    current = rates.first()
    return render(request, 'core/manage_rates.html', {
        'club': club, 'club_member': member, 'rates': rates, 'current_rate': current, 'error': error,
    })


@login_required
def club_rates(request, club_slug):
    """Admin-only rate card: instructor grade rates, surcharge amounts, hire rates."""
    club = get_object_or_404(Club, slug=club_slug)
    try:
        member = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not member.is_admin:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    from .models import ChargeRate
    saved = False

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'save_grade_rates':
            for ig in InstructorGrade.objects.filter(club=club):
                val = request.POST.get(f'ig_rate_{ig.id}', '').strip()
                if val:
                    try:
                        ig.hourly_rate = val
                        ig.save(update_fields=['hourly_rate'])
                    except Exception:
                        pass
            saved = True

        elif action == 'save_surcharge_amounts':
            for st in AircraftSurchargeType.objects.filter(club=club):
                val = request.POST.get(f'st_amount_{st.id}', '').strip()
                if val:
                    try:
                        st.amount = val
                        st.save(update_fields=['amount'])
                    except Exception:
                        pass
            saved = True

        elif action == 'save_hire_rate':
            aircraft_id = request.POST.get('aircraft_id')
            ft_id = request.POST.get('ft_id')
            time_method = request.POST.get('time_method', 'hobbs')
            amount = request.POST.get('amount', '').strip()
            ac = Aircraft.objects.filter(club=club, id=aircraft_id).first()
            ft = FlightType.objects.filter(club=club, id=ft_id).first()
            if ac and ft and amount:
                ChargeRate.objects.update_or_create(
                    aircraft=ac, flight_type=ft, time_method=time_method,
                    defaults={'club': club, 'amount': amount}
                )
            saved = True

        elif action == 'delete_hire_rate':
            ChargeRate.objects.filter(
                club=club, id=request.POST.get('rate_id')
            ).delete()

        return redirect('core:club_rates', club_slug=club_slug)

    instructor_grades = InstructorGrade.objects.filter(club=club)
    surcharge_types = AircraftSurchargeType.objects.filter(club=club)
    hire_rates = (ChargeRate.objects
                  .filter(club=club)
                  .select_related('aircraft', 'flight_type')
                  .order_by('aircraft__registration', 'flight_type__name'))
    aircraft_list = Aircraft.objects.filter(club=club, status='online').order_by('registration')
    flight_types = FlightType.objects.filter(club=club, is_billable=True)

    return render(request, 'core/club_rates.html', {
        'club': club, 'club_member': member,
        'instructor_grades': instructor_grades,
        'surcharge_types': surcharge_types,
        'hire_rates': hire_rates,
        'aircraft_list': aircraft_list,
        'flight_types': flight_types,
        'saved': saved,
    })
