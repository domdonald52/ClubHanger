from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.db import transaction
from datetime import datetime, timedelta, time, date
from .models import (Club, ClubMember, Booking, Aircraft, AircraftType, Role, FlightType, BlockOutType,
                     SlotWatch, InstructorGrade, AircraftSurchargeType,
                     Aerodrome, FuelSurchargeRate, Invoice, InvoiceLineItem,
                     FlightCompletion, AircraftMaintenanceItem, ChargeRate, FlightChargeItem,
                     FlightLandingEntry, AccountTransaction, ClubConfig,
                     OccurrenceReport, OccurrenceType, OccurrenceAction, OccurrenceAuditEntry,
                     ContactType, MembershipHistoryEntry, VoucherType,
                     create_maint_log_entry)
from .availability import find_available_slots, get_date_range
from .services import booking_service
from .services import availability_service
from .services import charging_service
from .services import qualification_service


def _aware(dt):
    """Make a naive datetime timezone-aware in the active timezone."""
    if dt is not None and timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt


def _audit(booking, user, event_type, notes='', field_name='', old_value='', new_value=''):
    """Thin wrapper — logic lives in booking_service.audit."""
    booking_service.audit(booking, user, event_type, notes=notes,
                          field_name=field_name, old_value=old_value, new_value=new_value)


def _blockout_check(club, aircraft, instructor, start_dt, end_dt, actor, override, exclude_booking_id=None):
    """Thin wrapper — logic lives in booking_service.check_blockout."""
    return booking_service.check_blockout(
        club, aircraft, instructor, start_dt, end_dt, actor, override,
        exclude_booking_id=exclude_booking_id
    )


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
        club=club, is_on_instructor_roster=True
    ).select_related('user').order_by('standing', 'user__last_name')
    
    aircraft_list = Aircraft.objects.filter(club=club, status='online').order_by('registration')
    all_aircraft = Aircraft.objects.filter(club=club).order_by('registration')
    
    # Get all bookings for day
    bookings = Booking.objects.filter(
        club=club,
        scheduled_start__gte=day_start,
        scheduled_start__lt=day_end + timedelta(days=1)
    ).exclude(status='cancelled').select_related('member__user', 'aircraft', 'instructor', 'confirmed_by', 'flight_type', 'flight_completion')
    
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

        if b.instructor_id:
            instr_cm = next((i for i in instructors if i.user_id == b.instructor_id), None)
            if instr_cm:
                roster = _av_cache.get(instr_cm.id)
                if roster is not True:
                    issues.append(('instructor_roster', 'Instructor off roster'))

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
            'records_hobbs':      b.aircraft.records_hobbs      if b.aircraft else False,
            'records_tacho':      b.aircraft.records_tacho      if b.aircraft else False,
            'records_airswitch':  b.aircraft.records_airswitch  if b.aircraft else False,
            'paid': (getattr(getattr(b, 'flight_completion', None), 'paid_at', None) is not None),
            'decl_pending': (
                getattr(b.flight_type, 'requires_declaration', False) and
                b.status in ('pending', 'confirmed') and
                not (hasattr(b, 'declaration') and not b.declaration.is_draft)
            ),
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

        normal_show = is_active and on_roster is True
        ghost = (not normal_show) and has_bookings
        if not normal_show and not ghost:
            continue

        if instr.standing in ('resigned', 'lapsed'):
            ghost_reason = 'inactive'
        elif on_roster is not True:
            ghost_reason = 'off_roster'
        else:
            ghost_reason = None

        bands = bands_for_instructor(instr.user)
        instructor_rows.append({
            'type': 'instructor',
            'label': f"{instr.user.first_name} {instr.user.last_name}".strip() or instr.user.username,
            'row_key': f"instructor:{instr.user.id}",
            'resource_id': instr.user.id,
            'detail_id': instr.id,
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
            'label': f"{ac.registration} ({ac.aircraft_type.name if ac.aircraft_type_id else '?'})",
            'row_key': f"aircraft:{ac.id}",
            'resource_id': ac.id,
            'detail_id': ac.id,
            'pills': [booking_geometry(b) for b in ac_bookings],
            'bands': ac_bands,
            'has_blockout': bool(ac_bands),
            'ghost': ghost,
            'ghost_reason': 'retired' if ghost else None,
        })

    # Row-label width: fit the longest instructor/aircraft label (7px/char approx at .76rem)
    all_labels = [r['label'] for r in instructor_rows + aircraft_rows]
    label_w = max(140, min(220, max((len(l) * 7 + 18 for l in all_labels), default=140)))

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
        {'id': a.id, 'reg': a.registration, 'type': a.aircraft_type.name if a.aircraft_type_id else ''} for a in aircraft_list
    ]
    instructors_data = [
        {
            'id': i.user.id,
            'name': f"{i.user.first_name} {i.user.last_name}".strip(),
            'on_roster': _av_cache.get(i.id),  # True / False / None (None = no schedule = always available)
        }
        for i in instructors
    ]
    flight_types_data = [
        {'id': ft.id, 'name': ft.name, 'code': ft.code, 'is_solo': ft.is_solo}
        for ft in FlightType.objects.filter(club=club)
    ]
    blockout_types_data = [
        {'id': bt.id, 'name': bt.name, 'target': bt.target}
        for bt in BlockOutType.objects.filter(club=club)
    ]

    # Now-line: pixel offset of current time (only for today, only if within operating window)
    now_px = None
    if selected_date == today:
        now_dt = timezone.now()
        if day_start <= now_dt <= day_end:
            now_px = int((now_dt - day_start).total_seconds() / 60 * px_per_min)

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
        'now_px': now_px,
        'label_w': label_w,
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
    if not (club_member.is_admin or club_member.is_instructor):
        return JsonResponse({'error': 'Only instructors and admins can reschedule'}, status=403)

    new_start_str = request.POST.get('new_start')
    if not new_start_str:
        return JsonResponse({'error': 'Missing new_start'}, status=400)

    try:
        new_start_dt  = _aware(datetime.fromisoformat(new_start_str))
        duration_param = request.POST.get('duration')
        duration = int(duration_param) if duration_param else int(
            (booking.scheduled_end - booking.scheduled_start).total_seconds() / 60
        )

        aircraft_id   = request.POST.get('aircraft_id')
        instructor_id = request.POST.get('instructor_id')
        override      = request.POST.get('override') in ('1', 'true', 'on')

        aircraft = None
        if aircraft_id:
            aircraft = Aircraft.objects.filter(id=aircraft_id, club=club).first()
            if not aircraft:
                return JsonResponse({'error': 'Aircraft not found'}, status=404)

        instructor = None
        if instructor_id:
            from .models import User as _User
            instructor = _User.objects.filter(id=instructor_id).first()

        result = booking_service.reschedule(
            booking, club_member, new_start_dt, duration,
            aircraft=aircraft, instructor=instructor, override=override
        )
        if not result.ok:
            status_code = 409 if result.data.get('blockout') else 400
            return JsonResponse({'error': result.error, **result.data}, status=status_code)
        return JsonResponse({'success': True})

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


def _credential_checks(booking):
    """
    Return a list of {label, status ('ok'|'warn'|'info'), detail} for the
    booking's member + flight type. Used at confirmation time.
    """
    from datetime import date as _d
    from django.db.models import Q as _Q

    member = booking.member
    ft = booking.flight_type
    today = _d.today()
    creds = member.credentials.all()

    LICENCE_TYPES = ('ppl', 'cpl', 'atpl', 'instr_c', 'instr_b', 'instr_a', 'examiner')
    MEDICAL_TYPES  = ('medical_c1', 'medical_c2', 'medical_c3', 'dlr9')
    SOLO_MEDICAL   = ('medical_c1', 'medical_c2', 'dlr9')  # Class 2+ for solo/private

    def latest_valid(*types):
        return (creds
                .filter(credential_type__in=types)
                .filter(_Q(expiry_date__isnull=True) | _Q(expiry_date__gte=today))
                .order_by('-expiry_date')
                .first())

    checks = []

    # ── Medical ──────────────────────────────────────────────────────────────
    med = latest_valid(*MEDICAL_TYPES)
    if not med:
        checks.append({'label': 'Medical certificate',
                       'status': 'warn',
                       'detail': 'No current medical certificate on record'})
    elif ft.is_solo and med.credential_type == 'medical_c3':
        checks.append({'label': 'Medical certificate',
                       'status': 'warn',
                       'detail': f'Class 3 medical only — Class 2 or better is required for private solo flying'})
    else:
        exp = f', valid to {med.expiry_date}' if med.expiry_date else ''
        checks.append({'label': 'Medical certificate', 'status': 'ok',
                       'detail': f'{med.display_name}{exp}'})

    if ft.is_solo:
        # ── Pilot licence ─────────────────────────────────────────────────────
        licence = latest_valid(*LICENCE_TYPES)
        if not licence:
            checks.append({'label': 'Pilot licence',
                           'status': 'warn',
                           'detail': 'No PPL or higher licence on record'})
        else:
            checks.append({'label': 'Pilot licence', 'status': 'ok',
                           'detail': licence.display_name})

        # ── Flight Review (BFR) — every 24 months ────────────────────────────
        fr = latest_valid('fr')
        if not fr:
            checks.append({'label': 'Flight Review (BFR)',
                           'status': 'warn',
                           'detail': 'No current Flight Review on record — required every 24 months for PPL/CPL/ATPL'})
        else:
            exp = f', valid to {fr.expiry_date}' if fr.expiry_date else ''
            checks.append({'label': 'Flight Review (BFR)', 'status': 'ok',
                           'detail': f'Current{exp}'})

    # ── Age ───────────────────────────────────────────────────────────────────
    if ft.is_solo:
        if member.date_of_birth:
            age = (today - member.date_of_birth).days // 365
            # NZ CAA: solo minimum 16, PPL minimum 17
            min_age = 16 if ft.is_training else 17
            if age < min_age:
                checks.append({'label': 'Minimum age',
                               'status': 'warn',
                               'detail': f'Member is {age} years old — minimum is {min_age} for this flight type'})
            else:
                checks.append({'label': 'Minimum age', 'status': 'ok',
                               'detail': f'Age {age} — meets minimum of {min_age}'})
        else:
            checks.append({'label': 'Minimum age', 'status': 'info',
                           'detail': 'Date of birth not recorded — cannot verify minimum age requirement'})

    return checks


@login_required
def prev_readings_api(request, booking_id):
    """Return the last recorded meter end readings for the aircraft on this booking."""
    booking = get_object_or_404(Booking, id=booking_id)
    try:
        actor = ClubMember.objects.get(user=request.user, club=booking.club)
    except ClubMember.DoesNotExist:
        return JsonResponse({'error': 'Not authorized'}, status=403)
    if not (actor.is_admin or actor.is_instructor):
        return JsonResponse({'error': 'Not authorized'}, status=403)

    from django.db.models import Q as _Q
    prev = (FlightCompletion.objects
            .filter(booking__aircraft=booking.aircraft, booking__club=booking.club)
            .exclude(booking=booking)
            .filter(_Q(hobbs_end__isnull=False) | _Q(tacho_end__isnull=False) | _Q(airswitch_end__isnull=False))
            .order_by('-booking__arrived_at', '-created_at')
            .first())
    if not prev:
        return JsonResponse({})
    return JsonResponse({
        'hobbs_end':     float(prev.hobbs_end)     if prev.hobbs_end     is not None else None,
        'tacho_end':     float(prev.tacho_end)     if prev.tacho_end     is not None else None,
        'airswitch_end': float(prev.airswitch_end) if prev.airswitch_end is not None else None,
    })


@login_required
def credential_check_api(request, booking_id):
    """Pre-confirmation credential check for staff — member credentials + aircraft maintenance."""
    booking = get_object_or_404(Booking, id=booking_id)
    try:
        actor = ClubMember.objects.get(user=request.user, club=booking.club)
    except ClubMember.DoesNotExist:
        return JsonResponse({'error': 'Not authorized'}, status=403)
    if not (actor.is_admin or actor.is_instructor):
        return JsonResponse({'error': 'Not authorized'}, status=403)

    checks = _credential_checks(booking)

    # Aircraft maintenance items with progress
    ac = booking.aircraft
    maint_items = list(AircraftMaintenanceItem.objects.filter(aircraft=ac).order_by('urgency', 'due_date'))
    latest_fc = (FlightCompletion.objects
                 .filter(booking__aircraft=ac)
                 .exclude(hobbs_end__isnull=True)
                 .order_by('-booking__arrived_at', '-created_at')
                 .values('hobbs_end', 'tacho_end').first())
    current_hobbs = float(latest_fc['hobbs_end']) if latest_fc and latest_fc['hobbs_end'] else None

    _today = date.today()
    maint_data = []
    for m in maint_items:
        progress_pct = None
        detail = ''
        if m.last_completed_date and m.due_date:
            total_days = max(1, (m.due_date - m.last_completed_date).days)
            elapsed = (_today - m.last_completed_date).days
            days_left = (m.due_date - _today).days
            progress_pct = min(100, max(0, round(elapsed / total_days * 100)))
            detail = f'{days_left}d remaining' if days_left >= 0 else f'{abs(days_left)}d overdue'
        elif m.last_completed_hours is not None and m.due_hours is not None and current_hobbs is not None:
            total_h = float(m.due_hours - m.last_completed_hours)
            hrs_left = float(m.due_hours) - current_hobbs
            if total_h > 0:
                progress_pct = min(100, max(0, round((current_hobbs - float(m.last_completed_hours)) / total_h * 100)))
            detail = f'{hrs_left:.1f}h remaining' if hrs_left >= 0 else f'{abs(hrs_left):.1f}h overdue'
        elif m.due_date:
            days_left = (m.due_date - _today).days
            detail = f'{days_left}d remaining' if days_left >= 0 else f'{abs(days_left)}d overdue'
        maint_data.append({
            'name': m.name,
            'urgency': m.urgency,
            'progress_pct': progress_pct,
            'detail': detail,
        })

    return JsonResponse({
        'member': booking.member.user.get_full_name(),
        'flight_type': booking.flight_type.name,
        'is_solo': booking.flight_type.is_solo,
        'checks': checks,
        'has_warnings': any(c['status'] == 'warn' for c in checks),
        'maintenance': maint_data,
        'has_maint_warnings': any(m['urgency'] in ('amber', 'red') for m in maint_data),
        'current_hobbs': current_hobbs,
        'aircraft_reg': ac.registration,
    })


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

    result = booking_service.confirm(booking, request.user)
    if not result.ok:
        return JsonResponse({'error': result.error}, status=400)
    return JsonResponse({'success': True})


@login_required
@require_POST
def depart_booking(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)
    club = booking.club
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return JsonResponse({'error': 'Not authorized'}, status=403)
    is_own = (booking.member.user == request.user)
    if not (actor.is_admin or actor.is_instructor or is_own):
        return JsonResponse({'error': 'Not authorized'}, status=403)

    no_decl_reason = request.POST.get('no_declaration_reason', '').strip()
    result = booking_service.depart(booking, request.user, no_decl_reason)
    if not result.ok:
        return JsonResponse({'error': result.error, **result.data}, status=400)
    return JsonResponse({'success': True, 'status': 'departed'})


@login_required
@require_POST
@transaction.atomic
def checkin_booking(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)
    club = booking.club
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return JsonResponse({'error': 'Not authorized'}, status=403)
    if not (actor.is_admin or actor.is_instructor):
        return JsonResponse({'error': 'Instructors only'}, status=403)

    result = booking_service.check_in(
        booking, request.user,
        outcome=request.POST.get('outcome', 'completed'),
        outcome_notes=request.POST.get('outcome_notes', '').strip(),
        hobbs_start=request.POST.get('hobbs_start', '').strip() or None,
        hobbs_end=request.POST.get('hobbs_end', '').strip() or None,
        tacho_start=request.POST.get('tacho_start', '').strip() or None,
        tacho_end=request.POST.get('tacho_end', '').strip() or None,
        airswitch_start=request.POST.get('airswitch_start', '').strip() or None,
        airswitch_end=request.POST.get('airswitch_end', '').strip() or None,
    )
    if not result.ok:
        return JsonResponse({'error': result.error}, status=400)
    return JsonResponse({'success': True, 'status': 'completed',
                         'charges_url': result.data.get('charges_url', '')})


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

    result = booking_service.cancel(
        booking, request.user,
        release_slot=request.POST.get('release') == '1',
        reason=request.POST.get('reason', ''),
        reason_other=request.POST.get('reason_other', ''),
    )
    if not result.ok:
        return JsonResponse({'error': result.error}, status=400)
    return JsonResponse({'success': True})


@login_required
@require_POST
@transaction.atomic
def create_booking(request):
    try:
        actor = ClubMember.objects.filter(user=request.user).first()
        if not actor:
            return JsonResponse({'error': 'Not a club member'}, status=403)

        club     = actor.club
        config   = get_config(club)
        aircraft_id   = request.POST.get('aircraft_id')
        start_time    = request.POST.get('start_time')
        duration      = int(request.POST.get('duration') or config.default_booking_duration)
        instructor_id = request.POST.get('instructor_id')
        member_id     = request.POST.get('member_id')
        description   = request.POST.get('description', '')
        override      = request.POST.get('override') in ('1', 'true', 'on')

        if not aircraft_id or not start_time:
            return JsonResponse({'error': 'Missing aircraft or start time'}, status=400)

        aircraft = Aircraft.objects.get(id=aircraft_id, club=club)
        start_dt = _aware(datetime.fromisoformat(start_time))
        end_dt   = start_dt + timedelta(minutes=duration)

        # Who is the booking FOR?
        booking_member = actor
        if member_id and (actor.is_admin or actor.is_instructor):
            from .models import User as _User
            target_user = _User.objects.filter(id=member_id).first()
            if target_user:
                booking_member = ClubMember.objects.filter(user=target_user, club=club).first() or actor

        instructor = None
        if instructor_id:
            from .models import User as _User
            instructor = _User.objects.filter(id=instructor_id).first()

        # Flight type resolution
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

        result = booking_service.create(
            club, actor, aircraft, start_dt, end_dt, flight_type,
            instructor=instructor, booking_member=booking_member,
            description=description, override=override,
        )
        if not result.ok:
            status_code = 409 if result.data.get('blockout') else 400
            return JsonResponse({'error': result.error, **result.data}, status=status_code)
        return JsonResponse({'success': True, 'booking_id': result.data['booking_id']})

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
        config        = get_config(club)
        aircraft_id   = request.POST.get('aircraft_id')
        start_time    = request.POST.get('start_time')
        duration      = int(request.POST.get('duration') or config.default_booking_duration)
        instructor_id = request.POST.get('instructor_id')
        member_id     = request.POST.get('member_id')
        description   = request.POST.get('description', '')
        override      = request.POST.get('override') in ('1', 'true', 'on')

        aircraft = Aircraft.objects.get(id=aircraft_id, club=club) if aircraft_id else booking.aircraft
        start_dt = _aware(datetime.fromisoformat(start_time)) if start_time else booking.scheduled_start
        end_dt   = start_dt + timedelta(minutes=duration)

        instructor = None
        if instructor_id:
            from .models import User as _User
            instructor = _User.objects.filter(id=instructor_id).first()

        booking_member = None
        if member_id:
            from .models import User as _User
            tu = _User.objects.filter(id=member_id).first()
            if tu:
                booking_member = ClubMember.objects.filter(user=tu, club=club).first()

        flight_type = None
        flight_type_id = request.POST.get('flight_type_id')
        if flight_type_id:
            flight_type = FlightType.objects.filter(club=club, id=flight_type_id).first()

        result = booking_service.edit(
            booking, actor, aircraft, start_dt, end_dt,
            flight_type=flight_type, instructor=instructor,
            booking_member=booking_member, description=description, override=override,
        )
        if not result.ok:
            status_code = 409 if result.data.get('blockout') else 400
            return JsonResponse({'error': result.error, **result.data}, status=status_code)
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
    instructors = ClubMember.objects.filter(club=club, is_on_instructor_roster=True).select_related('user')
    aircraft_list = Aircraft.objects.filter(club=club, status='online')
    aircraft_types = sorted(set(
        a.aircraft_type.name for a in aircraft_list if a.aircraft_type_id
    ))
    
    results = []
    search_performed = False
    filters_applied = {}
    result_count = 0
    
    # Support both POST (legacy) and GET (preferred — allows bookmarkable URLs and auto-resubmit)
    _p = request.GET if request.method == 'GET' else request.POST
    if request.method == 'POST' or request.GET.get('s'):
        search_performed = True
        range_type = _p.get('range_type', 'this_week')
        aircraft_filter = _p.get('aircraft', '')
        aircraft_type_filter = _p.get('aircraft_type', '')
        instructor_filter = _p.get('instructor', '')
        booking_kind = _p.get('booking_kind', 'dual')
        duration = int(_p.get('duration') or config.default_booking_duration)

        # --- Reconcile aircraft type vs specific aircraft (specific wins) ---
        specific_aircraft = Aircraft.objects.filter(club=club, id=aircraft_filter).first() if aircraft_filter else None
        if specific_aircraft:
            # A specific tail implies its type; ignore any conflicting type filter.
            aircraft_type_filter = specific_aircraft.aircraft_type.name if specific_aircraft.aircraft_type_id else ''
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
        _min_mins = config.time_slot_interval or 30

        def clip_and_mark(s, st_dt, en_dt):
            """Clip span to typical operating hours. Returns False if span is too short after clipping."""
            from datetime import time as _time
            day = st_dt.date()
            tz = st_dt.tzinfo
            from datetime import datetime as _dt
            typ_s = timezone.make_aware(_dt.combine(day, typ_start), tz) if tz else _dt.combine(day, typ_start)
            typ_e = timezone.make_aware(_dt.combine(day, typ_end), tz) if tz else _dt.combine(day, typ_end)
            clipped_start = max(st_dt, typ_s)
            clipped_end = min(en_dt, typ_e)
            if (clipped_end - clipped_start).total_seconds() / 60 < _min_mins:
                return False
            s['start'] = clipped_start
            s['end'] = clipped_end
            s['minutes'] = int((clipped_end - clipped_start).total_seconds() / 60)
            s['start_label'] = clipped_start.strftime('%H:%M')
            s['end_label'] = clipped_end.strftime('%H:%M')
            s['atypical'] = False
            s['start_iso'] = clipped_start.isoformat()
            return True

        by_day = {}
        _now = timezone.now()

        if is_solo:
            # Aircraft-only spans; no instructor.
            raw = availability_service.free_spans_solo(
                club, date_start, date_end,
                aircraft=specific_aircraft, aircraft_type=aircraft_type_filter or None,
                min_minutes=config.time_slot_interval,
            )
            for entry in raw:
                d = entry['date']; ac = entry['aircraft']
                future_spans = []
                for s in entry['spans']:
                    if s['start'] <= _now:
                        continue  # skip slots already started or in the past
                    st = timezone.localtime(s['start']); en = timezone.localtime(s['end'])
                    if not clip_and_mark(s, st, en):
                        continue  # outside or too short after clipping to typical hours
                    s['aircraft_id'] = ac.id
                    s['instructor_id'] = ''  # solo
                    future_spans.append(s)
                if future_spans:
                    by_day.setdefault(d, []).append({
                        'aircraft': ac,
                        'instructor_rows': [{'instructor': None, 'instructor_name': 'Solo (no instructor)', 'spans': future_spans}],
                    })
        else:
            # Dual: aircraft AND instructor both free.
            raw = availability_service.free_spans_dual(
                club, date_start, date_end,
                aircraft=specific_aircraft, aircraft_type=aircraft_type_filter or None,
                instructor=specific_instructor, min_minutes=config.time_slot_interval,
            )
            for entry in raw:
                d = entry['date']; ac = entry['aircraft']
                instr_rows = []
                for ir in entry['instructor_rows']:
                    instr = ir['instructor']
                    future_spans = []
                    for s in ir['spans']:
                        if s['start'] <= _now:
                            continue
                        st = timezone.localtime(s['start']); en = timezone.localtime(s['end'])
                        if not clip_and_mark(s, st, en):
                            continue
                        s['aircraft_id'] = ac.id
                        s['instructor_id'] = instr.id
                        future_spans.append(s)
                    if future_spans:
                        instr_rows.append({
                            'instructor': instr,
                            'instructor_name': f"{instr.first_name} {instr.last_name}".strip() or instr.username,
                            'spans': future_spans,
                        })
                if instr_rows:
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
    aircraft_type_map = _json.dumps({str(a.id): (a.aircraft_type.name if a.aircraft_type_id else '') for a in aircraft_list})

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
        is_on_instructor_roster=True,
    ).exclude(user_id__in=busy_instructors).select_related('user')
    
    # Get available aircraft of same type
    busy_aircraft = Booking.objects.filter(
        club=club,
        scheduled_start__lt=booking.scheduled_end,
        scheduled_end__gt=booking.scheduled_start
    ).exclude(id=booking.id).values_list('aircraft_id', flat=True)
    
    available_aircraft = Aircraft.objects.filter(
        club=club,
        aircraft_type=booking.aircraft.aircraft_type_id,
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
def club_settings(request, club_slug, mode='settings'):
    """Admin-only settings page. mode='settings' shows general/billing/roles; mode='types' shows reference type lists."""
    club = get_object_or_404(Club, slug=club_slug)
    try:
        member = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not member.is_admin:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    config = get_config(club)
    saved = False
    is_types = (mode == 'types')
    _redir_name = 'core:club_types' if is_types else 'core:club_settings'

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
            return _redirect(_redir_name, club_slug=club_slug)

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
            return _redirect(_redir_name, club_slug=club_slug)

        elif action == 'set_flight_type_flag':
            ft_id = request.POST.get('ft_id')
            flag = request.POST.get('ft_flag')
            value = request.POST.get('ft_value') == '1'
            allowed = {'is_training', 'is_billable', 'requires_declaration', 'is_solo'}
            ft = FlightType.objects.filter(club=club, id=ft_id).first()
            if ft and flag in allowed:
                setattr(ft, flag, value)
                ft.save(update_fields=[flag])
            return redirect(_redir_name, club_slug=club_slug)

        elif action == 'edit_flight_type':
            ft_id = request.POST.get('ft_id')
            ft = FlightType.objects.filter(club=club, id=ft_id).first()
            if ft:
                name = request.POST.get('ft_name', '').strip()
                if name:
                    ft.name = name
                    ft.save(update_fields=['name'])

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
            return redirect(_redir_name, club_slug=club_slug)

        elif action == 'set_blockout_type_hard':
            bot_id = request.POST.get('bot_id')
            is_hard = 'is_hard' in request.POST
            bt = BlockOutType.objects.filter(club=club, id=bot_id).first()
            if bt:
                bt.is_hard = is_hard
                bt.save(update_fields=['is_hard'])
            return redirect(_redir_name, club_slug=club_slug)

        elif action == 'edit_blockout_type':
            bot_id = request.POST.get('bot_id')
            bt = BlockOutType.objects.filter(club=club, id=bot_id).first()
            if bt:
                name = request.POST.get('bot_name', '').strip()
                color = request.POST.get('bot_color', '').strip()
                if name:
                    bt.name = name
                if color:
                    bt.color = color
                bt.save(update_fields=['name', 'color'])

        elif action == 'delete_blockout_type':
            bot_id = request.POST.get('bot_id')
            bt = BlockOutType.objects.filter(club=club, id=bot_id).first()
            if bt:
                bt.delete()
            return redirect(_redir_name, club_slug=club_slug)

        # ── Role management ──────────────────────────────────────────────────
        elif action == 'add_role':
            rname = request.POST.get('role_name', '').strip()
            if rname:
                Role.objects.get_or_create(club=club, name=rname)
            return redirect(_redir_name, club_slug=club_slug)

        elif action == 'delete_role':
            role = Role.objects.filter(club=club, id=request.POST.get('role_id')).first()
            if role and role.is_system_role:
                from django.contrib import messages as _msg
                _msg.error(request, f'"{role.name}" is a system role and cannot be deleted.')
            elif role:
                role.delete()
            return redirect(_redir_name, club_slug=club_slug)

        elif action == 'rename_role':
            role = Role.objects.filter(club=club, id=request.POST.get('role_id')).first()
            new_name = request.POST.get('role_name', '').strip()
            if role and new_name and new_name != role.name:
                if not Role.objects.filter(club=club, name=new_name).exclude(pk=role.pk).exists():
                    role.name = new_name
                    role.save(update_fields=['name'])
                else:
                    from django.contrib import messages as _msg
                    _msg.error(request, f'A role named "{new_name}" already exists.')
            return redirect(redirect(_redir_name, club_slug=club_slug).url + '?tab=roles&saved=1')

        elif action == 'set_role_fee':
            role = Role.objects.filter(club=club, id=request.POST.get('role_id')).first()
            if role:
                raw = request.POST.get('fee', '').strip()
                role.annual_renewal_fee = raw if raw else None
                role.save(update_fields=['annual_renewal_fee'])
            return redirect(_redir_name, club_slug=club_slug)

        elif action == 'set_role_permission':
            _BOOL_PERMS = {'can_access_manage', 'can_access_fleet', 'can_access_safety',
                           'can_access_settings', 'can_access_reports', 'is_superadmin', 'renewal_required'}
            role = Role.objects.filter(club=club, id=request.POST.get('role_id')).first()
            perm = request.POST.get('perm', '')
            # Admin system role permissions are always ALL — block changes
            if role and perm and role.system_role_type != 'admin':
                if perm == 'bookings_access':
                    val = request.POST.get('value', '')
                    if val in dict(Role.BOOKINGS_CHOICES):
                        role.bookings_access = val
                        role.save(update_fields=['bookings_access'])
                elif perm in _BOOL_PERMS:
                    setattr(role, perm, request.POST.get('value') == '1')
                    role.save(update_fields=[perm])
            return redirect(_redir_name, club_slug=club_slug)

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
            return redirect(_redir_name, club_slug=club_slug)

        elif action == 'delete_instructor_grade':
            InstructorGrade.objects.filter(club=club, id=request.POST.get('ig_id')).delete()
            return redirect(_redir_name, club_slug=club_slug)

        elif action == 'edit_instructor_grade':
            ig = InstructorGrade.objects.filter(club=club, id=request.POST.get('ig_id')).first()
            if ig:
                name = request.POST.get('ig_name', '').strip()
                if name:
                    ig.name = name
                ig.hourly_rate = request.POST.get('ig_rate', ig.hourly_rate)
                order = request.POST.get('ig_order', '').strip()
                if order.isdigit():
                    ig.display_order = int(order)
                ig.save(update_fields=['name', 'hourly_rate', 'display_order'])
            return redirect(_redir_name, club_slug=club_slug)

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
            return redirect(_redir_name, club_slug=club_slug)

        elif action == 'delete_surcharge_type':
            AircraftSurchargeType.objects.filter(club=club, id=request.POST.get('st_id')).delete()
            return redirect(_redir_name, club_slug=club_slug)

        elif action == 'edit_surcharge_type':
            st = AircraftSurchargeType.objects.filter(club=club, id=request.POST.get('st_id')).first()
            if st:
                name = request.POST.get('st_name', '').strip()
                if name:
                    st.name = name
                st.description = request.POST.get('st_desc', '').strip()
                st.amount = request.POST.get('st_amount', st.amount)
                st.save(update_fields=['name', 'description', 'amount'])
            return redirect(_redir_name, club_slug=club_slug)

        elif action == 'add_aircraft_type':
            at_name = request.POST.get('at_name', '').strip()
            at_icao = request.POST.get('at_icao', '').strip().upper()
            if at_name:
                AircraftType.objects.get_or_create(club=club, name=at_name,
                                                    defaults={'icao_designator': at_icao})
            return redirect(_redir_name, club_slug=club_slug)

        elif action == 'edit_aircraft_type':
            at = AircraftType.objects.filter(club=club, id=request.POST.get('at_id')).first()
            if at:
                name = request.POST.get('at_name', '').strip()
                if name:
                    at.name = name
                at.icao_designator = request.POST.get('at_icao', '').strip().upper()
                at.save(update_fields=['name', 'icao_designator'])
            return redirect(_redir_name, club_slug=club_slug)

        elif action == 'delete_aircraft_type':
            at = AircraftType.objects.filter(club=club, id=request.POST.get('at_id')).first()
            if at:
                if at.aircraft.exists():
                    ft_error = f"Cannot delete '{at.name}' — it is assigned to aircraft in the fleet."
                elif at.type_ratings.exists():
                    ft_error = f"Cannot delete '{at.name}' — it is referenced by member type ratings."
                else:
                    at.delete()
            if not ft_error:
                return redirect(_redir_name, club_slug=club_slug)

        elif action == 'add_occurrence_type':
            ot_name = request.POST.get('ot_name', '').strip()
            ot_desc = request.POST.get('ot_desc', '').strip()
            if ot_name:
                OccurrenceType.objects.get_or_create(club=club, name=ot_name, defaults={'description': ot_desc})
            return redirect(f"{redirect(_redir_name, club_slug=club_slug).url}?tab=incident-types&saved=1")

        elif action == 'edit_occurrence_type':
            ot = OccurrenceType.objects.filter(club=club, id=request.POST.get('ot_id')).first()
            if ot:
                ot_name = request.POST.get('ot_name', '').strip()
                if ot_name:
                    ot.name = ot_name
                ot.description = request.POST.get('ot_desc', '').strip()
                ot.sort_order = int(request.POST.get('ot_sort', ot.sort_order) or ot.sort_order)
                ot.save()
            return redirect(f"{redirect(_redir_name, club_slug=club_slug).url}?tab=incident-types&saved=1")

        elif action == 'delete_occurrence_type':
            ot = OccurrenceType.objects.filter(club=club, id=request.POST.get('ot_id')).first()
            if ot and not ot.reports.exists():
                ot.delete()
            return redirect(f"{redirect(_redir_name, club_slug=club_slug).url}?tab=incident-types&saved=1")

        elif action == 'toggle_occurrence_type':
            ot = OccurrenceType.objects.filter(club=club, id=request.POST.get('ot_id')).first()
            if ot:
                ot.is_active = not ot.is_active
                ot.save(update_fields=['is_active'])
            return redirect(f"{redirect(_redir_name, club_slug=club_slug).url}?tab=incident-types&saved=1")

        elif action == 'add_contact_type':
            ct_name = request.POST.get('ct_name', '').strip()
            if ct_name:
                ContactType.objects.get_or_create(club=club, name=ct_name,
                    defaults={'sort_order': ContactType.objects.filter(club=club).count()})
            return redirect(f"{redirect(_redir_name, club_slug=club_slug).url}?tab=contact-types&saved=1")

        elif action == 'edit_contact_type':
            ct = ContactType.objects.filter(club=club, id=request.POST.get('ct_id')).first()
            if ct:
                ct_name = request.POST.get('ct_name', '').strip()
                if ct_name: ct.name = ct_name
                ct.sort_order = int(request.POST.get('ct_sort', ct.sort_order) or ct.sort_order)
                ct.save()
            return redirect(f"{redirect(_redir_name, club_slug=club_slug).url}?tab=contact-types&saved=1")

        elif action == 'delete_contact_type':
            ct = ContactType.objects.filter(club=club, id=request.POST.get('ct_id')).first()
            if ct and not ct.contacts.exists():
                ct.delete()
            return redirect(f"{redirect(_redir_name, club_slug=club_slug).url}?tab=contact-types&saved=1")

        elif action == 'toggle_contact_type':
            ct = ContactType.objects.filter(club=club, id=request.POST.get('ct_id')).first()
            if ct:
                ct.is_active = not ct.is_active
                ct.save(update_fields=['is_active'])
            return redirect(f"{redirect(_redir_name, club_slug=club_slug).url}?tab=contact-types&saved=1")

        elif action == 'add_voucher_type':
            vt_name = request.POST.get('vt_name', '').strip()
            vt_val  = request.POST.get('vt_value', '').strip()
            if vt_name and vt_val:
                try:
                    VoucherType.objects.get_or_create(
                        club=club, name=vt_name,
                        defaults={
                            'default_value': vt_val,
                            'description': request.POST.get('vt_desc', '').strip(),
                            'sort_order': VoucherType.objects.filter(club=club).count(),
                        }
                    )
                except Exception:
                    pass
            return redirect(f"{redirect(_redir_name, club_slug=club_slug).url}?tab=voucher-types&saved=1")

        elif action == 'edit_voucher_type':
            vt = VoucherType.objects.filter(club=club, id=request.POST.get('vt_id')).first()
            if vt:
                vt.name        = request.POST.get('vt_name', vt.name).strip() or vt.name
                try: vt.default_value = float(request.POST.get('vt_value', vt.default_value))
                except (ValueError, TypeError): pass
                vt.description = request.POST.get('vt_desc', '').strip()
                vt.sort_order  = int(request.POST.get('vt_sort', vt.sort_order) or vt.sort_order)
                vt.save()
            return redirect(f"{redirect(_redir_name, club_slug=club_slug).url}?tab=voucher-types&saved=1")

        elif action == 'delete_voucher_type':
            vt = VoucherType.objects.filter(club=club, id=request.POST.get('vt_id')).first()
            if vt:
                vt.delete()
            return redirect(f"{redirect(_redir_name, club_slug=club_slug).url}?tab=voucher-types&saved=1")

        elif action == 'toggle_voucher_type':
            vt = VoucherType.objects.filter(club=club, id=request.POST.get('vt_id')).first()
            if vt:
                vt.is_active = not vt.is_active
                vt.save(update_fields=['is_active'])
            return redirect(f"{redirect(_redir_name, club_slug=club_slug).url}?tab=voucher-types&saved=1")

        elif action == 'save_billing':
            for field in ['billing_name', 'billing_address', 'billing_phone', 'billing_email',
                          'gst_number', 'bank_name', 'bank_account', 'payment_terms_text',
                          'invoice_number_prefix']:
                setattr(config, field, request.POST.get(field, '').strip())
            try:
                config.gst_rate = float(request.POST.get('gst_rate', config.gst_rate))
            except (ValueError, TypeError):
                pass
            try:
                config.payment_terms_days = int(request.POST.get('payment_terms_days', config.payment_terms_days))
            except (ValueError, TypeError):
                pass
            try:
                next_num = int(request.POST.get('invoice_number_next', ''))
                if next_num > 0:
                    config.invoice_number_next = next_num
            except (ValueError, TypeError):
                pass
            try:
                fy = int(request.POST.get('fy_start_month', config.fy_start_month))
                if 1 <= fy <= 12:
                    config.fy_start_month = fy
            except (ValueError, TypeError):
                pass
            config.save()
            return redirect(f"{redirect(_redir_name, club_slug=club_slug).url}?tab=billing&saved=1")

        else:
            club_name = request.POST.get('club_name', '').strip()
            if club_name:
                club.name = club_name
                club.save(update_fields=['name'])
            for field in ['theme_banner', 'theme_primary', 'theme_accent',
                          'theme_confirmed', 'theme_pending',
                          'theme_departed', 'theme_returned', 'theme_completed_paid',
                          'theme_weekend', 'theme_atypical']:
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
            return redirect(f"{redirect(_redir_name, club_slug=club_slug).url}?saved=1")

    color_fields = [
        ('theme_banner', 'Banner', config.theme_banner),
        ('theme_primary', 'Primary (buttons, links)', config.theme_primary),
        ('theme_accent', 'Accent', config.theme_accent),
        ('theme_confirmed', 'Confirmed booking', config.theme_confirmed),
        ('theme_pending', 'Pending booking', config.theme_pending),
        ('theme_departed', 'Departed', config.theme_departed),
        ('theme_returned', 'Returned (awaiting payment)', config.theme_returned),
        ('theme_completed_paid', 'Completed & paid', config.theme_completed_paid),
        ('theme_weekend', 'Weekend shade', config.theme_weekend),
        ('theme_atypical', 'Outside typical hours', config.theme_atypical),
    ]

    status_color_fields = [
        ('theme_confirmed',     'Confirmed',            config.theme_confirmed),
        ('theme_pending',       'Pending',              config.theme_pending),
        ('theme_departed',      'Departed',             config.theme_departed),
        ('theme_returned',      'Returned',             config.theme_returned),
        ('theme_completed_paid','Completed & paid',     config.theme_completed_paid),
    ]

    _BOOL_PERM_NAMES = ['can_access_manage', 'can_access_fleet', 'can_access_safety', 'can_access_settings', 'can_access_reports', 'is_superadmin', 'renewal_required']
    from django.db.models import Count as _Count, Case as _Case, When as _When, IntegerField as _IntF, Value as _Val
    _sys_order = _Case(
        _When(system_role_type='member',     then=_Val(0)),
        _When(system_role_type='instructor', then=_Val(1)),
        _When(system_role_type='admin',      then=_Val(2)),
        default=_Val(3), output_field=_IntF(),
    )
    roles = Role.objects.filter(club=club).annotate(
        member_count=_Count('clubmember'),
        sys_order=_sys_order,
    ).order_by('sys_order', 'name')
    for r in roles:
        r.perm_items = [(p, getattr(r, p)) for p in _BOOL_PERM_NAMES]
    return render(request, 'core/club_settings.html', {
        'club': club,
        'config': config,
        'color_fields': color_fields,
        'status_color_fields': status_color_fields,
        'all_blockout_types': BlockOutType.objects.filter(club=club, target='all'),
        'instructor_blockout_types': BlockOutType.objects.filter(club=club, target='instructor'),
        'aircraft_blockout_types': BlockOutType.objects.filter(club=club, target='aircraft'),
        'flight_types': FlightType.objects.filter(club=club),
        'instructor_grades': InstructorGrade.objects.filter(club=club),
        'surcharge_types': AircraftSurchargeType.objects.filter(club=club),
        'aircraft_type_list': AircraftType.objects.filter(club=club),
        'occurrence_types': OccurrenceType.objects.filter(club=club),
        'contact_types_list': ContactType.objects.filter(club=club).order_by('sort_order', 'name'),
        'voucher_types_list': VoucherType.objects.filter(club=club),
        'roles': roles,
        'saved': saved,
        'ft_error': ft_error,
        'is_types': is_types,
        'fy_month_choices': [(i, date(2000, i, 1).strftime('%B')) for i in range(1, 13)],
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

    # Pre-load instructor availability windows for off-roster conflict detection
    from .models import InstructorAvailability as _IA
    _roster_member_ids = set(
        ClubMember.objects.filter(club=club, is_on_instructor_roster=True).values_list('user_id', flat=True)
    )
    _av_windows = {}  # user_id -> list[InstructorAvailability]
    for av in _IA.objects.filter(club_member__club=club).select_related('club_member'):
        _av_windows.setdefault(av.club_member.user_id, []).append(av)

    def _instructor_off_roster(booking):
        uid = booking.instructor_id
        if not uid or uid not in _roster_member_ids:
            return False
        windows = _av_windows.get(uid, [])
        if not windows:
            return True  # no schedule = not available
        bdate = booking.scheduled_start.date()
        return not any(w.applies_on(bdate) for w in windows)

    if request.method == 'POST':
        action = request.POST.get('action', '')
        ids = [int(i) for i in request.POST.getlist('booking_ids') if i.isdigit()]
        qs = Booking.objects.filter(club=club, id__in=ids).exclude(status='cancelled')
        if action == 'confirm':
            qs.filter(status='pending').update(
                status='confirmed', confirmed_by=request.user, confirmed_at=timezone.now()
            )
        elif action == 'cancel':
            reason = request.POST.get('bulk_cancel_reason', 'no_longer_required')
            qs.update(status='cancelled', cancellation_reason=reason)
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

    from datetime import timedelta
    from urllib.parse import urlencode

    view = request.GET.get('view', 'active')
    f_aircraft   = request.GET.get('aircraft', '')
    f_instructor = request.GET.get('instructor', '')
    f_status     = request.GET.get('status', '')
    f_date_from  = request.GET.get('date_from', '')
    f_date_to    = request.GET.get('date_to', '')
    show_all_history = request.GET.get('all_history') == '1'

    _base_qs = (Booking.objects
                .filter(club=club)
                .select_related('member__user', 'aircraft', 'instructor', 'flight_type', 'flight_completion'))

    # Apply shared filters to all views
    if f_aircraft:
        _base_qs = _base_qs.filter(aircraft_id=f_aircraft)
    if f_instructor:
        _base_qs = _base_qs.filter(instructor_id=f_instructor)
    if f_status:
        _base_qs = _base_qs.filter(status=f_status)

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
        if _instructor_off_roster(b):
            r.append('Instructor off roster')
        return r

    _STATUS_ORDER = {'completed': 0, 'departed': 1, 'confirmed': 2, 'pending': 3}

    active_bookings_data = []
    recent_bookings_data = []
    bookings_data = []
    using_default_window = False

    if view == 'active':
        from django.db.models import Q as _Q2
        _near_cutoff = today + timedelta(days=14)
        # Active = urgent (any date: departed, completed-unpaid) + upcoming in next 14 days
        _active_qs = (
            _base_qs
            .filter(
                _Q2(status='departed') |
                _Q2(status='completed', flight_completion__paid_at__isnull=True) |
                _Q2(status__in=['pending', 'confirmed'], scheduled_start__date__lte=_near_cutoff)
            )
            .order_by('scheduled_start')
        )
        _all_active = list(_active_qs)
        _all_active.sort(key=lambda b: (_STATUS_ORDER.get(b.status, 9), b.scheduled_start))
        active_bookings_data = [{'b': b, 'reasons': conflict_reasons(b)} for b in _all_active]

        # Recent past: completed-paid + cancelled, last 7 days
        _cutoff = today - timedelta(days=7)
        _recent_qs = (
            _base_qs
            .filter(
                _Q2(status='completed', flight_completion__paid_at__isnull=False) |
                _Q2(status='cancelled')
            )
            .filter(scheduled_start__date__gte=_cutoff)
            .order_by('-scheduled_start')
        )
        recent_bookings_data = [{'b': b, 'reasons': []} for b in _recent_qs]

    else:  # view == 'all'
        qs = _base_qs.exclude(status='cancelled').order_by('scheduled_start')
        if not (f_date_from or f_date_to or f_status or show_all_history):
            from django.db.models import Q as _Q3
            cutoff = today - timedelta(days=30)
            qs = qs.filter(
                _Q3(status__in=['pending', 'confirmed', 'departed']) |
                _Q3(status__in=['completed', 'transferred'], scheduled_start__date__gte=cutoff)
            )
            using_default_window = True
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
        bookings_data = [{'b': b, 'reasons': conflict_reasons(b)} for b in qs]

    aircraft_list = Aircraft.objects.filter(club=club, status='online').order_by('registration')
    instructors = ClubMember.objects.filter(club=club, is_on_instructor_roster=True).select_related('user')
    members_qs = ClubMember.objects.filter(club=club).select_related('user').order_by('user__last_name')
    def _toggle_url(new_view):
        p = {k: v for k, v in request.GET.items() if k != 'view' and v}
        p['view'] = new_view
        return '?' + urlencode(p)

    return render(request, 'core/manage_bookings.html', {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'bookings_data': bookings_data,
        'active_bookings_data': active_bookings_data,
        'recent_bookings_data': recent_bookings_data,
        'view': view,
        'f_aircraft': f_aircraft, 'f_instructor': f_instructor,
        'f_status': f_status, 'f_date_from': f_date_from, 'f_date_to': f_date_to,
        'aircraft_list': aircraft_list, 'instructors': instructors, 'members_qs': members_qs,
        'url_active': _toggle_url('active'),
        'url_all': _toggle_url('all'),
        'using_default_window': using_default_window,
    })


@login_required
@transaction.atomic
def booking_detail(request, club_slug, booking_id):
    from .models import (FlightCompletion, FlightChargeItem, FlightLandingEntry,
                         AccountTransaction, FuelSurchargeRate, ChargeRate, Aerodrome)
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not (actor.is_admin or actor.is_instructor):
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    booking = get_object_or_404(Booking, club=club, id=booking_id)
    is_own_booking = (booking.member.user == request.user)
    # Members can view and depart their own bookings; all other actions require staff
    if not (actor.is_admin or actor.is_instructor or is_own_booking):
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    error = None
    success = None

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'confirm' and booking.status == 'pending':
            booking.status = 'confirmed'
            booking.confirmed_by = request.user
            booking.confirmed_at = timezone.now()
            booking.save()
            _audit(booking, request.user, 'confirmed')
            from .services import notification_service
            notification_service.notify_booking_confirmed(booking)
            from .email_notifications import booking_confirmed as _email_confirmed
            _email_confirmed(booking)
            success = 'Booking confirmed.'

        elif action == 'cancel_booking' and booking.status in ('pending', 'confirmed'):
            reason = request.POST.get('cancellation_reason', 'no_longer_required')
            reason_other = request.POST.get('cancellation_reason_other', '')
            result = booking_service.cancel(
                booking, request.user,
                reason=reason,
                reason_other=reason_other,
            )
            if result.ok:
                _audit(booking, request.user, 'cancelled')
                from .email_notifications import booking_cancelled as _email_cancelled
                _email_cancelled(booking, reason=reason)
                success = 'Booking cancelled.'
            else:
                error = result.error

        elif action == 'depart' and booking.status == 'confirmed':
            no_decl_reason = request.POST.get('no_declaration_reason', '').strip()
            elig_override_reason = request.POST.get('eligibility_override_reason', '').strip()
            requires_decl = booking.flight_type.requires_declaration
            has_decl = hasattr(booking, 'declaration') and not booking.declaration.is_draft
            # Run eligibility to check for blocks (POST-side validation mirrors the GET-side display)
            _elig = qualification_service.check_eligibility(booking) if booking.member else None
            has_elig_blocks = _elig.has_blocks if _elig else False
            if requires_decl and not has_decl and not no_decl_reason:
                error = 'This flight type requires a departure declaration. Provide a reason to override.'
            elif has_elig_blocks and not elig_override_reason:
                error = 'One or more compliance checks failed. Provide an override reason to proceed.'
            else:
                booking.status = 'departed'
                booking.departed_at = timezone.now()
                if requires_decl and not has_decl:
                    booking.departed_without_declaration = True
                    booking.departed_without_declaration_reason = no_decl_reason
                # Snapshot fuel rate at departure
                fuel_rate = FuelSurchargeRate.current_rate(club, booking.aircraft)
                booking.save()
                # Store snapshot on a pending FlightCompletion stub
                FlightCompletion.objects.get_or_create(
                    booking=booking,
                    defaults={
                        'logged_by': request.user,
                        'fuel_surcharge_rate_snapshot': fuel_rate.rate if fuel_rate else None,
                        'departed_with_aircraft': booking.aircraft,
                        'departed_with_instructor': booking.instructor,
                    }
                )
                audit_notes = f'Compliance override: {elig_override_reason}' if elig_override_reason else None
                _audit(booking, request.user, 'departed', notes=audit_notes)
                success = 'Checked out.'

        elif action == 'undo_depart' and booking.status == 'departed' and actor.is_admin:
            fc = getattr(booking, 'flight_completion', None)
            # Delete the FC shell created at departure (fuel rate snapshot only, no charges yet)
            if fc and (fc.amount_paid or 0) > 0:
                error = 'Cannot undo departure — a payment is recorded against this flight.'
            elif fc and fc.charge_items.exists():
                error = 'Cannot undo departure — charge items exist. Use void check-in instead.'
            else:
                if fc:
                    fc.delete()
                booking.status = 'confirmed'
                booking.departed_at = None
                booking.departed_without_declaration = False
                booking.departed_without_declaration_reason = ''
                booking.save(update_fields=['status', 'departed_at',
                                            'departed_without_declaration',
                                            'departed_without_declaration_reason'])
                _audit(booking, request.user, 'undo_depart')
                success = 'Departure undone — flight is back to confirmed.'

        elif action == 'change_details' and booking.status in ('pending', 'confirmed') and actor.is_admin:
            new_instr_id = request.POST.get('instructor_id', '').strip()
            new_ac_id    = request.POST.get('aircraft_id', '').strip()
            new_desc     = request.POST.get('description', booking.description or '').strip()
            changed = []
            if new_instr_id == '__none__':
                booking.instructor = None
                changed.append('instructor')
            elif new_instr_id:
                from .models import User as _U
                new_instr = _U.objects.filter(id=new_instr_id).first()
                if new_instr and new_instr != booking.instructor:
                    booking.instructor = new_instr
                    changed.append('instructor')
            if new_ac_id:
                new_ac = Aircraft.objects.filter(club=club, id=new_ac_id).first()
                if new_ac and new_ac != booking.aircraft:
                    booking.aircraft = new_ac
                    changed.append('aircraft')
            if new_desc != (booking.description or ''):
                booking.description = new_desc
                changed.append('description')
            if changed:
                booking.save(update_fields=['instructor', 'aircraft', 'description'])
                _audit(booking, request.user, 'edited', notes=', '.join(changed) + ' updated')
                success = 'Booking updated.'

        elif action == 'undo_confirm' and booking.status == 'confirmed' and actor.is_admin:
            booking.status = 'pending'
            booking.confirmed_by = None
            booking.confirmed_at = None
            booking.save(update_fields=['status', 'confirmed_by', 'confirmed_at'])
            _audit(booking, request.user, 'undo_confirm')
            success = 'Confirmation undone — flight is back to pending.'

        elif action == 'checkin' and booking.status == 'departed':
            outcome = request.POST.get('outcome', 'completed')
            outcome_notes = request.POST.get('outcome_notes', '').strip()
            hobbs_start      = request.POST.get('hobbs_start', '').strip() or None
            hobbs_end        = request.POST.get('hobbs_end', '').strip() or None
            tacho_start      = request.POST.get('tacho_start', '').strip() or None
            tacho_end        = request.POST.get('tacho_end', '').strip() or None
            airswitch_start  = request.POST.get('airswitch_start', '').strip() or None
            airswitch_end    = request.POST.get('airswitch_end', '').strip() or None
            new_ft_id = request.POST.get('flight_type_id', '').strip()
            gap_explanation = request.POST.get('gap_explanation', '').strip()

            ac = booking.aircraft
            if ac.records_hobbs and (not hobbs_start or not hobbs_end):
                error = 'Hobbs start and end are required for this aircraft.'
            elif ac.records_tacho and (not tacho_start or not tacho_end):
                error = 'Tacho start and end are required for this aircraft.'
            elif ac.records_airswitch and (not airswitch_start or not airswitch_end):
                error = 'Air switch start and end are required for this aircraft.'
            elif hobbs_start and hobbs_end:
                try:
                    if float(hobbs_end) <= float(hobbs_start):
                        error = 'Hobbs end must be greater than start.'
                except ValueError:
                    error = 'Invalid Hobbs reading.'
            elif tacho_start and tacho_end:
                try:
                    if float(tacho_end) <= float(tacho_start):
                        error = 'Tacho end must be greater than start.'
                except ValueError:
                    error = 'Invalid Tacho reading.'
            elif airswitch_start and airswitch_end:
                try:
                    if float(airswitch_end) <= float(airswitch_start):
                        error = 'Air switch end must be greater than start.'
                except ValueError:
                    error = 'Invalid air switch reading.'

            # Gap detection: compare submitted start against the last recorded end for this aircraft
            if not error:
                from django.db.models import Q as _Q
                prev_fc = (FlightCompletion.objects
                           .filter(booking__aircraft=ac, booking__club=club)
                           .exclude(booking=booking)
                           .filter(_Q(hobbs_end__isnull=False) | _Q(tacho_end__isnull=False) | _Q(airswitch_end__isnull=False))
                           .order_by('-booking__arrived_at', '-created_at')
                           .first())
                _gap_detected = False
                _gap_label = ''
                if prev_fc:
                    try:
                        if hobbs_start and prev_fc.hobbs_end is not None:
                            gap_h = float(hobbs_start) - float(prev_fc.hobbs_end)
                            if gap_h > 0.05:
                                _gap_detected = True
                                _gap_label = (f'Hobbs start {hobbs_start} is {gap_h:.1f}h ahead of '
                                              f'last recorded end ({prev_fc.hobbs_end})')
                        if not _gap_detected and tacho_start and prev_fc.tacho_end is not None:
                            gap_t = float(tacho_start) - float(prev_fc.tacho_end)
                            if gap_t > 0.005:
                                _gap_detected = True
                                _gap_label = (f'Tacho start {tacho_start} is {gap_t:.2f} ahead of '
                                              f'last recorded end ({prev_fc.tacho_end})')
                        if not _gap_detected and airswitch_start and prev_fc.airswitch_end is not None:
                            gap_a = float(airswitch_start) - float(prev_fc.airswitch_end)
                            if gap_a > 0.05:
                                _gap_detected = True
                                _gap_label = (f'Air switch start {airswitch_start} is {gap_a:.1f}h ahead of '
                                              f'last recorded end ({prev_fc.airswitch_end})')
                    except (TypeError, ValueError):
                        pass
                if _gap_detected and not gap_explanation:
                    error = f'Meter gap detected — {_gap_label}. An explanation is required (see warning below).'

            if not error:
                fc, _ = FlightCompletion.objects.get_or_create(
                    booking=booking, defaults={'logged_by': request.user}
                )
                fc.outcome = outcome
                fc.outcome_notes = outcome_notes
                fc.hobbs_start     = hobbs_start
                fc.hobbs_end       = hobbs_end
                fc.tacho_start     = tacho_start
                fc.tacho_end       = tacho_end
                fc.airswitch_start = airswitch_start
                fc.airswitch_end   = airswitch_end
                fc.logged_by = request.user

                method = booking.aircraft.total_time_method
                try:
                    if method == 'hobbs' and hobbs_start and hobbs_end:
                        fc.actual_flight_hours = float(hobbs_end) - float(hobbs_start)
                    elif method in ('tacho', 'tacho_less_5') and tacho_start and tacho_end:
                        # tacho_less_5 is a maintenance-only concept; billing always uses full tacho hours
                        fc.actual_flight_hours = round(float(tacho_end) - float(tacho_start), 2)
                    elif method == 'airswitch' and airswitch_start and airswitch_end:
                        fc.actual_flight_hours = float(airswitch_end) - float(airswitch_start)
                except (ValueError, TypeError):
                    pass

                if booking.instructor:
                    instr_member = ClubMember.objects.filter(user=booking.instructor, club=club).first()
                    if instr_member and instr_member.instructor_grade:
                        fc.instructor_rate_snapshot = instr_member.instructor_grade.hourly_rate

                if new_ft_id:
                    new_ft = FlightType.objects.filter(club=club, id=new_ft_id).first()
                    if new_ft and new_ft != booking.flight_type:
                        fc.original_flight_type = booking.flight_type
                        booking.flight_type = new_ft
                        booking.save(update_fields=['flight_type'])

                if gap_explanation:
                    fc.meter_gap_note = gap_explanation
                fc.save()
                create_maint_log_entry(fc)
                for _mi in booking.aircraft.maintenance_items.all():
                    _mi.recalc_urgency()
                    _mi.save(update_fields=['urgency'])
                booking.status = 'completed'
                booking.arrived_at = timezone.now()
                booking.save(update_fields=['status', 'arrived_at'])

                # Split flight segments
                from .models import FlightSegment as _FS
                _segments = []
                if request.POST.get('has_split') == '1':
                    _h1   = request.POST.get('seg_hobbs_h1',     '').strip() or None
                    _t1   = request.POST.get('seg_tacho_h1',     '').strip() or None
                    _a1   = request.POST.get('seg_airswitch_h1', '').strip() or None
                    _m2id = request.POST.get('seg_member_2',     '').strip()
                    _m2   = ClubMember.objects.filter(club=club, id=_m2id).first()
                    _meth = booking.aircraft.total_time_method
                    if _m2 and (_h1 or _t1 or _a1):
                        _seg1 = _FS.objects.create(
                            flight_completion=fc, member=booking.member, sequence=1,
                            hobbs_start=fc.hobbs_start, hobbs_end=_h1,
                            tacho_start=fc.tacho_start, tacho_end=_t1,
                            airswitch_start=fc.airswitch_start, airswitch_end=_a1,
                            hours=_calc_segment_hours(_meth, fc.hobbs_start, _h1, fc.tacho_start, _t1, fc.airswitch_start, _a1),
                        )
                        _seg2 = _FS.objects.create(
                            flight_completion=fc, member=_m2, sequence=2,
                            hobbs_start=_h1, hobbs_end=fc.hobbs_end,
                            tacho_start=_t1, tacho_end=fc.tacho_end,
                            airswitch_start=_a1, airswitch_end=fc.airswitch_end,
                            hours=_calc_segment_hours(_meth, _h1, fc.hobbs_end, _t1, fc.tacho_end, _a1, fc.airswitch_end),
                        )
                        _segments = [_seg1, _seg2]

                hours = fc.actual_flight_hours
                if _segments:
                    _generate_segment_charges(fc, _segments, booking)
                else:
                    hire_rate = ChargeRate.objects.filter(
                        aircraft=booking.aircraft, flight_type=booking.flight_type,
                        time_method=booking.aircraft.total_time_method
                    ).first()
                    if hire_rate and hours:
                        FlightChargeItem.objects.get_or_create(
                            flight_completion=fc, item_type='hire',
                            defaults={'description': f'Aircraft hire — {booking.aircraft.registration}',
                                      'amount': round(float(hire_rate.amount) * float(hours), 2)}
                        )
                    if fc.fuel_surcharge_rate_snapshot and hours and not (hire_rate and hire_rate.includes_fuel) and fc.fuel_surcharge_rate_snapshot > 0:
                        FlightChargeItem.objects.get_or_create(
                            flight_completion=fc, item_type='fuel',
                            defaults={'description': 'Fuel charge',
                                      'amount': round(float(fc.fuel_surcharge_rate_snapshot) * float(hours), 2)}
                        )
                    if fc.instructor_rate_snapshot and hours and booking.instructor:
                        FlightChargeItem.objects.get_or_create(
                            flight_completion=fc, item_type='instructor',
                            defaults={'description': f'Instructor fee — {booking.instructor.get_full_name()}',
                                      'amount': round(float(fc.instructor_rate_snapshot) * float(hours), 2)}
                        )
                    for sc in booking.aircraft.surcharges.all():
                        FlightChargeItem.objects.get_or_create(
                            flight_completion=fc, item_type='surcharge',
                            defaults={'description': sc.name, 'amount': sc.amount}
                        )

                _update_total(fc)
                _audit(booking, request.user, 'completed')
                success = 'Flight checked in.'

        elif action == 'edit_checkin' and booking.status == 'completed' and actor.is_admin:
            fc = getattr(booking, 'flight_completion', None)
            if fc:
                hobbs_start      = request.POST.get('hobbs_start', '').strip() or None
                hobbs_end        = request.POST.get('hobbs_end', '').strip() or None
                tacho_start      = request.POST.get('tacho_start', '').strip() or None
                tacho_end        = request.POST.get('tacho_end', '').strip() or None
                airswitch_start  = request.POST.get('airswitch_start', '').strip() or None
                airswitch_end    = request.POST.get('airswitch_end', '').strip() or None
                outcome          = request.POST.get('outcome', fc.outcome)
                outcome_notes    = request.POST.get('outcome_notes', fc.outcome_notes or '').strip()
                ac = booking.aircraft
                if ac.records_hobbs and (not hobbs_start or not hobbs_end):
                    error = 'Hobbs start and end are required for this aircraft.'
                if not error and ac.records_tacho and (not tacho_start or not tacho_end):
                    error = 'Tacho start and end are required for this aircraft.'
                if not error and ac.records_airswitch and (not airswitch_start or not airswitch_end):
                    error = 'Air switch start and end are required for this aircraft.'
                if not error:
                    try:
                        if hobbs_start and hobbs_end and float(hobbs_end) <= float(hobbs_start):
                            error = 'Hobbs end must be greater than start.'
                        if tacho_start and tacho_end and float(tacho_end) <= float(tacho_start):
                            error = 'Tacho end must be greater than start.'
                        if airswitch_start and airswitch_end and float(airswitch_end) <= float(airswitch_start):
                            error = 'Air switch end must be greater than start.'
                    except ValueError:
                        error = 'Invalid meter reading.'
                if not error:
                    fc.hobbs_start      = hobbs_start
                    fc.hobbs_end        = hobbs_end
                    fc.tacho_start      = tacho_start
                    fc.tacho_end        = tacho_end
                    fc.airswitch_start  = airswitch_start
                    fc.airswitch_end    = airswitch_end
                    fc.outcome          = outcome
                    fc.outcome_notes    = outcome_notes
                    method = ac.total_time_method
                    try:
                        if method == 'hobbs' and hobbs_start and hobbs_end:
                            fc.actual_flight_hours = round(float(hobbs_end) - float(hobbs_start), 2)
                        elif method in ('tacho', 'tacho_less_5') and tacho_start and tacho_end:
                            fc.actual_flight_hours = round(float(tacho_end) - float(tacho_start), 2)
                        elif method == 'airswitch' and airswitch_start and airswitch_end:
                            fc.actual_flight_hours = round(float(airswitch_end) - float(airswitch_start), 2)
                    except (ValueError, TypeError):
                        pass
                    fc.save()
                    # Rebuild auto-generated charges; preserve manual/landing/one-off items
                    fc.charge_items.filter(
                        item_type__in=['hire', 'fuel', 'instructor', 'surcharge']
                    ).delete()
                    hours = fc.actual_flight_hours
                    hire_rate = ChargeRate.objects.filter(
                        aircraft=ac, flight_type=booking.flight_type,
                        time_method=ac.total_time_method
                    ).first()
                    if hire_rate and hours:
                        FlightChargeItem.objects.create(
                            flight_completion=fc, item_type='hire',
                            description=f'Aircraft hire — {ac.registration}',
                            amount=round(float(hire_rate.amount) * float(hours), 2),
                        )
                    if fc.fuel_surcharge_rate_snapshot and hours and not (hire_rate and hire_rate.includes_fuel) and fc.fuel_surcharge_rate_snapshot > 0:
                        FlightChargeItem.objects.create(
                            flight_completion=fc, item_type='fuel',
                            description='Fuel charge',
                            amount=round(float(fc.fuel_surcharge_rate_snapshot) * float(hours), 2),
                        )
                    if fc.instructor_rate_snapshot and hours and booking.instructor:
                        FlightChargeItem.objects.create(
                            flight_completion=fc, item_type='instructor',
                            description=f'Instructor fee — {booking.instructor.get_full_name()}',
                            amount=round(float(fc.instructor_rate_snapshot) * float(hours), 2),
                        )
                    for sc in ac.surcharges.all():
                        FlightChargeItem.objects.create(
                            flight_completion=fc, item_type='surcharge',
                            description=sc.name, amount=sc.amount,
                        )
                    _update_total(fc)
                    for _mi in booking.aircraft.maintenance_items.all():
                        _mi.recalc_urgency()
                        _mi.save(update_fields=['urgency'])
                    _audit(booking, request.user, 'edit_checkin')
                    success = 'Check-in details updated. Review charges and payment below.'

        elif action == 'set_client' and actor.can_access_manage:
            from .models import Contact as _Cont
            cid = request.POST.get('client_id', '').strip()
            booking.client = _Cont.objects.filter(id=cid, club=club).first() if cid else None
            booking.billed_to = request.POST.get('billed_to', '')
            booking.save(update_fields=['client', 'billed_to'])
            return redirect(request.path + ('?inline=1' if is_inline else ''))

        elif action == 'add_charge' and booking.status == 'completed':
            fc = getattr(booking, 'flight_completion', None)
            if fc:
                item_type = request.POST.get('item_type', 'one_off')
                description = request.POST.get('description', '').strip()
                amount = request.POST.get('amount', '').strip()
                ae_id  = request.POST.get('aerodrome_id', '')
                ft_id  = request.POST.get('fee_type_id', '')
                from .models import AerodromeFeeType
                ae = Aerodrome.objects.filter(club=club, id=ae_id).first() if ae_id else None
                fee_type = AerodromeFeeType.objects.filter(id=ft_id).first() if ft_id else None
                result = charging_service.add_charge(
                    fc, item_type, description, amount,
                    aerodrome=ae, fee_type=fee_type,
                    custom_icao=request.POST.get('custom_icao', '').strip(),
                    custom_name=request.POST.get('custom_name', '').strip(),
                    quantity=int(request.POST.get('quantity', 1) or 1),
                    unit_amount=request.POST.get('unit_amount', amount) or amount,
                )
                if result.ok:
                    success = 'Charge added.'
                else:
                    error = result.error

        elif action == 'delete_charge' and booking.status == 'completed':
            fc = getattr(booking, 'flight_completion', None)
            if fc:
                result = charging_service.delete_charge(fc, request.POST.get('item_id'))
                success = 'Charge removed.' if result.ok else ''

        elif action == 'confirm_payment' and booking.status == 'completed':
            # Quick single-payment record (default to booking member)
            fc = getattr(booking, 'flight_completion', None)
            if fc and not fc.is_paid:
                amount_str = request.POST.get('payment_amount', '').strip()
                pay_amount = amount_str if amount_str else str(fc.balance_owing)
                result = charging_service.record_payment(
                    fc, booking, request.user, pay_amount,
                    method=request.POST.get('payment_method', 'eftpos'),
                )
                if result.ok:
                    success = result.data['message']
                else:
                    error = result.error

        elif action == 'add_payee' and booking.status == 'completed':
            fc = getattr(booking, 'flight_completion', None)
            if fc:
                from .models import ClubMember as _CM
                member_id = request.POST.get('member_id', '').strip()
                amount_str = request.POST.get('payment_amount', '').strip()
                method = request.POST.get('payment_method', 'eftpos')
                try:
                    payee = _CM.objects.get(id=member_id, club=club)
                except _CM.DoesNotExist:
                    error = 'Member not found'
                else:
                    result = charging_service.allocate_payment(
                        fc, booking, request.user, amount_str, method=method, member=payee
                    )
                    if result.ok:
                        success = 'Payee added.'
                    else:
                        error = result.error

        elif action == 'record_payee' and booking.status == 'completed':
            fc = getattr(booking, 'flight_completion', None)
            if fc:
                payment_id = request.POST.get('payment_id', '').strip()
                result = charging_service.record_allocated_payment(
                    fc, booking, request.user, payment_id
                )
                if result.ok:
                    success = result.data['message']
                else:
                    error = result.error

        elif action == 'remove_payee' and booking.status == 'completed':
            fc = getattr(booking, 'flight_completion', None)
            if fc and actor.is_admin:
                payment_id = request.POST.get('payment_id', '').strip()
                result = charging_service.remove_payment_allocation(fc, payment_id)
                if result.ok:
                    success = 'Payee removed.'
                else:
                    error = result.error

        elif action == 'record_multi_payment' and booking.status == 'completed':
            fc = getattr(booking, 'flight_completion', None)
            if fc:
                from decimal import Decimal as _D, InvalidOperation
                method = request.POST.get('payment_method', 'eftpos')
                received_str = request.POST.get('amount_received', '').strip()
                try:
                    received = _D(received_str)
                except InvalidOperation:
                    error = 'Invalid amount received'
                else:
                    # Arrears clearance — separate from flight amounts
                    try:
                        arrears_clear_amt = _D(request.POST.get('arrears_amount', '0') or '0')
                    except InvalidOperation:
                        arrears_clear_amt = _D('0')
                    include_arrears = bool(
                        request.POST.get('include_arrears') and arrears_clear_amt > 0
                    )
                    received_for_flights = received - arrears_clear_amt if include_arrears else received

                    # Build ordered list: current flight first, then selected others by date
                    fc_amounts = [(fc, booking, fc.balance_owing)]
                    selected_ids = request.POST.getlist('other_fc_ids')
                    if selected_ids:
                        _other_fcs = (FlightCompletion.objects
                                      .filter(id__in=selected_ids,
                                              booking__member=booking.member,
                                              booking__club=club)
                                      .select_related('booking__aircraft', 'booking')
                                      .order_by('booking__scheduled_start'))
                        for ofc in _other_fcs:
                            amt_str = request.POST.get(f'other_amount_{ofc.id}', '').strip()
                            try:
                                amt = _D(amt_str)
                            except InvalidOperation:
                                amt = ofc.balance_owing
                            fc_amounts.append((ofc, ofc.booking, amt))
                    result = charging_service.record_multi_payment(
                        fc, booking, request.user, method, fc_amounts, received_for_flights
                    )
                    if result.ok:
                        if include_arrears:
                            from .models import AccountTransaction as _AT
                            try:
                                _acct2 = booking.member.account
                                _AT.objects.create(
                                    account=_acct2,
                                    transaction_type='deposit',
                                    direction='credit',
                                    amount=arrears_clear_amt,
                                    payment_method=method,
                                    description='Arrears clearance — collected with flight payment',
                                    created_by=request.user,
                                )
                                _acct2.apply_transaction(arrears_clear_amt, 'credit')
                            except Exception:
                                pass
                        success = result.data['message']
                    else:
                        error = result.error

        elif action == 'void_checkin' and booking.status == 'completed' and actor.is_admin:
            fc = getattr(booking, 'flight_completion', None)
            if fc:
                if fc.amount_paid and fc.amount_paid > 0:
                    error = 'Cannot void check-in while a payment is recorded. Reverse the payment first.'
                else:
                    # Clear all charge items and meter data, reset status to departed
                    fc.charge_items.all().delete()
                    fc.hobbs_start = fc.hobbs_end = None
                    fc.tacho_start = fc.tacho_end = None
                    fc.airswitch_start = fc.airswitch_end = None
                    fc.actual_flight_hours = None
                    fc.outcome = 'completed'
                    fc.outcome_notes = ''
                    fc.total_charge = 0
                    fc.meter_gap_note = ''
                    fc.save()
                    booking.status = 'departed'
                    booking.arrived_at = None
                    booking.save(update_fields=['status', 'arrived_at'])
                    _audit(booking, request.user, 'void_checkin')
                    success = 'Check-in voided — flight is back to departed.'

        elif action == 'reverse_payment' and booking.status == 'completed' and actor.is_admin:
            fc = getattr(booking, 'flight_completion', None)
            if fc:
                payment_id = request.POST.get('payment_id') or None
                result = charging_service.reverse_payment(fc, booking, request.user, payment_id=payment_id)
                if result.ok:
                    success = result.data['message']
                else:
                    error = result.error

        elif action == 'record_refund' and booking.status == 'completed' and actor.is_admin:
            fc = getattr(booking, 'flight_completion', None)
            if fc:
                result = charging_service.record_refund(
                    fc, booking, request.user,
                    amount=request.POST.get('refund_amount', '').strip(),
                    method=request.POST.get('refund_method', 'eftpos'),
                )
                if result.ok:
                    success = result.data['message']
                else:
                    error = result.error

        is_inline = request.POST.get('inline') == '1' or request.GET.get('inline') == '1'
        if error:
            pass  # fall through to re-render with error
        elif success and not error:
            if is_inline:
                return redirect(f'{request.path}?inline=1')
            return redirect('core:booking_detail', club_slug=club_slug, booking_id=booking_id)

    is_inline = request.GET.get('inline') == '1'

    # GET — build context
    fc = getattr(booking, 'flight_completion', None)
    charge_items = fc.charge_items.select_related('segment').all() if fc else []

    # Split-flight segments with charge items pre-grouped
    fc_segments = []
    if fc:
        _segs = list(fc.segments.select_related('member__user').order_by('sequence'))
        if _segs:
            from collections import defaultdict as _dd
            _by_seg = _dd(list)
            for _ci in charge_items:
                if _ci.segment_id:
                    _by_seg[_ci.segment_id].append(_ci)
            for _s in _segs:
                _s.segment_charges = _by_seg.get(_s.id, [])
            fc_segments = _segs
            charge_items = [_ci for _ci in charge_items if not _ci.segment_id]
    total = fc.total_charge if fc else 0
    balance_owing = fc.balance_owing if fc else 0
    from decimal import Decimal as _D
    overpayment = max(_D('0'), (_D(str(fc.amount_paid or 0)) - _D(str(fc.total_charge or 0)))) if fc else _D('0')
    fc_payments = list(fc.payments.select_related('member__user').order_by('created_at')) if fc else []
    club_members = list(ClubMember.objects.filter(club=club).exclude(standing='resigned').select_related('user').order_by('user__last_name', 'user__first_name'))
    from .models import Contact as _Cont
    contacts = list(_Cont.objects.filter(club=club, converted_to_member__isnull=True).order_by('name'))

    # Other unpaid/partially-paid flights for this member (for payment warning)
    if fc:
        _base = (FlightCompletion.objects
                 .filter(booking__member=booking.member, booking__club=club)
                 .exclude(booking=booking)
                 .select_related('booking__aircraft', 'booking'))
        other_unpaid_list = list(
            _base.filter(paid_at__isnull=True, total_charge__gt=0)
        )
        other_outstanding_list = list(
            _base.filter(paid_at__isnull=False).extra(where=['amount_paid < total_charge'])
        )
    else:
        other_unpaid_list = []
        other_outstanding_list = []

    other_unpaid_total = sum((_D(str(x.total_charge)) for x in other_unpaid_list), _D('0'))
    other_outstanding_total = sum((_D(str(x.balance_owing)) for x in other_outstanding_list), _D('0'))
    other_total = other_unpaid_total + other_outstanding_total
    other_total_count = len(other_unpaid_list) + len(other_outstanding_list)
    aerodromes = Aerodrome.objects.filter(club=club, is_active=True).prefetch_related('fee_types')
    flight_types = FlightType.objects.filter(club=club)
    requires_decl = booking.flight_type.requires_declaration
    has_submitted_decl = (hasattr(booking, 'declaration') and
                          not booking.declaration.is_draft
                          if requires_decl else False)

    # Eligibility check — shown in the depart section so staff can see issues before letting a member fly
    eligibility = None
    if booking.status in ('confirmed', 'pending') and booking.member:
        eligibility = qualification_service.check_eligibility(booking)

    # Previous meter readings for this aircraft — used by the gap-detection JS in the check-in form
    from django.db.models import Q as _Q
    _prev_fc = (FlightCompletion.objects
                .filter(booking__aircraft=booking.aircraft, booking__club=club)
                .exclude(booking=booking)
                .filter(_Q(hobbs_end__isnull=False) | _Q(tacho_end__isnull=False) | _Q(airswitch_end__isnull=False))
                .order_by('-booking__arrived_at', '-created_at')
                .first()) if booking.status == 'departed' else None
    prev_hobbs_end     = float(_prev_fc.hobbs_end)     if _prev_fc and _prev_fc.hobbs_end     is not None else None
    prev_tacho_end     = float(_prev_fc.tacho_end)     if _prev_fc and _prev_fc.tacho_end     is not None else None
    prev_airswitch_end = float(_prev_fc.airswitch_end) if _prev_fc and _prev_fc.airswitch_end is not None else None

    # Rate data for the check-in JS charge preview
    import json as _json
    checkin_rates_json = 'null'
    if booking.status == 'departed':
        _hire = ChargeRate.objects.filter(
            aircraft=booking.aircraft, flight_type=booking.flight_type,
            time_method=booking.aircraft.total_time_method
        ).first()
        _instr_rate = None
        if booking.instructor:
            _im = ClubMember.objects.filter(user=booking.instructor, club=club).first()
            if _im and _im.instructor_grade:
                _instr_rate = float(_im.instructor_grade.hourly_rate)
        _fuel_snap = float(fc.fuel_surcharge_rate_snapshot) if fc and fc.fuel_surcharge_rate_snapshot else None
        _surcharge_list = [
            {'name': sc.name, 'amount': float(sc.amount)}
            for sc in booking.aircraft.surcharges.all()
        ]
        checkin_rates_json = _json.dumps({
            'time_method':    booking.aircraft.total_time_method,
            'hire_rate':      float(_hire.amount) if _hire else None,
            'includes_fuel':  _hire.includes_fuel if _hire else False,
            'fuel_rate':      _fuel_snap,
            'instructor_rate': _instr_rate,
            'surcharges':     _surcharge_list,
        })

    try:
        _acct = booking.member.account
    except Exception:
        _acct = None
    _arrears_clearable = (
        _acct and _acct.balance < 0 and _acct.has_warning
    )

    ctx = {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'booking': booking,
        'member_account': _acct,
        'arrears_clearable': _arrears_clearable,
        'fc': fc,
        'fc_segments': fc_segments,
        'charge_items': charge_items,
        'contacts': contacts,
        'total': total,
        'balance_owing': balance_owing,
        'overpayment': overpayment,
        'fc_payments': fc_payments,
        'club_members': club_members,
        'other_unpaid_list': other_unpaid_list,
        'other_outstanding_list': other_outstanding_list,
        'other_unpaid_total': other_unpaid_total,
        'other_outstanding_total': other_outstanding_total,
        'other_total': other_total,
        'other_total_count': other_total_count,
        'aerodromes': aerodromes,
        'flight_types': flight_types,
        'requires_decl': requires_decl,
        'has_submitted_decl': has_submitted_decl,
        'eligibility': eligibility,
        'error': error,
        'success': success,
        'prev_hobbs_end': prev_hobbs_end,
        'prev_tacho_end': prev_tacho_end,
        'prev_airswitch_end': prev_airswitch_end,
        'checkin_rates_json': checkin_rates_json,
        'rostered_instructors': ClubMember.objects.filter(club=club, is_on_instructor_roster=True).select_related('user').order_by('user__last_name'),
        'online_aircraft': Aircraft.objects.filter(club=club, status='online').order_by('registration'),
        'base_template': 'core/base_inline.html' if is_inline else 'core/base.html',
        'inline_title': f'Manage <span class="crumb-sep">›</span> Bookings <span class="crumb-sep">›</span> <span class="crumb-cur">{booking.member.user.get_full_name()} · {booking.aircraft.registration}</span>',
    }
    return render(request, 'core/booking_detail.html', ctx)


def _inline_redirect(request, view_name, saved=False, error='', **kwargs):
    """Redirect back to the same page, preserving ?inline=1 and optionally ?saved=1 or ?err=..."""
    from django.urls import reverse
    url = reverse(view_name, kwargs=kwargs)
    params = []
    if request.GET.get('inline') == '1':
        params.append('inline=1')
    if saved:
        params.append('saved=1')
    if error:
        from urllib.parse import quote
        params.append(f'err={quote(error)}')
    if params:
        url += '?' + '&'.join(params)
    return redirect(url)


def _update_total(fc):
    """Thin wrapper — logic lives in booking_service.update_total."""
    booking_service.update_total(fc)


def _calc_segment_hours(method, hobbs_s, hobbs_e, tacho_s, tacho_e, air_s, air_e):
    from decimal import Decimal as _D
    def _d(v):
        try: return _D(str(v)) if v else None
        except Exception: return None
    try:
        if method == 'hobbs':
            s, e = _d(hobbs_s), _d(hobbs_e)
            if s and e and e > s: return round(e - s, 2)
        elif method in ('tacho', 'tacho_less_5'):
            s, e = _d(tacho_s), _d(tacho_e)
            if s and e and e > s:
                h = e - s
                return round(h * _D('0.95'), 2) if method == 'tacho_less_5' else round(h, 2)
        elif method == 'airswitch':
            s, e = _d(air_s), _d(air_e)
            if s and e and e > s: return round(e - s, 2)
    except Exception:
        pass
    return _D('0')


def _generate_segment_charges(fc, segments, booking, config=None):
    from decimal import Decimal as _D
    from .models import FlightChargeItem as _FCI, ChargeRate
    ac = booking.aircraft
    hire_rate = ChargeRate.objects.filter(
        aircraft=ac, flight_type=booking.flight_type,
        time_method=ac.total_time_method
    ).first()
    total_hours = sum(s.hours for s in segments) or _D('0')

    for seg in segments:
        h = seg.hours
        if not h:
            continue
        if hire_rate:
            _FCI.objects.create(
                flight_completion=fc, segment=seg, item_type='hire',
                description=f'Aircraft hire — {ac.registration} ({seg.member.user.get_full_name()})',
                amount=round(float(hire_rate.amount) * float(h), 2),
            )
        if fc.fuel_surcharge_rate_snapshot and not (hire_rate and hire_rate.includes_fuel):
            _FCI.objects.create(
                flight_completion=fc, segment=seg, item_type='fuel',
                description=f'Fuel levy ({seg.member.user.get_full_name()})',
                amount=round(float(fc.fuel_surcharge_rate_snapshot) * float(h), 2),
            )

    # Instructor fee — applied to non-instructor segments, weighted by hours
    if fc.instructor_rate_snapshot and booking.instructor and total_hours:
        instr_user = booking.instructor
        student_segs = [s for s in segments if s.member.user_id != instr_user.pk]
        student_total = sum(s.hours for s in student_segs) or _D('0')
        if student_total:
            for seg in student_segs:
                amt = round(float(fc.instructor_rate_snapshot) * float(total_hours) * float(seg.hours) / float(student_total), 2)
                if amt > 0:
                    _FCI.objects.create(
                        flight_completion=fc, segment=seg, item_type='instructor',
                        description=f'Instructor fee — {booking.instructor.get_full_name()} ({seg.member.user.get_full_name()})',
                        amount=amt,
                    )

    # Flat surcharges — split proportionally, last segment absorbs rounding
    if total_hours:
        for sc in ac.surcharges.all():
            amounts = [round(float(sc.amount) * float(s.hours) / float(total_hours), 2) for s in segments]
            amounts[-1] = round(float(sc.amount) - sum(amounts[:-1]), 2)
            for seg, amt in zip(segments, amounts):
                if amt > 0:
                    _FCI.objects.create(
                        flight_completion=fc, segment=seg, item_type='surcharge',
                        description=f'{sc.name} ({seg.member.user.get_full_name()})',
                        amount=amt,
                    )


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
        _saved = action in ('save_membership',)
        if action == 'avatar_upload':
            if request.FILES.get('avatar'):
                member.avatar = request.FILES['avatar']
                member.save(update_fields=['avatar'])
            return _inline_redirect(request, 'core:manage_member_detail', club_slug=club_slug, member_id=member_id)
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
            dob = request.POST.get('date_of_birth', '').strip()
            member.date_of_birth = dob or None
            member.sex = request.POST.get('sex', '').strip()
            member.next_of_kin_name  = request.POST.get('next_of_kin_name', '').strip()
            member.next_of_kin_phone = request.POST.get('next_of_kin_phone', '').strip()
            member.save()
            return _inline_redirect(request, 'core:manage_member_detail',
                                    club_slug=club_slug, member_id=member_id, saved=True)
        elif action == 'save_membership' and actor.is_admin:
            _old_standing = member.standing
            _old_role = member.role
            _old_sub_exp = member.subscription_expires
            _old_cat = member.membership_category

            standing = request.POST.get('standing')
            if standing in dict(ClubMember.STANDING_CHOICES):
                member.standing = standing
                if standing in ('resigned', 'lapsed') and not member.resigned_at:
                    member.resigned_at = date.today()
            sub_exp = request.POST.get('subscription_expires')
            member.subscription_expires = sub_exp or None
            role_id = request.POST.get('role_id')
            new_role = Role.objects.filter(club=club, id=role_id).first() if role_id else None
            new_has_admin = request.POST.get('has_admin_access') == 'on'
            # Guard: don't remove the last admin
            would_be_admin = new_has_admin or (new_role and new_role.effective_is_admin)
            if not would_be_admin:
                other_admins = ClubMember.objects.filter(club=club).exclude(id=member.id).filter(
                    models.Q(has_admin_access=True) |
                    models.Q(role__is_superadmin=True) |
                    models.Q(role__can_access_settings=True)
                )
                if not other_admins.exists():
                    return _inline_redirect(request, 'core:manage_member_detail',
                                            club_slug=club_slug, member_id=member_id,
                                            error='Cannot remove admin access — at least one admin must remain.')
            member.role = new_role
            member.has_admin_access = new_has_admin
            member.save()
            # Audit log
            _sc = dict(ClubMember.STANDING_CHOICES)
            if member.standing != _old_standing:
                MembershipHistoryEntry.objects.create(
                    club_member=member, event_type='standing_change', changed_by=request.user,
                    old_value=_sc.get(_old_standing, _old_standing),
                    new_value=_sc.get(member.standing, member.standing),
                )
            if member.role != _old_role:
                MembershipHistoryEntry.objects.create(
                    club_member=member, event_type='role_change', changed_by=request.user,
                    old_value=_old_role.name if _old_role else '—',
                    new_value=member.role.name if member.role else '—',
                )
            if member.subscription_expires != _old_sub_exp:
                MembershipHistoryEntry.objects.create(
                    club_member=member, event_type='subscription_renewed', changed_by=request.user,
                    old_value=str(_old_sub_exp) if _old_sub_exp else '—',
                    new_value=str(member.subscription_expires) if member.subscription_expires else '—',
                )
        elif action == 'save_notes' and actor.is_admin:
            pass  # notes field not yet on model — placeholder

        elif action in ('add_credential', 'edit_credential') and (actor.is_admin or actor.is_instructor):
            from .models import MemberCredential
            cred_type = request.POST.get('credential_type', '').strip()
            name = request.POST.get('cred_name', '').strip()
            cert_num = request.POST.get('certificate_number', '').strip()
            issue_str = request.POST.get('issue_date', '').strip() or None
            expiry_str = request.POST.get('expiry_date', '').strip() or None
            notes = request.POST.get('notes', '').strip()
            ac_type_id = request.POST.get('cred_aircraft_type_id', '').strip()
            ac_type_obj = (AircraftType.objects.filter(club=club, id=ac_type_id).first()
                           if ac_type_id and cred_type == 'type' else None)
            if action == 'add_credential' and cred_type:
                cred = MemberCredential(
                    club_member=member, credential_type=cred_type, name=name,
                    aircraft_type=ac_type_obj,
                    certificate_number=cert_num, issue_date=issue_str,
                    expiry_date=expiry_str, notes=notes, created_by=request.user,
                )
                if request.FILES.get('evidence'):
                    cred.evidence = request.FILES['evidence']
                cred.save()
            elif action == 'edit_credential':
                cred_id = request.POST.get('cred_id')
                cred = MemberCredential.objects.filter(club_member=member, id=cred_id).first()
                if cred:
                    if cred_type:
                        cred.credential_type = cred_type
                    cred.aircraft_type = ac_type_obj
                    cred.name = name
                    cred.certificate_number = cert_num
                    cred.issue_date = issue_str
                    cred.expiry_date = expiry_str
                    cred.notes = notes
                    if request.FILES.get('evidence'):
                        cred.evidence = request.FILES['evidence']
                    cred.save()

        elif action == 'delete_credential' and (actor.is_admin or actor.is_instructor):
            from .models import MemberCredential
            MemberCredential.objects.filter(club_member=member, id=request.POST.get('cred_id')).delete()

        elif action == 'set_credit_limit' and actor.is_admin:
            from .models import Account as _Account
            acct, _ = _Account.objects.get_or_create(club_member=member, defaults={'balance': 0})
            raw = request.POST.get('credit_limit', '').strip()
            if raw == '' or raw.lower() == 'exempt':
                acct.credit_limit = None
            else:
                try:
                    acct.credit_limit = float(raw)
                except ValueError:
                    pass
            acct.save(update_fields=['credit_limit'])

        elif action == 'account_topup' and actor.is_admin:
            from .models import AccountTransaction
            amount = request.POST.get('amount', '').strip()
            pay_method = request.POST.get('payment_method', 'bank_transfer')
            ref = request.POST.get('reference', '').strip()
            desc = request.POST.get('description', '').strip() or 'Account top-up'
            if amount:
                try:
                    acct, _ = member.account.__class__.objects.get_or_create(club_member=member, defaults={'balance': 0})
                    AccountTransaction.objects.create(
                        account=acct, transaction_type='top_up', direction='credit',
                        amount=amount, description=desc,
                        payment_method=pay_method, reference=ref, created_by=request.user,
                    )
                    acct.apply_transaction(amount, 'credit')
                except Exception:
                    pass

        elif action == 'account_adjustment' and actor.is_admin:
            from .models import AccountTransaction
            amount = request.POST.get('amount', '').strip()
            direction = request.POST.get('direction', 'credit')
            desc = request.POST.get('description', '').strip()
            if amount and desc:
                try:
                    acct = member.account
                    AccountTransaction.objects.create(
                        account=acct, transaction_type='adjustment', direction=direction,
                        amount=amount, description=desc, created_by=request.user,
                    )
                    acct.apply_transaction(amount, direction)
                except Exception:
                    pass

        elif action == 'account_transfer' and actor.is_admin:
            from .models import AccountTransaction
            amount = request.POST.get('amount', '').strip()
            dest_id = request.POST.get('dest_member_id', '').strip()
            desc = request.POST.get('description', '').strip() or 'Member-to-member transfer'
            dest = ClubMember.objects.filter(club=club, id=dest_id).first() if dest_id else None
            if amount and dest and dest != member:
                try:
                    src_acct = member.account
                    dst_acct = dest.account
                    AccountTransaction.objects.create(
                        account=src_acct, transaction_type='adjustment', direction='debit',
                        amount=amount, description=f'Transfer to {dest.user.get_full_name()} — {desc}',
                        created_by=request.user,
                    )
                    src_acct.apply_transaction(amount, 'debit')
                    AccountTransaction.objects.create(
                        account=dst_acct, transaction_type='adjustment', direction='credit',
                        amount=amount, description=f'Transfer from {member.user.get_full_name()} — {desc}',
                        created_by=request.user,
                    )
                    dst_acct.apply_transaction(amount, 'credit')
                except Exception:
                    pass

        elif action == 'add_frequent_passenger':
            from .models import FrequentPassenger
            fp_name  = request.POST.get('fp_name', '').strip()
            fp_phone = request.POST.get('fp_phone', '').strip()
            if fp_name:
                FrequentPassenger.objects.create(club_member=member, name=fp_name, phone=fp_phone)

        elif action == 'delete_frequent_passenger':
            from .models import FrequentPassenger
            FrequentPassenger.objects.filter(club_member=member, id=request.POST.get('fp_id')).delete()

        return _inline_redirect(request, 'core:manage_member_detail', club_slug=club_slug, member_id=member_id, saved=_saved)

    from .models import MemberCredential, AccountTransaction, FrequentPassenger as _FP
    from django.db.models import Q as _Q
    _now = timezone.now()
    _PAST_STATUSES = ('completed', 'transferred')
    _ACTIVE_STATUSES = ('pending', 'confirmed', 'departed')
    upcoming_bookings = (Booking.objects
                         .filter(club=club, member=member, status__in=_ACTIVE_STATUSES,
                                 scheduled_start__gte=_now)
                         .select_related('aircraft', 'instructor', 'flight_type', 'flight_completion')
                         .order_by('scheduled_start')[:10])
    past_bookings = (Booking.objects
                     .filter(club=club, member=member)
                     .filter(_Q(status__in=_PAST_STATUSES) | _Q(scheduled_start__lt=_now))
                     .exclude(status='cancelled')
                     .select_related('aircraft', 'instructor', 'flight_type', 'flight_completion')
                     .order_by('-scheduled_start')[:20])
    credentials = MemberCredential.objects.filter(club_member=member).order_by('expiry_date')
    roles = Role.objects.filter(club=club)

    try:
        account = member.account
        transactions = account.transactions.select_related('flight_completion__booking__aircraft').order_by('-created_at')[:30]
    except Exception:
        account = None
        transactions = []

    all_members = (ClubMember.objects.filter(club=club)
                   .exclude(id=member.id)
                   .select_related('user')
                   .order_by('user__last_name'))

    from .models import CredentialType
    frequent_passengers = _FP.objects.filter(club_member=member).order_by('name')
    _is_inline = request.GET.get('inline') == '1'
    membership_history = member.membership_history.select_related('changed_by').order_by('-changed_at')
    return render(request, 'core/manage_member_detail.html', {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'member': member, 'upcoming_bookings': upcoming_bookings, 'past_bookings': past_bookings,
        'credentials': credentials, 'account': account, 'transactions': transactions,
        'all_members': all_members, 'roles': roles,
        'standing_choices': ClubMember.STANDING_CHOICES,
        'credential_types': CredentialType.choices,
        'aircraft_type_list': AircraftType.objects.filter(club=club),
        'frequent_passengers': frequent_passengers,
        'membership_history': membership_history,
        'base_template': 'core/base_inline.html' if _is_inline else 'core/base.html',
        'inline_title': f'Manage <span class="crumb-sep">›</span> Members <span class="crumb-sep">›</span> <span class="crumb-cur">{member.user.get_full_name()}</span>',
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
        _profile_url = redirect('core:my_profile', club_slug=club_slug).url
        if action == 'avatar_upload':
            if request.FILES.get('avatar'):
                member.avatar = request.FILES['avatar']
                member.save(update_fields=['avatar'])
            return redirect(_profile_url + '?saved=1')
        elif action == 'save_notifications':
            from .models import NotificationPreference
            pref, _ = NotificationPreference.objects.get_or_create(club_member=member)
            # Per-type alert toggles
            _toggle_fields = [
                'booking_confirmed', 'booking_cancelled', 'booking_reminder',
                'credential_expiring', 'subscription_expiring',
                'instructor_booking_urgent', 'instructor_booking_upcoming',
                'maintenance_alert', 'lapsed_credentials', 'slot_released',
            ]
            for f in _toggle_fields:
                setattr(pref, f, request.POST.get(f) == 'on')
            # Slot-release filters
            pref.aircraft.set(request.POST.getlist('notify_aircraft'))
            pref.instructors.set(request.POST.getlist('notify_instructors'))
            raw_days = request.POST.get('max_days_ahead', '').strip()
            pref.max_days_ahead = int(raw_days) if raw_days.isdigit() else None
            pref.save()
            return redirect(_profile_url + '?saved=1#notifications')
        elif action == 'delete_notifications':
            from .models import NotificationPreference
            NotificationPreference.objects.filter(club_member=member).delete()
            from django.contrib import messages as _msg
            _msg.success(request, 'Notification preferences reset.')
            return redirect(_profile_url + '#notifications')
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
            dob = request.POST.get('date_of_birth', '').strip()
            member.date_of_birth = dob or None
            member.sex = request.POST.get('sex', '').strip()
            member.save()
            return redirect(_profile_url + '?saved=1')

    from django.db.models import Q as _Q
    _now = timezone.now()
    _PAST_STATUSES = ('completed', 'transferred')
    _ACTIVE_STATUSES = ('pending', 'confirmed', 'departed')
    upcoming = (Booking.objects
                .filter(club=club, member=member, status__in=_ACTIVE_STATUSES,
                        scheduled_start__gte=_now)
                .select_related('aircraft', 'instructor', 'flight_type', 'flight_completion')
                .order_by('scheduled_start')[:10])
    past = (Booking.objects
            .filter(club=club, member=member)
            .filter(_Q(status__in=_PAST_STATUSES) | _Q(scheduled_start__lt=_now))
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

    # Bookings where this user is the instructor (next 7 days)
    upcoming_as_instructor = None
    if member.is_instructor:
        upcoming_as_instructor = (Booking.objects
            .filter(club=club, instructor=request.user,
                    status__in=('pending', 'confirmed', 'departed'),
                    scheduled_start__gte=_now,
                    scheduled_start__lt=_now + timedelta(days=7))
            .select_related('aircraft', 'member__user', 'flight_type')
            .order_by('scheduled_start'))

    club_aircraft = Aircraft.objects.filter(club=club, status='online').order_by('registration')
    club_instructors = ClubMember.objects.filter(
        club=club, is_on_instructor_roster=True
    ).select_related('user').order_by('user__last_name')

    _raw_toggles = [
        ('booking_confirmed',           'Booking confirmed',                             True),
        ('booking_cancelled',           'Booking cancelled',                             True),
        ('booking_reminder',            'Booking reminder (day before)',                 True),
        ('credential_expiring',         'Credential expiring (medical, BFR, etc.)',      True),
        ('subscription_expiring',       'Subscription expiring',                         True),
        ('instructor_booking_urgent',   'New booking assigned — within 2 days (urgent)', True),
        ('instructor_booking_upcoming', 'New booking assigned — within 10 days',         True),
        ('maintenance_alert',           'Maintenance alert (amber/red items)',            True),
        ('lapsed_credentials',          'Member has lapsed credentials — flight today',   True),
        ('slot_released',               'Slot released by another member (opt-in)',       False),
    ]
    _notif_toggles = [
        {'field': f, 'label': l,
         'enabled': getattr(notification_pref, f, default) if notification_pref else default}
        for f, l, default in _raw_toggles
    ]
    paid_flights = (FlightCompletion.objects
                    .filter(booking__member=member, booking__club=club)
                    .exclude(amount_paid=0)
                    .exclude(amount_paid__isnull=True)
                    .select_related('booking__aircraft', 'booking__flight_type')
                    .order_by('-booking__scheduled_start')[:50])

    my_actions = (OccurrenceAction.objects
                  .filter(assigned_to=member, status=OccurrenceAction.STATUS_OPEN)
                  .select_related('report__occurrence_type', 'report__club')
                  .order_by('due_date', 'created_at'))
    return render(request, 'core/my_profile.html', {
        'club': club, 'club_member': member, 'is_instructor': member.is_instructor,
        'member': member, 'upcoming': upcoming, 'past': past, 'account': account,
        'notification_pref': notification_pref,
        'notification_toggle_fields': _notif_toggles,
        'club_aircraft': club_aircraft,
        'club_instructors': club_instructors,
        'upcoming_as_instructor': upcoming_as_instructor,
        'paid_flights': paid_flights,
        'my_actions': my_actions,
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
        elif action == 'edit_blockout':
            bo_id = request.POST.get('bo_id')
            bo = BlockOut.objects.filter(club=club, id=bo_id, scope='all').first()
            if bo:
                bot_id = request.POST.get('bot_id')
                bo.blockout_type = BlockOutType.objects.filter(club=club, id=bot_id).first() if bot_id else None
                bo.label = request.POST.get('label', '').strip()
                bo.recurrence = request.POST.get('recurrence', 'one_off')
                bo.all_day = request.POST.get('all_day') in ('on', '1', 'true')
                from datetime import date as _date
                if bo.recurrence == 'one_off':
                    date_str = request.POST.get('date', '')
                    try:
                        bo.date = _date.fromisoformat(date_str) if date_str else None
                    except ValueError:
                        bo.date = None
                    bo.weekday = None
                elif bo.recurrence == 'weekly':
                    weekday_str = request.POST.get('weekday', '0')
                    bo.weekday = int(weekday_str) if weekday_str.isdigit() else 0
                    bo.date = None
                else:
                    bo.date = None
                    bo.weekday = None
                if not bo.all_day:
                    bo.start_time = request.POST.get('start_time') or None
                    bo.end_time = request.POST.get('end_time') or None
                else:
                    bo.start_time = None
                    bo.end_time = None
                bo.save()
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

    modal_error = modal_error_id = None
    modal_error_values = {}
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'add_member' and actor.is_admin:
            from django.contrib.auth import get_user_model as _get_user
            _User = _get_user()
            first = request.POST.get('first_name', '').strip()
            last = request.POST.get('last_name', '').strip()
            email = request.POST.get('email', '').strip().lower()
            password = request.POST.get('password', '').strip()
            if not (first and last and email):
                modal_error = 'First name, last name, and email are required.'
            elif _User.objects.filter(email=email).exists():
                modal_error = 'An account with that email address already exists.'
            else:
                import secrets as _secrets
                from django.contrib import messages as _messages
                auto_generated = not password
                pw = password or _secrets.token_urlsafe(12)
                user = _User.objects.create_user(
                    username=email, email=email,
                    first_name=first, last_name=last, password=pw
                )
                new_cm = ClubMember.objects.create(club=club, user=user)
                MembershipHistoryEntry.objects.create(
                    club_member=new_cm, event_type='joined',
                    changed_by=request.user, new_value=email,
                )
                if auto_generated:
                    _messages.info(request,
                        f"Member created. Auto-generated password: {pw} — note this now and give it to {first}. It won't be shown again.")
                return redirect('core:manage_members', club_slug=club_slug)
            modal_error_id = 'add-member-modal'
            modal_error_values = {'first_name': first, 'last_name': last, 'email': email}
        else:
            cm_id = request.POST.get('cm_id')
            cm = ClubMember.objects.filter(club=club, id=cm_id).first() if cm_id else None
            if cm:
                if action == 'set_standing':
                    standing = request.POST.get('standing')
                    if standing in dict(ClubMember.STANDING_CHOICES):
                        old_standing = cm.standing
                        cm.standing = standing
                        if standing in ('resigned', 'lapsed') and not cm.resigned_at:
                            cm.resigned_at = date.today()
                        cm.save(update_fields=['standing', 'resigned_at'])
                        MembershipHistoryEntry.objects.create(
                            club_member=cm, event_type='standing_change',
                            changed_by=request.user,
                            old_value=dict(ClubMember.STANDING_CHOICES).get(old_standing, old_standing),
                            new_value=dict(ClubMember.STANDING_CHOICES).get(standing, standing),
                        )
                elif action == 'set_role':
                    role_id = request.POST.get('role_id')
                    new_role = Role.objects.filter(club=club, id=role_id).first() if role_id else None
                    # Guard: must always have at least one member on the admin system role
                    _admin_role = Role.objects.filter(club=club, system_role_type='admin').first()
                    if _admin_role and cm.role == _admin_role and new_role != _admin_role:
                        _remaining_admins = ClubMember.objects.filter(
                            club=club, role=_admin_role).exclude(id=cm.id).count()
                        if _remaining_admins == 0:
                            from django.contrib import messages as _msg
                            _msg.error(request, 'Cannot remove the last Administrator. Assign another member to Administrator first.')
                            return redirect('core:manage_members', club_slug=club_slug)
                    old_role = cm.role
                    cm.role = new_role
                    cm.save(update_fields=['role'])
                    MembershipHistoryEntry.objects.create(
                        club_member=cm, event_type='role_change',
                        changed_by=request.user,
                        old_value=old_role.name if old_role else '—',
                        new_value=new_role.name if new_role else '—',
                    )
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
        'modal_error': modal_error,
        'modal_error_id': modal_error_id,
        'modal_error_values': modal_error_values,
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
    retire_error = ''
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'set_status' and actor.is_admin:
            ac_id = request.POST.get('ac_id')
            status = request.POST.get('status')
            force = request.POST.get('force') == '1'
            ac = Aircraft.objects.filter(club=club, id=ac_id).first()
            if ac and status in [s.value for s in AircraftStatus]:
                if status == 'retired' and not force:
                    future_count = Booking.objects.filter(
                        club=club, aircraft=ac,
                        scheduled_start__gt=timezone.now(),
                    ).exclude(status='cancelled').count()
                    if future_count:
                        retire_error = (
                            f"{ac.registration} has "
                            f"{future_count} future booking{'s' if future_count != 1 else ''} — "
                            "they will appear on the Exceptions screen for reassignment."
                        )
                        # Store pending retire context for the confirm button
                        retire_error = {'msg': retire_error, 'ac_id': ac_id, 'count': future_count}
                        status = None  # don't save yet
                if status:
                    ac.status = status
                    ac.save(update_fields=['status'])
        elif action == 'add_aircraft' and actor.is_admin:
            reg = request.POST.get('registration', '').strip().upper()
            ac_type_id = request.POST.get('aircraft_type_id', '').strip()
            ac_type_obj = AircraftType.objects.filter(club=club, id=ac_type_id).first() if ac_type_id else None
            if reg:
                Aircraft.objects.get_or_create(
                    club=club, registration=reg,
                    defaults={'aircraft_type': ac_type_obj}
                )
        elif action == 'add_aircraft_blockout':
            ac_id = request.POST.get('ac_id')
            ac = Aircraft.objects.filter(club=club, id=ac_id).first()
            if ac:
                _create_blockout_from_post(request, club, scope='aircraft', aircraft=ac)
        elif action == 'delete_blockout':
            bo_id = request.POST.get('bo_id')
            BlockOut.objects.filter(club=club, id=bo_id).delete()
        if not retire_error:
            return redirect('core:manage_aircraft', club_slug=club_slug)

    online_aircraft  = Aircraft.objects.filter(club=club, status='online').order_by('registration')
    retired_aircraft = Aircraft.objects.filter(club=club, status='retired').order_by('registration')
    aircraft_list = list(online_aircraft) + list(retired_aircraft)
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
        'online_aircraft': online_aircraft,
        'retired_aircraft': retired_aircraft,
        'aircraft_list': aircraft_list,
        'aircraft_type_list': AircraftType.objects.filter(club=club),
        'status_choices': AircraftStatus.choices,
        'aircraft_blockout_types': aircraft_blockout_types,
        'retire_error': retire_error,
    })


@login_required
def manage_aircraft_detail(request, club_slug, aircraft_id):
    from .models import ChargeRate, FuelSurchargeRate, AircraftSurchargeType, AircraftMaintenanceItem, BlockOutType
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not (actor.is_admin or actor.is_instructor):
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    ac = get_object_or_404(Aircraft, club=club, id=aircraft_id)

    if request.method == 'POST':
        action = request.POST.get('action', '')

        _saved = action in ('save_details', 'save_instruments')
        if action == 'save_details' and actor.is_admin:
            ac_type_id = request.POST.get('aircraft_type_id', '').strip()
            ac_type_obj = AircraftType.objects.filter(club=club, id=ac_type_id).first() if ac_type_id else None
            if ac_type_obj:
                ac.aircraft_type = ac_type_obj
            ac.serial_number = request.POST.get('serial_number', '').strip()
            seats = request.POST.get('seats', '')
            if seats.isdigit():
                ac.seats = int(seats)
            engines = request.POST.get('engine_count', '')
            if engines.isdigit():
                ac.engine_count = int(engines)
            ac.is_leased = request.POST.get('is_leased') == 'on'
            ac.is_available_for_hire = request.POST.get('is_available_for_hire') == 'on'
            ac.save()

        elif action == 'save_instruments' and actor.is_admin:
            ac.records_hobbs = request.POST.get('records_hobbs') == 'on'
            ac.records_tacho = request.POST.get('records_tacho') == 'on'
            ac.records_airswitch = request.POST.get('records_airswitch') == 'on'
            ttm = request.POST.get('total_time_method', '')
            if ttm in dict(Aircraft.TOTAL_TIME_METHOD_CHOICES):
                ac.total_time_method = ttm
            fuel_cph = request.POST.get('fuel_consumption_per_hour', '').strip()
            try:
                ac.fuel_consumption_per_hour = fuel_cph
            except Exception:
                pass
            hobbs_init     = request.POST.get('hobbs_initial', '').strip()
            tacho_init     = request.POST.get('tacho_initial', '').strip()
            airswitch_init = request.POST.get('airswitch_initial', '').strip()
            ac.hobbs_initial     = hobbs_init or None
            ac.tacho_initial     = tacho_init or None
            ac.airswitch_initial = airswitch_init or None
            mts = request.POST.get('maint_time_source', '')
            if mts in dict(Aircraft.MAINT_SOURCE_CHOICES):
                ac.maint_time_source = mts
            frac = request.POST.get('maint_time_fraction', '').strip()
            try:
                f = float(frac)
                if 0.5 <= f <= 1.0:
                    ac.maint_time_fraction = round(f, 2)
            except (ValueError, TypeError):
                pass
            mhi = request.POST.get('maint_hours_initial', '').strip()
            ac.maint_hours_initial = mhi or None
            ac.save()

        elif action == 'save_hire_rate' and actor.is_admin:
            ft_id = request.POST.get('ft_id')
            time_method = request.POST.get('time_method', 'hobbs')
            amount = request.POST.get('amount', '').strip()
            includes_fuel = request.POST.get('includes_fuel') == 'on'
            ft = FlightType.objects.filter(club=club, id=ft_id).first()
            if ft and amount:
                ChargeRate.objects.update_or_create(
                    aircraft=ac, flight_type=ft, time_method=time_method,
                    defaults={'club': club, 'amount': amount, 'includes_fuel': includes_fuel}
                )

        elif action == 'edit_hire_rate' and actor.is_admin:
            rate_id = request.POST.get('rate_id')
            rate = ChargeRate.objects.filter(club=club, aircraft=ac, id=rate_id).first()
            if rate:
                amount = request.POST.get('amount', '').strip()
                if amount:
                    rate.amount = amount
                rate.includes_fuel = request.POST.get('includes_fuel') == 'on'
                rate.save(update_fields=['amount', 'includes_fuel'])

        elif action == 'delete_hire_rate' and actor.is_admin:
            ChargeRate.objects.filter(club=club, aircraft=ac, id=request.POST.get('rate_id')).delete()

        elif action == 'add_fuel_rate' and actor.is_admin:
            rate = request.POST.get('fuel_rate', '').strip()
            effective_from = request.POST.get('effective_from', '').strip()
            notes = request.POST.get('notes', '').strip()
            if rate and effective_from:
                FuelSurchargeRate.objects.create(
                    club=club, aircraft=ac, rate=rate,
                    effective_from=effective_from, notes=notes
                )

        elif action == 'toggle_fuel_rate' and actor.is_admin:
            r = FuelSurchargeRate.objects.filter(club=club, aircraft=ac, id=request.POST.get('rate_id')).first()
            if r:
                r.is_active = not r.is_active
                r.save(update_fields=['is_active'])

        elif action == 'delete_fuel_rate' and actor.is_admin:
            FuelSurchargeRate.objects.filter(club=club, aircraft=ac, id=request.POST.get('rate_id')).delete()

        elif action == 'toggle_surcharge' and actor.is_admin:
            sc_id = request.POST.get('sc_id')
            sc = AircraftSurchargeType.objects.filter(club=club, id=sc_id).first()
            if sc:
                if ac.surcharges.filter(id=sc.id).exists():
                    ac.surcharges.remove(sc)
                else:
                    ac.surcharges.add(sc)

        elif action == 'save_surcharges' and actor.is_admin:
            selected_ids = [int(i) for i in request.POST.getlist('surcharge_ids') if i.isdigit()]
            ac.surcharges.set(AircraftSurchargeType.objects.filter(club=club, id__in=selected_ids))

        elif action == 'add_maintenance' and actor.is_admin:
            name = request.POST.get('maint_name', '').strip()
            if name:
                AircraftMaintenanceItem.objects.create(
                    aircraft=ac, name=name,
                    description=request.POST.get('maint_desc', '').strip(),
                    due_date=request.POST.get('due_date') or None,
                    due_hours=request.POST.get('due_hours') or None,
                    last_completed_date=request.POST.get('last_completed_date') or None,
                    last_completed_hours=request.POST.get('last_completed_hours') or None,
                    interval_days=request.POST.get('interval_days') or None,
                    interval_hours=request.POST.get('interval_hours') or None,
                    warn_days=request.POST.get('warn_days') or None,
                    alert_days=request.POST.get('alert_days') or None,
                    warn_hours=request.POST.get('warn_hours') or None,
                    alert_hours=request.POST.get('alert_hours') or None,
                )

        elif action == 'edit_maintenance' and actor.is_admin:
            maint_id = request.POST.get('maint_id')
            m = AircraftMaintenanceItem.objects.filter(aircraft=ac, id=maint_id).first()
            if m:
                name = request.POST.get('maint_name', '').strip()
                if name:
                    m.name = name
                m.description = request.POST.get('maint_desc', '').strip()
                m.due_date = request.POST.get('due_date') or None
                m.due_hours = request.POST.get('due_hours') or None
                m.last_completed_date = request.POST.get('last_completed_date') or None
                m.last_completed_hours = request.POST.get('last_completed_hours') or None
                m.interval_days = request.POST.get('interval_days') or None
                m.interval_hours = request.POST.get('interval_hours') or None
                m.warn_days = request.POST.get('warn_days') or None
                m.alert_days = request.POST.get('alert_days') or None
                m.warn_hours = request.POST.get('warn_hours') or None
                m.alert_hours = request.POST.get('alert_hours') or None
                m.save()

        elif action == 'delete_maintenance' and actor.is_admin:
            AircraftMaintenanceItem.objects.filter(aircraft=ac, id=request.POST.get('maint_id')).delete()

        elif action == 'add_manual_log_entry' and actor.is_admin:
            from .models import MaintenanceLogEntry as _MLE
            from decimal import Decimal as _D
            entry_date = request.POST.get('entry_date') or date.today().isoformat()
            notes = request.POST.get('notes', '').strip()
            hobbs = request.POST.get('hobbs_reading') or None
            tacho = request.POST.get('tacho_reading') or None
            airswitch = request.POST.get('airswitch_reading') or None
            try:
                maint_hrs = _D(request.POST.get('maint_hours', '0') or '0')
            except Exception:
                maint_hrs = _D('0')
            # Compute cumulative total from last log entry
            prev = _MLE.objects.filter(aircraft=ac).order_by('-date', '-id').first()
            prev_total = prev.maint_hours_total if prev else _D(str(ac.maint_hours_initial or 0))
            _MLE.objects.create(
                aircraft=ac,
                date=entry_date,
                hobbs_reading=hobbs,
                tacho_reading=tacho,
                airswitch_reading=airswitch,
                maint_hours_flight=maint_hrs,
                maint_hours_total=prev_total + maint_hrs,
                notes=notes,
            )
            _saved = True

        elif action == 'add_aircraft_blockout':
            _create_blockout_from_post(request, club, scope='aircraft', aircraft=ac)

        elif action == 'delete_blockout':
            from .models import BlockOut
            BlockOut.objects.filter(club=club, id=request.POST.get('bo_id')).delete()

        return _inline_redirect(request, 'core:manage_aircraft_detail', club_slug=club_slug, aircraft_id=aircraft_id, saved=_saved)

    from .models import BlockOut
    from django.db.models import Q
    from datetime import date as _date
    _today = _date.today()
    _active_q = (
        Q(recurrence='one_off', date__gte=_today) |
        Q(recurrence__in=['weekly', 'daily'], active_until__isnull=True) |
        Q(recurrence__in=['weekly', 'daily'], active_until__gte=_today)
    )
    blockouts = (BlockOut.objects
                 .filter(club=club, scope='aircraft')
                 .filter(_active_q)
                 .prefetch_related('aircraft', 'blockout_type')
                 .order_by('recurrence', 'date', 'weekday', 'start_time'))
    ac_blockouts = [bo for bo in blockouts if ac in bo.aircraft.all()]

    hire_rates = (ChargeRate.objects
                  .filter(aircraft=ac)
                  .select_related('flight_type')
                  .order_by('flight_type__name', 'time_method'))
    fuel_rates = FuelSurchargeRate.objects.filter(aircraft=ac).order_by('-effective_from')
    all_surcharge_types = AircraftSurchargeType.objects.filter(club=club)
    assigned_surcharge_ids = set(ac.surcharges.values_list('id', flat=True))
    maintenance_items = list(AircraftMaintenanceItem.objects.filter(aircraft=ac).order_by('urgency', 'due_date'))

    # Compute progress percentage for each maintenance item and attach as a dynamic attribute.
    # Priority: date-based interval if both last_completed_date and due_date are set; else hours-based.
    _latest_meters = (FlightCompletion.objects
                      .filter(booking__aircraft=ac, booking__club=club)
                      .exclude(hobbs_end__isnull=True)
                      .order_by('-booking__arrived_at', '-created_at')
                      .values('hobbs_end').first())
    _current_hobbs = float(_latest_meters['hobbs_end']) if _latest_meters else None
    _today = date.today()
    for _m in maintenance_items:
        _m.progress_pct = None
        if _m.last_completed_date and _m.due_date:
            _total = max(1, (_m.due_date - _m.last_completed_date).days)
            _elapsed = (_today - _m.last_completed_date).days
            _m.progress_pct = min(100, max(0, round(_elapsed / _total * 100)))
        elif _m.last_completed_hours is not None and _m.due_hours is not None and _current_hobbs is not None:
            _total_h = float(_m.due_hours - _m.last_completed_hours)
            if _total_h > 0:
                _elapsed_h = _current_hobbs - float(_m.last_completed_hours)
                _m.progress_pct = min(100, max(0, round(_elapsed_h / _total_h * 100)))
    flight_types = FlightType.objects.filter(club=club, is_billable=True)
    aircraft_blockout_types = BlockOutType.objects.filter(club=club, target='aircraft')

    flight_history = (Booking.objects
                      .filter(club=club, aircraft=ac)
                      .exclude(status='cancelled')
                      .select_related('member__user', 'instructor', 'flight_type', 'flight_completion')
                      .order_by('-scheduled_start')[:100])

    from .models import MaintenanceLogEntry
    maint_log = (MaintenanceLogEntry.objects
                 .filter(aircraft=ac)
                 .select_related('flight_completion__booking__member__user')
                 .order_by('-date', '-id')[:100])

    _is_inline = request.GET.get('inline') == '1'
    return render(request, 'core/manage_aircraft_detail.html', {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'ac': ac,
        'hire_rates': hire_rates,
        'fuel_rates': fuel_rates,
        'all_surcharge_types': all_surcharge_types,
        'assigned_surcharge_ids': assigned_surcharge_ids,
        'maintenance_items': maintenance_items,
        'maint_log': maint_log,
        'flight_types': flight_types,
        'aircraft_blockout_types': aircraft_blockout_types,
        'ac_blockouts': ac_blockouts,
        'flight_history': flight_history,
        'aircraft_type_list': AircraftType.objects.filter(club=club),
        'base_template': 'core/base_inline.html' if _is_inline else 'core/base.html',
        'inline_title': f'Manage <span class="crumb-sep">›</span> Aircraft <span class="crumb-sep">›</span> <span class="crumb-cur">{ac.registration}</span>',
        'records_instruments': [
            ('records_hobbs',      'Hobbs meter',  ac.records_hobbs),
            ('records_tacho',      'Tachometer',   ac.records_tacho),
            ('records_airswitch',  'Air switch',   ac.records_airswitch),
        ],
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

    if request.method == 'POST' and actor.is_admin:
        action = request.POST.get('action', '')
        if action == 'add_instructor':
            member_id = request.POST.get('member_id', '').strip()
            member = ClubMember.objects.filter(club=club, id=member_id).first()
            if member and member.role and member.role.effective_is_instructor:
                member.is_on_instructor_roster = True
                member.save(update_fields=['is_on_instructor_roster'])
        elif action == 'remove_instructor':
            member_id = request.POST.get('member_id', '').strip()
            member = ClubMember.objects.filter(club=club, id=member_id).first()
            if member:
                future_bookings = Booking.objects.filter(
                    club=club, instructor=member.user,
                    scheduled_end__gte=timezone.now(),
                    status__in=['pending', 'confirmed'],
                )
                for b in future_bookings:
                    new_instr_id = request.POST.get(f'reassign_{b.id}', '').strip()
                    if new_instr_id:
                        new_instr = ClubMember.objects.filter(
                            club=club, id=new_instr_id, is_on_instructor_roster=True
                        ).first()
                        b.instructor = new_instr.user if new_instr else None
                    else:
                        b.instructor = None
                    b.save(update_fields=['instructor'])
                ClubMember.objects.filter(club=club, id=member_id).update(is_on_instructor_roster=False)
        return redirect('core:manage_instructors', club_slug=club_slug)

    from .models import InstructorAvailability
    from django.db.models import Q
    from datetime import date as _date
    _today = _date.today()
    _active_q = (
        Q(recurrence='one_off', date__gte=_today) |
        Q(recurrence='weekly', active_until__isnull=True) |
        Q(recurrence='weekly', active_until__gte=_today)
    )
    av_counts = {}
    for av in InstructorAvailability.objects.filter(club_member__club=club).filter(_active_q):
        av_counts[av.club_member_id] = av_counts.get(av.club_member_id, 0) + 1

    instructors = (ClubMember.objects
                   .filter(club=club, is_on_instructor_roster=True)
                   .select_related('user', 'instructor_grade', 'role')
                   .order_by('user__last_name'))
    for instr in instructors:
        instr.av_count = av_counts.get(instr.id, 0)
        instr.future_bookings = list(
            Booking.objects
            .filter(club=club, instructor=instr.user,
                    scheduled_end__gte=timezone.now(),
                    status__in=['pending', 'confirmed'])
            .select_related('member__user', 'aircraft')
            .order_by('scheduled_start')
        )

    # Members eligible to add: have instructor-type role, active standing, not already on roster
    roster_ids = set(instructors.values_list('id', flat=True))
    # Use system_role_type='instructor' if set, fall back to permission flags for legacy roles
    from django.db.models import Q as _Q
    _instr_q = _Q(role__system_role_type='instructor') | _Q(
        role__bookings_access='manage_all', role__can_access_manage=True
    )
    eligible_members = (ClubMember.objects
                        .filter(_instr_q, club=club, standing='current')
                        .exclude(id__in=roster_ids)
                        .select_related('user', 'role')
                        .order_by('user__last_name', 'user__first_name'))
    any_instructor_role = ClubMember.objects.filter(_instr_q, club=club).exists()
    all_on_roster = not eligible_members.exists() and any_instructor_role

    return render(request, 'core/manage_instructors.html', {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'instructors': instructors,
        'eligible_members': eligible_members,
        'all_on_roster': all_on_roster,
    })


@login_required
def manage_instructor_detail(request, club_slug, member_id):
    from .models import InstructorAvailability, BlockOut, BlockOutType, MemberCredential
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not (actor.is_admin or actor.is_instructor):
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    instr = get_object_or_404(ClubMember, club=club, id=member_id)

    if request.method == 'POST':
        action = request.POST.get('action', '')
        _saved = action in ('save_contact', 'save_details')

        if action == 'save_details' and actor.is_admin:
            # Grade is the only instructor-specific field; contact is edited on the member profile
            grade_id = request.POST.get('grade_id', '')
            instr.instructor_grade = InstructorGrade.objects.filter(club=club, id=grade_id).first() if grade_id else None
            instr.save(update_fields=['instructor_grade'])

        elif action == 'save_grade' and actor.is_admin:
            grade_id = request.POST.get('grade_id')
            instr.instructor_grade = InstructorGrade.objects.filter(club=club, id=grade_id).first() if grade_id else None
            instr.save(update_fields=['instructor_grade'])

        elif action == 'add_instructor_availability':
            recurrence = request.POST.get('av_recurrence', 'weekly')
            all_day = request.POST.get('av_all_day') == 'on'
            av = InstructorAvailability(club_member=instr, recurrence=recurrence, all_day=all_day)
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
            InstructorAvailability.objects.filter(club_member=instr, id=request.POST.get('av_id')).delete()

        elif action == 'add_instructor_blockout':
            _create_blockout_from_post(request, club, scope='instructors', instructor_user=instr.user)

        elif action == 'delete_blockout':
            BlockOut.objects.filter(club=club, id=request.POST.get('bo_id')).delete()

        return _inline_redirect(request, 'core:manage_instructor_detail', club_slug=club_slug, member_id=member_id, saved=_saved)

    from django.db.models import Q
    from datetime import date as _date
    _today = _date.today()
    _active_q = (
        Q(recurrence='one_off', date__gte=_today) |
        Q(recurrence='weekly', active_until__isnull=True) |
        Q(recurrence='weekly', active_until__gte=_today)
    )
    av_windows = InstructorAvailability.objects.filter(club_member=instr).filter(_active_q).order_by('weekday', 'date')
    past_av_count = InstructorAvailability.objects.filter(club_member=instr).exclude(_active_q).count()

    _bo_active_q = (
        Q(recurrence='one_off', date__gte=_today) |
        Q(recurrence__in=['weekly', 'daily'], active_until__isnull=True) |
        Q(recurrence__in=['weekly', 'daily'], active_until__gte=_today)
    )
    all_instr_bos = (BlockOut.objects
                     .filter(club=club, scope='instructors')
                     .filter(_bo_active_q)
                     .prefetch_related('instructors', 'blockout_type'))
    instr_blockouts = [bo for bo in all_instr_bos if instr.user in bo.instructors.all()]

    credentials = MemberCredential.objects.filter(club_member=instr).order_by('credential_type', 'expiry_date')
    instructor_grades = InstructorGrade.objects.filter(club=club).order_by('display_order')
    instructor_blockout_types = BlockOutType.objects.filter(club=club).exclude(target='aircraft')

    upcoming_bookings = (Booking.objects
                         .filter(club=club, instructor=instr.user)
                         .exclude(status='cancelled')
                         .filter(scheduled_start__gte=timezone.now())
                         .select_related('member__user', 'aircraft', 'flight_type')
                         .order_by('scheduled_start')[:10])

    _is_inline = request.GET.get('inline') == '1'
    return render(request, 'core/manage_instructor_detail.html', {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'instr': instr,
        'av_windows': av_windows,
        'past_av_count': past_av_count,
        'instr_blockouts': instr_blockouts,
        'credentials': credentials,
        'instructor_grades': instructor_grades,
        'instructor_blockout_types': instructor_blockout_types,
        'upcoming_bookings': upcoming_bookings,
        'base_template': 'core/base_inline.html' if _is_inline else 'core/base.html',
        'inline_title': f'Manage <span class="crumb-sep">›</span> Instructors <span class="crumb-sep">›</span> <span class="crumb-cur">{instr.user.get_full_name()}</span>',
    })


@login_required
def manage_contacts(request, club_slug):
    from .models import Contact as _C
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not actor.can_access_manage:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    if request.method == 'POST':
        name     = request.POST.get('name', '').strip()
        email    = request.POST.get('email', '').strip()
        phone    = request.POST.get('phone', '').strip()
        is_org   = request.POST.get('is_organisation') == '1'
        org      = request.POST.get('organisation', '').strip()
        ctype_id = request.POST.get('contact_type', '').strip()
        notes    = request.POST.get('notes', '').strip()
        if name:
            _C.objects.create(
                club=club, name=name, email=email, phone=phone,
                is_organisation=is_org, organisation=org,
                contact_type=ContactType.objects.filter(id=ctype_id, club=club).first() if ctype_id else None,
                notes=notes,
                created_by=request.user,
            )
        return redirect('core:manage_contacts', club_slug=club_slug)

    f_type = request.GET.get('type', '')
    f_q    = request.GET.get('q', '').strip()
    from django.db.models import Q as _Q, Count as _Count
    qs = _C.objects.filter(club=club)
    if f_type:
        qs = qs.filter(contact_type_id=f_type)
    if f_q:
        qs = qs.filter(_Q(name__icontains=f_q) | _Q(organisation__icontains=f_q) | _Q(email__icontains=f_q))
    contacts = qs.annotate(booking_count=_Count('bookings')).order_by('name')

    return render(request, 'core/manage_contacts.html', {
        'club': club, 'club_member': actor,
        'contacts': contacts,
        'f_type': f_type, 'f_q': f_q,
        'contact_types': list(ContactType.objects.filter(club=club).order_by('sort_order','name')),
    })


@login_required
def contact_detail(request, club_slug, contact_id):
    from .models import Contact as _C
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not actor.can_access_manage:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    contact = get_object_or_404(_C, id=contact_id, club=club)
    error = None

    if request.method == 'POST':
        action = request.POST.get('action', 'save')

        if action == 'save':
            contact.name          = request.POST.get('name', contact.name).strip() or contact.name
            contact.email         = request.POST.get('email', '').strip()
            contact.phone         = request.POST.get('phone', '').strip()
            contact.is_organisation = request.POST.get('is_organisation') == '1'
            contact.organisation  = request.POST.get('organisation', '').strip()
            _ct_id = request.POST.get('contact_type', '').strip()
            contact.contact_type  = ContactType.objects.filter(id=_ct_id, club=club).first() if _ct_id else None
            contact.notes         = request.POST.get('notes', '').strip()
            contact.save()
            redir = request.path + ('?inline=1' if request.GET.get('inline') == '1' else '?saved=1')
            return redirect(redir)

        elif action == 'convert_to_member' and contact.can_convert:
            from django.contrib.auth import get_user_model as _gum
            from django.contrib.auth.hashers import make_password
            import secrets
            _User = _gum()
            email = contact.email or request.POST.get('email', '').strip()
            if not email:
                error = 'An email address is required to create a member account.'
            elif _User.objects.filter(email=email).exists():
                error = 'A user with this email already exists.'
            else:
                username = email.split('@')[0][:30]
                base_u = username
                i = 1
                while _User.objects.filter(username=username).exists():
                    username = f'{base_u}{i}'; i += 1
                names = contact.name.strip().split(None, 1)
                first = names[0]; last = names[1] if len(names) > 1 else ''
                user = _User.objects.create(
                    username=username, email=email,
                    first_name=first, last_name=last,
                    password=make_password(secrets.token_hex(20)),
                )
                member_role = ClubMember.objects.filter(
                    club=club, role__system_role_type='member'
                ).values_list('role_id', flat=True).first()
                new_member = ClubMember.objects.create(
                    club=club, user=user,
                    role_id=member_role,
                    standing='current',
                )
                contact.converted_to_member = new_member
                contact.save(update_fields=['converted_to_member'])
                return redirect('core:manage_member_detail', club_slug=club_slug, member_id=new_member.id)

    bookings = (contact.bookings
                .select_related('aircraft', 'flight_type', 'member__user')
                .order_by('-scheduled_start')[:20])

    is_inline = request.GET.get('inline') == '1'
    return render(request, 'core/contact_detail.html', {
        'club': club, 'club_member': actor,
        'contact': contact,
        'bookings': bookings,
        'error': error,
        'contact_types': list(ContactType.objects.filter(club=club).order_by('sort_order','name')),
        'base_template': 'core/base_inline.html' if is_inline else 'core/base.html',
        'inline_title': f'Manage <span class="crumb-sep">›</span> Contacts <span class="crumb-sep">›</span> <span class="crumb-cur">{contact.name}</span>',
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
def manage_charges(request, club_slug):
    from .models import FlightCompletion, AccountTransaction
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not actor.is_admin:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    unpaid = (FlightCompletion.objects
              .filter(booking__club=club, paid_at__isnull=True)
              .select_related('booking__member__user', 'booking__aircraft', 'booking__flight_type',
                              'booking__instructor')
              .order_by('-booking__scheduled_start'))

    recent_tx = (AccountTransaction.objects
                 .filter(account__club_member__club=club)
                 .select_related('account__club_member__user', 'flight_completion__booking__aircraft')
                 .order_by('-created_at')[:50])

    return render(request, 'core/manage_charges.html', {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'unpaid': unpaid,
        'recent_tx': recent_tx,
    })


@login_required
@transaction.atomic
def booking_declaration(request, club_slug, booking_id):
    from .models import DepartureDeclaration, DeclarationPassenger, FrequentPassenger
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')

    booking = get_object_or_404(Booking, club=club, id=booking_id)
    # Allow the member themselves or staff
    is_own = (booking.member.user == request.user)
    if not (is_own or actor.is_admin or actor.is_instructor):
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    # Completed flights: declaration is always read-only for everyone
    # Departed flights: read-only for non-staff
    readonly = (booking.status == 'completed') or \
               (not is_own and not actor.is_admin and not actor.is_instructor) or \
               (booking.status == 'departed' and not actor.is_admin and not actor.is_instructor)

    decl, _ = DepartureDeclaration.objects.get_or_create(
        booking=booking,
        defaults={'submitted_by': request.user, 'is_draft': True}
    )
    error = None

    _decl_url = f'{request.path}{"?inline=1" if request.GET.get("inline") == "1" else ""}'

    if request.method == 'POST' and not readonly:
        action = request.POST.get('action', 'save_draft')

        # Passenger operations — handled independently, don't touch declaration fields
        if action == 'add_passenger':
            pax_name = request.POST.get('pax_name', '').strip()
            pax_phone = request.POST.get('pax_phone', '').strip()
            pax_nok_name = request.POST.get('pax_nok_name', '').strip()
            pax_nok_phone = request.POST.get('pax_nok_phone', '').strip()
            if pax_name:
                decl.save()
                DeclarationPassenger.objects.create(
                    declaration=decl, name=pax_name, phone=pax_phone,
                    next_of_kin_name=pax_nok_name, next_of_kin_phone=pax_nok_phone,
                )
            return redirect(_decl_url)

        elif action == 'remove_passenger':
            DeclarationPassenger.objects.filter(
                declaration=decl, id=request.POST.get('pax_id')
            ).delete()
            return redirect(_decl_url)

        # Declaration content fields
        from django.utils.dateparse import parse_datetime as _pdatetime
        decl.authorising_instructor_id = request.POST.get('authorising_instructor_id') or None
        decl.route_intentions = request.POST.get('route_intentions', '').strip()
        decl.destination = request.POST.get('destination', '').strip()
        decl.is_cross_country = request.POST.get('is_cross_country') == 'on'
        decl.next_of_kin_name = request.POST.get('next_of_kin_name', '').strip()
        decl.next_of_kin_phone = request.POST.get('next_of_kin_phone', '').strip()
        for field in ['confirm_aip', 'confirm_weather', 'confirm_fuel', 'confirm_pickets',
                      'confirm_maps', 'confirm_fuel_card', 'confirm_afm', 'confirm_flight_plan']:
            setattr(decl, field, request.POST.get(field) == 'on')
        _er = request.POST.get('estimated_return', '').strip()
        decl.estimated_return = _pdatetime(_er.replace('T', ' ')) if _er else None
        _st = request.POST.get('sar_time', '').strip()
        decl.sar_time = _pdatetime(_st.replace('T', ' ')) if _st else None

        if action == 'submit':
            missing = []
            if not decl.route_intentions:
                missing.append('route intentions')
            if not decl.destination:
                missing.append('destination')
            if missing:
                error = 'Please complete: ' + ', '.join(missing)
            else:
                from django.utils import timezone as tz
                decl.is_draft = False
                decl.submitted_at = tz.now()
                decl.save()
                return redirect('core:booking_detail', club_slug=club_slug, booking_id=booking_id)
        else:
            decl.save()

        if not error:
            return redirect(_decl_url)

    instructors = ClubMember.objects.filter(club=club, is_on_instructor_roster=True).select_related('user')
    passengers = decl.passengers.all()
    frequent_passengers = FrequentPassenger.objects.filter(club_member=booking.member)
    member_nok_name = booking.member.next_of_kin_name
    member_nok_phone = booking.member.next_of_kin_phone

    _is_inline = request.GET.get('inline') == '1'
    return render(request, 'core/booking_declaration.html', {
        'club': club, 'club_member': actor,
        'booking': booking, 'decl': decl, 'error': error,
        'instructors': instructors,
        'passengers': passengers,
        'frequent_passengers': frequent_passengers,
        'member_nok_name': member_nok_name,
        'member_nok_phone': member_nok_phone,
        'readonly': readonly,
        'is_inline': _is_inline,
        'base_template': 'core/base_inline.html' if _is_inline else 'core/base.html',
        'inline_title': f'Bookings <span class="crumb-sep">›</span> {booking.aircraft.registration} {booking.scheduled_start.strftime("%j %b")} <span class="crumb-sep">›</span> <span class="crumb-cur">Declaration</span>',
    })


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

        elif action in ('toggle_aerodrome', 'set_aerodrome_status'):
            ae = Aerodrome.objects.filter(club=club, id=request.POST.get('ae_id')).first()
            if ae:
                if action == 'set_aerodrome_status':
                    ae.is_active = request.POST.get('is_active') == '1'
                else:
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
    """Fuel levy rates moved to individual aircraft detail pages."""
    return redirect('core:manage_aircraft', club_slug=club_slug)


@login_required
def club_rates(request, club_slug):
    """Rates page dissolved — hire rates on aircraft pages, grade/surcharge in Settings."""
    return redirect('core:club_settings', club_slug=club_slug)


# ============================================================================
# INVOICING
# ============================================================================

@login_required
@transaction.atomic
def generate_invoice(request, club_slug, booking_id):
    """Create an Invoice from a completed booking's charge items."""
    club = get_object_or_404(Club, slug=club_slug)
    booking = get_object_or_404(Booking, club=club, id=booking_id)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not (actor.is_admin or actor.is_instructor):
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    fc = getattr(booking, 'flight_completion', None)
    if not fc:
        return redirect('core:booking_detail', club_slug=club_slug, booking_id=booking_id)

    # If invoice already exists, go to it
    if hasattr(fc, 'invoice') and fc.invoice:
        return redirect('core:invoice_detail', club_slug=club_slug, invoice_id=fc.invoice.id)

    if not fc.charge_items.exists() or not fc.total_charge:
        from django.contrib import messages as _msg
        _msg.error(request, 'Cannot generate a $0 invoice — add charge items first.')
        return redirect('core:booking_detail', club_slug=club_slug, booking_id=booking_id)

    config = get_config(club)
    from datetime import date as _date, timedelta as _td

    # Allocate invoice number atomically
    from django.db.models import F
    ClubConfig = config.__class__
    ClubConfig.objects.filter(pk=config.pk).select_for_update().get()
    config.refresh_from_db()
    inv_number = config.invoice_number_next
    config.invoice_number_next = F('invoice_number_next') + 1
    config.save(update_fields=['invoice_number_next'])

    today = _date.today()
    due   = today + _td(days=config.payment_terms_days)

    # Derive description from flight type
    description = booking.flight_type.name if booking.flight_type else ''

    invoice = Invoice.objects.create(
        club=club,
        member=booking.member,
        flight_completion=fc,
        invoice_number=inv_number,
        issue_date=today,
        due_date=due,
        description=description,
        gst_rate=config.gst_rate,
        amount_paid=fc.amount_paid or 0,
        created_by=request.user,
    )

    # Snapshot charge items as line items (FlightChargeItem has description + amount only)
    UNIT_MAP = {'hire': 'Hr', 'instructor': 'Hr', 'fuel': 'Hr', 'landing': 'Ldg',
                'surcharge': 'Ea', 'one_off': 'Ea'}
    for order, ci in enumerate(fc.charge_items.all()):
        InvoiceLineItem.objects.create(
            invoice=invoice,
            description=ci.description or ci.get_item_type_display(),
            quantity=1,
            unit=UNIT_MAP.get(ci.item_type, 'Ea'),
            rate=ci.amount,
            amount=ci.amount,
            sort_order=order,
            charge_item=ci,
        )

    return redirect('core:invoice_detail', club_slug=club_slug, invoice_id=invoice.id)


@login_required
def invoice_detail(request, club_slug, invoice_id):
    """View, edit status, and print an invoice."""
    club    = get_object_or_404(Club, slug=club_slug)
    invoice = get_object_or_404(Invoice, club=club, id=invoice_id)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not (actor.is_admin or actor.is_instructor):
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    config = get_config(club)
    error = success = ''

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'mark_sent' and invoice.status == 'draft':
            invoice.status = 'sent'
            invoice.sent_at = timezone.now()
            invoice.save(update_fields=['status', 'sent_at'])
            from django.contrib import messages as _msg
            _msg.success(request, 'Invoice marked as sent.')
            return redirect('core:invoice_detail', club_slug=club_slug, invoice_id=invoice_id)

        elif action == 'mark_paid' and invoice.status == 'sent':
            from decimal import Decimal as _D
            pay_str = request.POST.get('payment_amount', '').strip()
            try:
                pay_amt = _D(pay_str)
                if pay_amt <= 0:
                    raise ValueError
            except (ValueError, Exception):
                error = 'Enter a valid payment amount.'
                pay_amt = None
            if pay_amt and not error:
                balance = (invoice.total or _D('0')) - (invoice.amount_paid or _D('0'))
                if pay_amt > balance:
                    error = f'Payment ${pay_amt:.2f} exceeds outstanding balance ${balance:.2f}.'
                    pay_amt = None
            if pay_amt and not error:
                invoice.amount_paid = (invoice.amount_paid or _D('0')) + pay_amt
                if invoice.amount_paid >= invoice.total:
                    invoice.status = 'paid'
                    invoice.paid_at = timezone.now()
                invoice.save(update_fields=['amount_paid', 'status', 'paid_at'])
                from django.contrib import messages as _msg
                _msg.success(request, f'Payment of ${pay_amt} recorded.')
                return redirect('core:invoice_detail', club_slug=club_slug, invoice_id=invoice_id)

        elif action == 'void' and invoice.status in ('draft', 'sent'):
            invoice.status = 'void'
            invoice.save(update_fields=['status'])
            from django.contrib import messages as _msg
            _msg.success(request, 'Invoice voided.')
            return redirect('core:invoice_detail', club_slug=club_slug, invoice_id=invoice_id)

        elif action == 'update_description':
            invoice.description = request.POST.get('description', '').strip()
            invoice.notes = request.POST.get('notes', '').strip()
            invoice.save(update_fields=['description', 'notes'])
            success = 'Description saved.'

    line_items = invoice.line_items.all()
    return render(request, 'core/invoice_detail.html', {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'invoice': invoice, 'line_items': line_items, 'config': config,
        'error': error, 'success': success,
    })


@login_required
def invoice_print(request, club_slug, invoice_id):
    """Print-optimised view — browser Ctrl+P / Save as PDF."""
    club    = get_object_or_404(Club, slug=club_slug)
    invoice = get_object_or_404(Invoice, club=club, id=invoice_id)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not (actor.is_admin or actor.is_instructor):
        return render(request, 'core/no_access.html', {'club': club}, status=403)
    config  = get_config(club)
    return render(request, 'core/invoice_print.html', {
        'club': club, 'invoice': invoice,
        'line_items': invoice.line_items.all(), 'config': config,
    })


@login_required
def reports(request, club_slug):
    from django.db.models import Sum
    from django.db.models.functions import TruncMonth
    import json as _json

    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not (actor.is_admin or actor.is_instructor):
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    config, _ = ClubConfig.objects.get_or_create(club=club)
    fy_start = config.fy_start_month

    today = date.today()
    # Determine current FY start date
    fy_year = today.year if today.month >= fy_start else today.year - 1
    fy_start_date = date(fy_year, fy_start, 1)
    import calendar as _cal
    fy_end_month = ((fy_start - 2) % 12) + 1
    fy_end_year = fy_year + 1 if fy_end_month < fy_start else fy_year
    fy_end_date = date(fy_end_year, fy_end_month, _cal.monthrange(fy_end_year, fy_end_month)[1])

    leased_filter = request.GET.get('leased', 'all')  # 'all' | 'owned' | 'leased'

    qs = (FlightCompletion.objects
          .filter(booking__club=club,
                  booking__arrived_at__date__gte=fy_start_date,
                  booking__arrived_at__date__lte=fy_end_date,
                  actual_flight_hours__isnull=False)
          .select_related('booking__aircraft'))
    if leased_filter == 'owned':
        qs = qs.filter(booking__aircraft__is_leased=False)
    elif leased_filter == 'leased':
        qs = qs.filter(booking__aircraft__is_leased=True)

    # Build months list for the FY
    months = []
    m, y = fy_start, fy_year
    for _ in range(12):
        months.append(date(y, m, 1))
        m += 1
        if m > 12:
            m = 1; y += 1
    month_labels = [d.strftime('%b %y') for d in months]

    aircraft_list = (Aircraft.objects.filter(club=club)
                     .exclude(status='retired')
                     .order_by('registration'))

    # hours[aircraft_id][month_index] = hours
    data = {ac.id: [0.0] * 12 for ac in aircraft_list}
    for fc in qs:
        ac_id = fc.booking.aircraft_id
        if ac_id not in data:
            continue
        idx = next((i for i, d in enumerate(months)
                    if d.year == fc.booking.arrived_at.year and d.month == fc.booking.arrived_at.month), None)
        if idx is not None:
            data[ac_id][idx] += float(fc.actual_flight_hours)

    datasets = []
    for i, ac in enumerate(aircraft_list):
        hours = [round(h, 1) for h in data[ac.id]]
        if any(h > 0 for h in hours):
            datasets.append({'label': ac.registration, 'data': hours})

    # ── Instructor hours chart ────────────────────────────────────────────────
    # One bar per instructor, stacked by month — flights where an instructor was assigned.
    instr_qs = (FlightCompletion.objects
                .filter(booking__club=club,
                        booking__arrived_at__date__gte=fy_start_date,
                        booking__arrived_at__date__lte=fy_end_date,
                        booking__instructor__isnull=False,
                        actual_flight_hours__isnull=False)
                .select_related('booking__instructor'))
    from django.contrib.auth import get_user_model as _gum
    _User = _gum()
    instr_ids = list(instr_qs.values_list('booking__instructor_id', flat=True).distinct())
    instr_users = {u.id: u for u in _User.objects.filter(id__in=instr_ids)}
    instr_data = {uid: [0.0] * 12 for uid in instr_ids}
    for fc in instr_qs:
        uid = fc.booking.instructor_id
        idx = next((i for i, d in enumerate(months)
                    if d.year == fc.booking.arrived_at.year and d.month == fc.booking.arrived_at.month), None)
        if idx is not None:
            instr_data[uid][idx] += float(fc.actual_flight_hours)
    instr_datasets = []
    for i, uid in enumerate(instr_ids):
        u = instr_users.get(uid)
        label = u.get_full_name() if u else f'Instructor {uid}'
        hours = [round(h, 1) for h in instr_data[uid]]
        if any(h > 0 for h in hours):
            instr_datasets.append({'label': label, 'data': hours})

    # ── Member activity table ─────────────────────────────────────────────────
    all_qs = (FlightCompletion.objects
              .filter(booking__club=club,
                      booking__arrived_at__date__gte=fy_start_date,
                      booking__arrived_at__date__lte=fy_end_date,
                      actual_flight_hours__isnull=False)
              .select_related('booking__member__user'))
    member_stats = {}
    for fc in all_qs:
        m = fc.booking.member
        if m not in member_stats:
            member_stats[m] = {'count': 0, 'hours': 0.0}
        member_stats[m]['count'] += 1
        member_stats[m]['hours'] += float(fc.actual_flight_hours)
    member_rows = sorted(
        [{'name': m.user.get_full_name(), 'flight_count': v['count'],
          'total_hours': round(v['hours'], 1)}
         for m, v in member_stats.items()],
        key=lambda r: -r['total_hours']
    )

    # ── Payment summary by month ───────────────────────────────────────────────
    from django.db.models import Sum as _Sum, Count as _Count
    payment_rows = []
    pay_total_count = pay_total_charged = pay_total_collected = 0
    for i, m_date in enumerate(months):
        month_fcs = [fc for fc in all_qs
                     if fc.booking.arrived_at.year == m_date.year
                     and fc.booking.arrived_at.month == m_date.month]
        charged   = sum(float(fc.total_charge) for fc in month_fcs)
        collected = sum(float(fc.amount_paid) for fc in month_fcs)
        if charged or collected:
            payment_rows.append({
                'label': m_date.strftime('%b %y'),
                'count': len(month_fcs),
                'charged':   round(charged, 2),
                'collected': round(collected, 2),
            })
            pay_total_count     += len(month_fcs)
            pay_total_charged   += charged
            pay_total_collected += collected

    # ── Dashboard KPIs ─────────────────────────────────────────────────────────
    from .models import Account as _DAcct
    from django.db.models import Sum as _DSum, Count as _DCnt

    dash_total_hours      = round(sum(float(fc.actual_flight_hours) for fc in all_qs), 1)
    dash_revenue_billed   = round(sum(float(fc.total_charge or 0)   for fc in all_qs), 2)
    dash_revenue_collected= round(sum(float(fc.amount_paid or 0)    for fc in all_qs), 2)
    dash_flights_count    = sum(1 for _ in all_qs)
    dash_instr_hours      = round(sum(
        float(fc.actual_flight_hours) for fc in all_qs if fc.booking.instructor_id
    ), 1)
    active_members_count  = ClubMember.objects.filter(club=club, standing='active').count()

    _debt_agg = (_DAcct.objects.filter(club_member__club=club, balance__lt=0)
                 .aggregate(total=_DSum('balance'), cnt=_DCnt('id')))
    dash_outstanding_debt = abs(round(float(_debt_agg['total'] or 0), 2))
    dash_debtor_count     = _debt_agg['cnt'] or 0

    # Monthly arrays for dashboard charts (all flights, no leased filter)
    _dash_billed = [0.0] * 12
    _dash_collected = [0.0] * 12
    for fc in all_qs:
        if fc.booking.arrived_at:
            _idx = next((i for i, d in enumerate(months)
                         if d.year == fc.booking.arrived_at.year and d.month == fc.booking.arrived_at.month), None)
            if _idx is not None:
                _dash_billed[_idx]    += float(fc.total_charge or 0)
                _dash_collected[_idx] += float(fc.amount_paid or 0)
    dash_monthly_billed    = _json.dumps([round(v, 0) for v in _dash_billed])
    dash_monthly_collected = _json.dumps([round(v, 0) for v in _dash_collected])

    # Top aircraft/members for dashboard tables
    _ac_hrs_db = (FlightCompletion.objects
                  .filter(booking__club=club,
                          booking__arrived_at__date__gte=fy_start_date,
                          booking__arrived_at__date__lte=fy_end_date,
                          actual_flight_hours__isnull=False)
                  .values('booking__aircraft__registration')
                  .annotate(hrs=_DSum('actual_flight_hours'), cnt=_DCnt('id'))
                  .order_by('-hrs')[:6])
    top_aircraft_dash = [{'reg': r['booking__aircraft__registration'],
                          'hours': round(float(r['hrs']), 1),
                          'count': r['cnt']} for r in _ac_hrs_db]
    top_members_dash  = member_rows[:6]

    # ── Occurrence analytics ──────────────────────────────────────────────────
    from django.db.models import Count as _OCount
    from django.db.models.functions import TruncMonth as _TrMonth

    # All-time totals
    occ_qs = OccurrenceReport.objects.filter(club=club)
    occ_total      = occ_qs.count()
    occ_open       = occ_qs.filter(status=OccurrenceReport.STATUS_SUBMITTED).count()
    occ_reviewed   = occ_qs.filter(status=OccurrenceReport.STATUS_REVIEWED).count()
    occ_closed     = occ_qs.filter(status=OccurrenceReport.STATUS_CLOSED).count()

    # FY-scoped occurrence counts for dashboard KPI
    occ_fy_qs   = occ_qs.filter(date_of_occurrence__gte=fy_start_date,
                                 date_of_occurrence__lte=fy_end_date)
    occ_fy_total = occ_fy_qs.count()
    occ_fy_open  = occ_fy_qs.filter(status=OccurrenceReport.STATUS_SUBMITTED).count()

    # By type (all time)
    by_type = list(
        occ_qs.values('occurrence_type__name')
        .annotate(count=_OCount('id'))
        .order_by('-count')
    )
    occ_type_labels = _json.dumps([r['occurrence_type__name'] for r in by_type])
    occ_type_data   = _json.dumps([r['count'] for r in by_type])

    # By month (last 12 months, rolling)
    from datetime import timedelta as _td
    twelve_months_ago = today - _td(days=365)
    monthly_qs = (occ_qs
                  .filter(date_of_occurrence__gte=twelve_months_ago)
                  .annotate(m=_TrMonth('date_of_occurrence'))
                  .values('m')
                  .annotate(count=_OCount('id'))
                  .order_by('m'))
    monthly_map = {r['m'].strftime('%b %y'): r['count'] for r in monthly_qs}
    # Build last 12 month labels (oldest first)
    occ_month_labels_list = []
    _my, _mm = today.year, today.month
    for _back in range(11, -1, -1):
        _m2 = _mm - _back
        _y2 = _my
        while _m2 <= 0:
            _m2 += 12; _y2 -= 1
        occ_month_labels_list.append(date(_y2, _m2, 1).strftime('%b %y'))
    occ_month_labels = _json.dumps(occ_month_labels_list)
    occ_month_data   = _json.dumps([monthly_map.get(lbl, 0) for lbl in occ_month_labels_list])

    # By aircraft (top 10, all time)
    by_aircraft = list(
        occ_qs.filter(aircraft__isnull=False)
        .values('aircraft__registration')
        .annotate(count=_OCount('id'))
        .order_by('-count')[:10]
    )
    occ_ac_labels = _json.dumps([r['aircraft__registration'] for r in by_aircraft])
    occ_ac_data   = _json.dumps([r['count'] for r in by_aircraft])

    return render(request, 'core/reports.html', {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'month_labels': _json.dumps(month_labels),
        'datasets': _json.dumps(datasets),
        'instr_datasets': _json.dumps(instr_datasets),
        'leased_filter': leased_filter,
        'fy_label': f"{fy_start_date.strftime('%b %Y')} – {fy_end_date.strftime('%b %Y')}",
        'member_rows': member_rows,
        'payment_rows': payment_rows,
        'payment_total_count': pay_total_count,
        'payment_total_charged': round(pay_total_charged, 2),
        'payment_total_collected': round(pay_total_collected, 2),
        'occ_total': occ_total,
        'occ_stats': [
            ('Total', occ_total, '#1a1f2e'),
            ('Open', occ_open, '#c0392b'),
            ('Reviewed', occ_reviewed, '#216c2a'),
            ('Closed', occ_closed, '#8a93a0'),
        ],
        'occ_type_labels': occ_type_labels, 'occ_type_data': occ_type_data,
        'occ_month_labels': occ_month_labels, 'occ_month_data': occ_month_data,
        'occ_ac_labels': occ_ac_labels, 'occ_ac_data': occ_ac_data,
        # Dashboard
        'dash_total_hours': dash_total_hours,
        'dash_revenue_billed': dash_revenue_billed,
        'dash_revenue_collected': dash_revenue_collected,
        'dash_flights_count': dash_flights_count,
        'dash_instr_hours': dash_instr_hours,
        'active_members_count': active_members_count,
        'dash_outstanding_debt': dash_outstanding_debt,
        'dash_debtor_count': dash_debtor_count,
        'dash_monthly_billed': dash_monthly_billed,
        'dash_monthly_collected': dash_monthly_collected,
        'top_aircraft_dash': top_aircraft_dash,
        'top_members_dash': top_members_dash,
        'occ_fy_total': occ_fy_total,
        'occ_fy_open': occ_fy_open,
        'ai_suggestions': [
            'Which aircraft flew the most hours this year?',
            'How much revenue did we collect last month?',
            'Who are the most active members this year?',
            'Which instructor has the most hours this year?',
            'Are any aircraft overdue for maintenance?',
            'How many active members do we have?',
        ],
    })


@login_required
def reports_pivot(request, club_slug):
    """AJAX pivot-table builder — returns aggregated flight data as JSON."""
    import json as _json
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return JsonResponse({'error': 'Access denied'}, status=403)
    if not (actor.is_admin or actor.is_instructor):
        return JsonResponse({'error': 'Access denied'}, status=403)

    rows   = request.GET.getlist('rows')
    values = request.GET.getlist('values')
    aggs   = request.GET.getlist('aggs')
    date_from_str = request.GET.get('date_from', '').strip()
    date_to_str   = request.GET.get('date_to', '').strip()

    VALID_DIMS    = {'aircraft', 'aircraft_type', 'flight_type', 'member',
                     'instructor', 'month', 'quarter', 'year', 'outcome'}
    VALID_METRICS = {'hours', 'charge', 'paid', 'flights', 'outstanding'}

    if not rows or not values:
        return JsonResponse({'error': 'rows and values required'}, status=400)
    for r in rows:
        if r not in VALID_DIMS:
            return JsonResponse({'error': f'Unknown dimension: {r}'}, status=400)
    for v in values:
        if v not in VALID_METRICS:
            return JsonResponse({'error': f'Unknown metric: {v}'}, status=400)

    qs = (FlightCompletion.objects
          .filter(booking__club=club, actual_flight_hours__isnull=False)
          .select_related('booking__aircraft__aircraft_type',
                          'booking__member__user',
                          'booking__instructor',
                          'booking__flight_type'))
    if date_from_str:
        try:
            qs = qs.filter(booking__arrived_at__date__gte=date_from_str)
        except Exception:
            pass
    if date_to_str:
        try:
            qs = qs.filter(booking__arrived_at__date__lte=date_to_str)
        except Exception:
            pass

    def get_dim(fc, dim):
        b = fc.booking
        if dim == 'aircraft':
            return b.aircraft.registration if b.aircraft_id else '—'
        if dim == 'aircraft_type':
            ac = b.aircraft
            return ac.aircraft_type.name if (ac and ac.aircraft_type_id) else '—'
        if dim == 'flight_type':
            return b.flight_type.name if b.flight_type_id else '—'
        if dim == 'member':
            m = b.member
            return m.user.get_full_name() if m else '—'
        if dim == 'instructor':
            i = b.instructor
            return i.get_full_name() if i else '(solo)'
        if dim == 'month':
            return b.arrived_at.strftime('%b %Y') if b.arrived_at else '—'
        if dim == 'quarter':
            if not b.arrived_at:
                return '—'
            q = ((b.arrived_at.month - 1) // 3) + 1
            return f'Q{q} {b.arrived_at.year}'
        if dim == 'year':
            return str(b.arrived_at.year) if b.arrived_at else '—'
        if dim == 'outcome':
            return fc.get_outcome_display()
        return '—'

    def get_metric(fc, metric):
        if metric == 'hours':
            return float(fc.actual_flight_hours or 0)
        if metric == 'charge':
            return float(fc.total_charge or 0)
        if metric == 'paid':
            return float(fc.amount_paid or 0)
        if metric == 'flights':
            return 1
        if metric == 'outstanding':
            return float(max(0, (fc.total_charge or 0) - (fc.amount_paid or 0)))
        return 0

    buckets = {}
    for fc in qs.iterator():
        key = tuple(get_dim(fc, d) for d in rows)
        if key not in buckets:
            buckets[key] = {v: [] for v in values}
        for v in values:
            buckets[key][v].append(get_metric(fc, v))

    agg_map = {}
    for i, v in enumerate(values):
        if v == 'flights':
            agg_map[v] = 'count'
        else:
            agg_map[v] = aggs[i] if i < len(aggs) and aggs[i] in ('sum', 'avg', 'count') else 'sum'

    def do_agg(vals, agg):
        if not vals:
            return 0
        if agg == 'avg':
            return round(sum(vals) / len(vals), 2)
        if agg == 'count':
            return int(sum(vals))
        return round(sum(vals), 2)

    def sort_key(item):
        key = item[0]
        from datetime import datetime as _dt
        try:
            parts = key[0].split()
            if len(parts) == 2:
                mn = _dt.strptime(parts[0], '%b').month
                return (int(parts[1]), mn, '')
        except Exception:
            pass
        try:
            parts = key[0].split()
            if len(parts) == 2 and parts[0].startswith('Q'):
                return (int(parts[1]), int(parts[0][1]), '')
        except Exception:
            pass
        return (0, 0, key[0])

    result_rows = []
    for key, mdata in sorted(buckets.items(), key=sort_key):
        row = list(key) + [do_agg(mdata[v], agg_map[v]) for v in values]
        result_rows.append(row)

    n_dims = len(rows)
    totals = ['Total'] + [''] * (n_dims - 1)
    for i, v in enumerate(values):
        col_vals = [r[n_dims + i] for r in result_rows]
        agg = agg_map[v]
        if agg == 'avg':
            totals.append(round(sum(col_vals) / len(col_vals), 2) if col_vals else 0)
        elif agg == 'count':
            totals.append(int(sum(col_vals)))
        else:
            totals.append(round(sum(col_vals), 2))

    LABELS = {
        'aircraft': 'Aircraft', 'aircraft_type': 'A/C type', 'flight_type': 'Flight type',
        'member': 'Member', 'instructor': 'Instructor',
        'month': 'Month', 'quarter': 'Quarter', 'year': 'Year', 'outcome': 'Outcome',
        'hours': 'Hours', 'charge': 'Charge ($)', 'paid': 'Paid ($)',
        'flights': 'Flights', 'outstanding': 'Outstanding ($)',
    }
    headers = [LABELS.get(f, f) for f in rows + values]

    return JsonResponse({'headers': headers, 'rows': result_rows,
                         'totals': totals, 'count': len(result_rows)})


@login_required
def ai_ask(request, club_slug):
    """Natural-language query endpoint — fetches club data upfront and passes as context to Groq."""
    import json as _json
    from django.conf import settings as _settings
    from groq import Groq

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return JsonResponse({'error': 'Not a member'}, status=403)
    if not actor.is_admin:
        return JsonResponse({'error': 'Admin access required'}, status=403)

    question = request.POST.get('question', '').strip()
    if not question:
        return JsonResponse({'error': 'No question provided'}, status=400)

    api_key = _settings.GROQ_API_KEY
    if not api_key:
        return JsonResponse({'error': 'AI not configured — add GROQ_API_KEY to .env and restart the server.'}, status=503)

    # ── Build club data snapshot ──────────────────────────────────────────────
    # Members
    members = ClubMember.objects.filter(club=club).select_related('role', 'user')
    member_rows = []
    for m in members:
        member_rows.append({
            'name': m.user.get_full_name(),
            'role': m.role.name if m.role else 'No role',
            'standing': m.standing or 'unknown',
            'subscription_expires': str(m.subscription_expires) if m.subscription_expires else None,
        })

    # Completed flights — all time
    fcs = (FlightCompletion.objects
           .filter(booking__club=club, actual_flight_hours__isnull=False)
           .select_related('booking__aircraft', 'booking__instructor', 'booking__member__user')
           .prefetch_related('charge_items')
           .order_by('booking__arrived_at'))
    flight_rows = []
    for fc in fcs:
        b = fc.booking
        charged = sum(float(ci.amount) for ci in fc.charge_items.all())
        flight_rows.append({
            'date': b.arrived_at.strftime('%Y-%m-%d') if b.arrived_at else None,
            'aircraft': b.aircraft.registration if b.aircraft else None,
            'member': b.member.user.get_full_name() if b.member else None,
            'instructor': b.instructor.get_full_name() if b.instructor else None,
            'hours': round(float(fc.actual_flight_hours), 1),
            'charged': round(charged, 2),
            'paid': round(float(fc.amount_paid or 0), 2),
        })

    # Aircraft
    aircraft_rows = []
    for ac in Aircraft.objects.filter(club=club).exclude(status='retired').prefetch_related('maintenance_items'):
        items = list(ac.maintenance_items.all())
        aircraft_rows.append({
            'registration': ac.registration,
            'type': ac.aircraft_type.name if ac.aircraft_type else 'Unknown',
            'status': ac.status,
            'is_leased': ac.is_leased,
            'overdue_maintenance': [m.name for m in items if getattr(m, 'urgency', '') == 'red'],
            'warning_maintenance': [m.name for m in items if getattr(m, 'urgency', '') == 'amber'],
        })

    # Account balances
    from .models import Account as _Acct
    account_rows = []
    for acc in _Acct.objects.filter(club_member__club=club).select_related('club_member__user'):
        account_rows.append({
            'member': acc.club_member.user.get_full_name(),
            'balance': round(float(acc.balance), 2),
            'credit_limit': float(acc.credit_limit) if acc.credit_limit is not None else None,
        })

    # Occurrences (last 2 years)
    from .models import OccurrenceReport as _OR
    occ_rows = []
    for o in _OR.objects.filter(club=club).select_related('occurrence_type', 'reported_by__user').order_by('-date_of_occurrence')[:100]:
        occ_rows.append({
            'date': str(o.date_of_occurrence),
            'type': o.occurrence_type.name,
            'status': o.status,
            'is_safety_risk': o.is_safety_risk,
            'reported_by': o.reported_by.user.get_full_name() if o.reported_by else None,
        })

    data_context = _json.dumps({
        'members': member_rows,
        'completed_flights': flight_rows,
        'aircraft': aircraft_rows,
        'account_balances': account_rows,
        'occurrences': occ_rows,
    }, default=str)

    # ── Groq call ─────────────────────────────────────────────────────────────
    try:
        client = Groq(api_key=api_key)
        system = (
            f"You are a helpful data assistant for {club.name}, an aviation club. "
            f"Today is {date.today().strftime('%d %B %Y')}. "
            "The following JSON contains club data — members, completed flights, aircraft, "
            "account balances, and safety occurrences. "
            "Answer questions using only this data. Be concise. "
            "Format currency as $X,XXX.XX and hours as X.X hrs.\n\n"
            f"CLUB DATA:\n{data_context}"
        )
        resp = client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[
                {'role': 'system', 'content': system},
                {'role': 'user',   'content': question},
            ],
            max_tokens=1024,
        )
        return JsonResponse({'answer': resp.choices[0].message.content})
    except Exception as exc:
        return JsonResponse({'error': f'AI error: {exc}'}, status=500)


@login_required
def notifications(request, club_slug):
    club = get_object_or_404(Club, slug=club_slug)
    try:
        member = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')

    # Auto-prune: delete notifications older than 90 days (keeps the list bounded)
    from django.utils import timezone as _tz
    cutoff = _tz.now() - __import__('datetime').timedelta(days=90)
    member.notifications.filter(created_at__lt=cutoff).delete()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'mark_read':
            member.notifications.filter(id=request.POST.get('id')).update(is_read=True)
        elif action == 'mark_all_read':
            member.notifications.filter(is_read=False).update(is_read=True)
        elif action == 'delete':
            member.notifications.filter(id=request.POST.get('id')).delete()
        elif action == 'delete_all_read':
            member.notifications.filter(is_read=True).delete()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            from django.http import JsonResponse
            return JsonResponse({'ok': True})
        _profile_url = redirect('core:notifications', club_slug=club_slug).url
        return redirect(_profile_url + '?saved=1')

    tab = request.GET.get('tab', 'unread')
    qs = member.notifications.all()
    if tab == 'unread':
        qs = qs.filter(is_read=False)
    items = list(qs[:100])
    unread_count = member.notifications.filter(is_read=False).count()
    return render(request, 'core/notifications.html', {
        'club': club, 'club_member': member, 'is_instructor': member.is_instructor,
        'notifications': items,
        'tab': tab,
        'unread_count': unread_count,
    })


@login_required
def manage_invoices(request, club_slug):
    """List all invoices with overdue indicators."""
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not (actor.is_admin or actor.is_instructor):
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    f_status = request.GET.get('status', '')
    f_member = request.GET.get('member', '')

    qs = (Invoice.objects.filter(club=club)
          .select_related('member__user', 'flight_completion__booking')
          .prefetch_related('line_items')
          .order_by('-invoice_number'))

    if f_status:
        qs = qs.filter(status=f_status)
    if f_member:
        qs = qs.filter(member__user_id=f_member)

    invoices = list(qs)
    members_qs = ClubMember.objects.filter(club=club).select_related('user').order_by('user__last_name')

    from datetime import date as _date
    today = _date.today()
    overdue_count = sum(1 for i in invoices if i.is_overdue)

    return render(request, 'core/manage_invoices.html', {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'invoices': invoices, 'f_status': f_status, 'f_member': f_member,
        'members_qs': members_qs, 'overdue_count': overdue_count,
        'status_choices': Invoice.STATUS_CHOICES,
    })


@login_required
def manage_exceptions(request, club_slug):
    from datetime import date as _date
    from django.db.models import Q as _Q
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not actor.can_access_manage:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    today = _date.today()

    # 1. Unpaid completed flights
    unpaid_flights = (
        Booking.objects
        .filter(club=club, status='completed',
                flight_completion__paid_at__isnull=True,
                flight_completion__total_charge__gt=0,
                flight_completion__invoice__isnull=True)
        .select_related('member__user', 'aircraft', 'flight_type', 'flight_completion')
        .order_by('arrived_at')
    )

    # Pre-load instructor availability for off-roster detection
    from .models import InstructorAvailability as _IA
    _roster_uids = set(
        ClubMember.objects.filter(club=club, is_on_instructor_roster=True).values_list('user_id', flat=True)
    )
    _av_wins = {}  # user_id -> list[InstructorAvailability]
    for av in _IA.objects.filter(club_member__club=club).select_related('club_member'):
        _av_wins.setdefault(av.club_member.user_id, []).append(av)

    def _instr_off_roster(booking):
        uid = booking.instructor_id
        if not uid or uid not in _roster_uids:
            return False
        windows = _av_wins.get(uid, [])
        if not windows:
            return True  # no schedule = not available
        return not any(w.applies_on(booking.scheduled_start.date()) for w in windows)

    # 2. Booking conflicts (future non-cancelled bookings with a flagged issue)
    _db_conflicts = list(
        Booking.objects
        .filter(club=club, scheduled_start__date__gte=today)
        .exclude(status__in=['cancelled', 'completed'])
        .filter(
            _Q(blockout_conflict=True) |
            _Q(member__standing__in=['suspended', 'lapsed', 'resigned']) |
            _Q(member__standing='active',
               member__subscription_expires__isnull=False,
               member__subscription_expires__lt=today) |
            _Q(aircraft__status='retired')
        )
        .select_related('member__user', 'aircraft', 'flight_type', 'instructor')
        .order_by('scheduled_start')
    )
    _seen_ids = {b.id for b in _db_conflicts}
    # Add instructor-off-roster bookings not caught by the DB query
    _instr_conflicts = [
        b for b in (
            Booking.objects
            .filter(club=club, scheduled_start__date__gte=today, instructor__isnull=False)
            .exclude(status__in=['cancelled', 'completed'])
            .exclude(id__in=_seen_ids)
            .select_related('member__user', 'aircraft', 'flight_type', 'instructor')
            .order_by('scheduled_start')
        )
        if _instr_off_roster(b)
    ]
    conflicts = sorted(_db_conflicts + _instr_conflicts, key=lambda b: b.scheduled_start)

    def _conflict_labels(b):
        labels = []
        if b.blockout_conflict:
            labels.append(b.blockout_conflict_reason or 'Block-out conflict')
        if b.member:
            if b.member.standing in ('suspended', 'lapsed', 'resigned'):
                labels.append(f'Member {b.member.get_standing_display()}')
            elif (b.member.standing == 'active' and b.member.subscription_expires
                  and b.member.subscription_expires < today):
                labels.append('Subscription expired')
        if b.aircraft and b.aircraft.status == 'retired':
            labels.append('Aircraft retired')
        if _instr_off_roster(b):
            labels.append('Instructor off roster')
        return labels

    conflicts_data = [{'b': b, 'labels': _conflict_labels(b)} for b in conflicts]

    # 3. Lapsed/expired credentials — members with upcoming bookings whose
    #    credentials have expired (any credential with an expiry_date in the past)
    from .models import MemberCredential
    future_member_ids = (
        Booking.objects
        .filter(club=club, scheduled_start__date__gte=today)
        .exclude(status__in=['cancelled', 'completed'])
        .values_list('member_id', flat=True)
        .distinct()
    )
    expired_cred_member_ids = (
        MemberCredential.objects
        .filter(club_member__club=club,
                club_member_id__in=future_member_ids,
                expiry_date__lt=today)
        .values_list('club_member_id', flat=True)
        .distinct()
    )
    members_lapsed_creds = (
        ClubMember.objects
        .filter(id__in=expired_cred_member_ids)
        .select_related('user')
        .prefetch_related('credentials')
        .order_by('user__last_name')
    )
    lapsed_creds_data = []
    for cm in members_lapsed_creds:
        expired = [c for c in cm.credentials.all() if c.expiry_date and c.expiry_date < today]
        upcoming = (Booking.objects
                    .filter(club=club, member=cm, scheduled_start__date__gte=today)
                    .exclude(status__in=['cancelled', 'completed'])
                    .order_by('scheduled_start').first())
        lapsed_creds_data.append({'cm': cm, 'expired': expired, 'next_booking': upcoming})

    # 4. Maintenance — amber and red items on online aircraft
    from .models import AircraftMaintenanceItem, MaintenanceUrgency
    from django.db.models import Case, When, IntegerField as _IntF
    maint_items = (
        AircraftMaintenanceItem.objects
        .filter(aircraft__club=club, aircraft__status='online',
                urgency__in=[MaintenanceUrgency.AMBER, MaintenanceUrgency.RED])
        .select_related('aircraft')
        .order_by(
            Case(
                When(urgency=MaintenanceUrgency.RED, then=0),
                When(urgency=MaintenanceUrgency.AMBER, then=1),
                default=2, output_field=_IntF()
            ),
            'aircraft__registration', 'name'
        )
    )

    # 5. Unpaid invoices — draft or sent (not yet paid)
    from .models import Invoice as _Inv
    unpaid_invoices = (
        _Inv.objects
        .filter(club=club, status__in=(_Inv.STATUS_DRAFT, _Inv.STATUS_SENT))
        .select_related('member__user', 'flight_completion__booking__aircraft')
        .order_by('due_date', 'invoice_number')
    )

    # Overdue returns — departed >24h with no check-in (potential safety issue)
    _overdue_cutoff = timezone.now() - timedelta(hours=24)
    overdue_departures = list(
        Booking.objects
        .filter(club=club, status='departed', departed_at__lt=_overdue_cutoff)
        .select_related('member__user', 'aircraft')
        .order_by('departed_at')
    )

    total_issues = (
        len(overdue_departures) +
        unpaid_flights.count() + len(conflicts_data) +
        len(lapsed_creds_data) + maint_items.count() +
        unpaid_invoices.count()
    )

    return render(request, 'core/manage_exceptions.html', {
        'club': club,
        'club_member': actor,
        'unpaid_flights': unpaid_flights,
        'conflicts_data': conflicts_data,
        'lapsed_creds_data': lapsed_creds_data,
        'maint_items': maint_items,
        'unpaid_invoices': unpaid_invoices,
        'overdue_departures': overdue_departures,
        'total_issues': total_issues,
        'today': today,
    })


@login_required
def manage_vouchers(request, club_slug):
    from .models import Voucher, Account, AccountTransaction
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not actor.is_admin:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'create_voucher':
            code = request.POST.get('code', '').strip().upper()
            value = request.POST.get('value', '').strip()
            desc = request.POST.get('description', '').strip()
            notes = request.POST.get('notes', '').strip()
            if code and value:
                try:
                    Voucher.objects.create(
                        club=club, code=code, value=value,
                        description=desc, notes=notes, created_by=request.user,
                    )
                    from django.contrib import messages as _messages
                    _messages.success(request, f'Voucher {code} created (${value}).')
                except Exception:
                    from django.contrib import messages as _messages
                    _messages.info(request, f'Voucher code {code} already exists.')

        elif action == 'redeem_voucher':
            voucher_id = request.POST.get('voucher_id')
            member_id  = request.POST.get('member_id')
            v = Voucher.objects.filter(club=club, id=voucher_id, is_redeemed=False).first()
            member = ClubMember.objects.filter(club=club, id=member_id).first()
            if v and member:
                from django.utils import timezone as _tz
                from django.contrib import messages as _messages
                acct, _ = Account.objects.get_or_create(
                    club_member=member, defaults={'balance': 0}
                )
                AccountTransaction.objects.create(
                    account=acct,
                    transaction_type='top_up',
                    direction='credit',
                    amount=v.value,
                    description=f'Voucher {v.code} redeemed — {v.description or "credit"}',
                    payment_method='other',
                    created_by=request.user,
                )
                acct.balance = acct.transactions.filter(direction='credit').aggregate(
                    s=models.Sum('amount'))['s'] or 0
                acct.balance -= acct.transactions.filter(direction='debit').aggregate(
                    s=models.Sum('amount'))['s'] or 0
                acct.save(update_fields=['balance'])
                v.is_redeemed = True
                v.redeemed_by = member
                v.redeemed_at = _tz.now()
                v.save()
                _messages.success(request, f'Voucher {v.code} redeemed — ${v.value} credited to {member.user.get_full_name()}.')

        return redirect('core:manage_vouchers', club_slug=club_slug)

    f_show = request.GET.get('show', 'pending')  # pending | redeemed | all
    qs = Voucher.objects.filter(club=club).select_related('redeemed_by__user', 'created_by')
    if f_show == 'pending':
        qs = qs.filter(is_redeemed=False)
    elif f_show == 'redeemed':
        qs = qs.filter(is_redeemed=True)
    members = ClubMember.objects.filter(club=club).select_related('user').order_by('user__last_name')

    return render(request, 'core/manage_vouchers.html', {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'vouchers': qs, 'f_show': f_show, 'members': members,
        'voucher_types': VoucherType.objects.filter(club=club, is_active=True),
    })


@login_required
def manage_sundry(request, club_slug):
    from .models import Account, AccountTransaction as _AT
    from decimal import Decimal as _D
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not actor.can_access_manage:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    error = None
    if request.method == 'POST':
        member_id  = request.POST.get('member_id', '').strip()
        description = request.POST.get('description', '').strip()
        amount_str  = request.POST.get('amount', '').strip()
        reference   = request.POST.get('reference', '').strip()
        member = ClubMember.objects.filter(club=club, id=member_id).select_related('user').first()
        try:
            amount = _D(amount_str)
            if amount <= 0:
                raise ValueError
        except (ValueError, Exception):
            amount = None
        if not member:
            error = 'Select a member.'
        elif not description:
            error = 'Description is required.'
        elif not amount:
            error = 'Enter a positive amount.'
        else:
            account, _ = Account.objects.get_or_create(club_member=member)
            _AT.objects.create(
                account=account,
                transaction_type='sale',
                direction='debit',
                amount=amount,
                description=description,
                reference=reference,
                created_by=request.user,
            )
            account.balance = account.balance - amount
            account.save(update_fields=['balance'])
            return redirect(f'{request.path}?saved=1')

    members = (ClubMember.objects
               .filter(club=club)
               .exclude(standing='resigned')
               .select_related('user')
               .order_by('user__last_name', 'user__first_name'))

    q_sales = request.GET.get('q', '').strip()
    sales_qs = (_AT.objects
                .filter(account__club_member__club=club, transaction_type='sale')
                .select_related('account__club_member__user', 'created_by')
                .order_by('-created_at'))
    if q_sales:
        from django.db.models import Q as _Q
        sales_qs = sales_qs.filter(
            _Q(account__club_member__user__first_name__icontains=q_sales) |
            _Q(account__club_member__user__last_name__icontains=q_sales) |
            _Q(description__icontains=q_sales) |
            _Q(reference__icontains=q_sales)
        )
    recent_sales = sales_qs[:30]
    sales_total = sales_qs.count()

    return render(request, 'core/manage_sundry.html', {
        'club': club, 'club_member': actor,
        'members': members,
        'recent_sales': recent_sales,
        'sales_total': sales_total,
        'q_sales': q_sales,
        'modal_error': error,
        'modal_error_id': 'new-sale-modal' if error else None,
        'modal_error_values': {
            'member_id': request.POST.get('member_id', ''),
            'description': request.POST.get('description', ''),
            'amount': request.POST.get('amount', ''),
            'reference': request.POST.get('reference', ''),
        } if error else {},
    })


@login_required
def health_check(request, club_slug):
    from decimal import Decimal as _D
    from django.db.models import Sum, F
    from django.utils import timezone as _tz

    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not actor.is_admin:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    # group -> {label, issues: [{severity, message, detail}], ok: [str]}
    groups = {
        'financial':  {'label': 'Financial',  'issues': [], 'ok': []},
        'operations': {'label': 'Operations', 'issues': [], 'ok': []},
        'members':    {'label': 'Members',    'issues': [], 'ok': []},
    }

    def _issue(group, sev, msg, detail=''):
        groups[group]['issues'].append({'severity': sev, 'message': msg, 'detail': detail})

    def _ok(group, name):
        groups[group]['ok'].append(name)

    # ── 1. Account balance drift ──────────────────────────────────────────────
    from .models import Account
    balance_drifts = []
    for acc in Account.objects.filter(club_member__club=club).select_related('club_member__user'):
        computed = acc.recompute_balance()
        if abs(_D(str(computed)) - acc.balance) > _D('0.01'):
            balance_drifts.append(
                f"{acc.club_member.user.get_full_name()}: stored ${acc.balance}, computed ${computed:.2f}"
            )
    if balance_drifts:
        _issue('financial', 'err',
               f"{len(balance_drifts)} account balance(s) don't match ledger sum",
               '\n'.join(balance_drifts))
    else:
        _ok('financial', 'Account balances')

    # ── 2. FlightCompletion charge drift ─────────────────────────────────────
    charge_drifts = []
    for fc in (FlightCompletion.objects
               .filter(booking__club=club)
               .prefetch_related('charge_items')
               .select_related('booking__member__user', 'booking__aircraft')):
        computed = sum(ci.amount for ci in fc.charge_items.all()) or _D('0')
        if abs(computed - fc.total_charge) > _D('0.01'):
            charge_drifts.append(
                f"FC #{fc.id} ({fc.booking.aircraft.registration if fc.booking.aircraft else '?'} "
                f"{fc.booking.scheduled_start.strftime('%d %b %y') if fc.booking.scheduled_start else '?'}): "
                f"stored ${fc.total_charge}, items sum ${computed:.2f}"
            )
    if charge_drifts:
        _issue('financial', 'err',
               f"{len(charge_drifts)} flight completion(s) have charge total mismatches",
               '\n'.join(charge_drifts))
    else:
        _ok('financial', 'Flight charges')

    # ── 3. FlightCompletion payment drift ────────────────────────────────────
    from .models import FlightPayment as _FP
    payment_drifts = []
    for fc in (FlightCompletion.objects
               .filter(booking__club=club)
               .select_related('booking__aircraft')):
        computed = _FP.objects.filter(completion=fc).aggregate(t=Sum('amount'))['t'] or _D('0')
        if abs(computed - fc.amount_paid) > _D('0.01'):
            payment_drifts.append(
                f"FC #{fc.id} ({fc.booking.aircraft.registration if fc.booking.aircraft else '?'} "
                f"{fc.booking.scheduled_start.strftime('%d %b %y') if fc.booking.scheduled_start else '?'}): "
                f"stored ${fc.amount_paid}, payments sum ${computed:.2f}"
            )
    if payment_drifts:
        _issue('financial', 'err',
               f"{len(payment_drifts)} flight completion(s) have payment total mismatches",
               '\n'.join(payment_drifts))
    else:
        _ok('financial', 'Flight payments')

    # ── 4. Meter hour gaps ───────────────────────────────────────────────────
    from .models import MaintenanceLogEntry
    meter_gaps = []
    for ac in Aircraft.objects.filter(club=club).exclude(status='retired'):
        fcs = list(
            FlightCompletion.objects
            .filter(booking__aircraft=ac, hobbs_end__isnull=False, hobbs_start__isnull=False)
            .order_by('booking__departed_at')
            .values('id', 'hobbs_start', 'hobbs_end', 'booking__departed_at')
        )
        for i in range(1, len(fcs)):
            prev_end = _D(str(fcs[i-1]['hobbs_end']))
            curr_start = _D(str(fcs[i]['hobbs_start']))
            gap = curr_start - prev_end
            if abs(gap) > _D('0.05'):
                meter_gaps.append(
                    f"{ac.registration}: gap of {gap:+.2f} hrs before FC #{fcs[i]['id']} "
                    f"({fcs[i]['booking__departed_at'].strftime('%d %b %y') if fcs[i]['booking__departed_at'] else '?'})"
                )
    if meter_gaps:
        _issue('operations', 'warn',
               f"{len(meter_gaps)} hobbs gap(s) detected between consecutive flights",
               '\n'.join(meter_gaps))
    else:
        _ok('operations', 'Meter readings')


    # ── 6. Active members with expired subscriptions ─────────────────────────
    today = date.today()
    lapsed_active = list(
        ClubMember.objects.filter(
            club=club, standing='active',
            subscription_expires__isnull=False,
            subscription_expires__lt=today,
        ).select_related('user').order_by('subscription_expires')
    )
    if lapsed_active:
        detail = '\n'.join(
            f"{m.user.get_full_name()} — expired {m.subscription_expires}"
            for m in lapsed_active
        )
        _issue('members', 'warn',
               f"{len(lapsed_active)} active member(s) have expired subscriptions", detail)
    else:
        _ok('members', 'Subscription standing')

    # ── 7. Orphaned invoices (pending but booking completed/cancelled) ───────
    from .models import Invoice
    orphaned_inv = list(
        Invoice.objects.filter(
            club=club, status__in=('pending', 'sent'),
            flight_completion__booking__status__in=('completed', 'cancelled'),
        ).select_related('flight_completion__booking__member__user')
        .order_by('created_at')
    )
    if orphaned_inv:
        detail = '\n'.join(
            f"Invoice #{inv.id} — "
            f"{inv.flight_completion.booking.member.user.get_full_name() if inv.flight_completion and inv.flight_completion.booking and inv.flight_completion.booking.member else '?'} "
            f"(booking {inv.flight_completion.booking.status if inv.flight_completion and inv.flight_completion.booking else '?'})"
            for inv in orphaned_inv
        )
        _issue('financial', 'warn',
               f"{len(orphaned_inv)} invoice(s) are open but booking is completed/cancelled", detail)
    else:
        _ok('financial', 'Invoices')

    total_issues = sum(len(g['issues']) for g in groups.values())
    return render(request, 'core/health_check.html', {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'groups': groups,
        'total_issues': total_issues,
    })


@login_required
def data_page(request, club_slug):
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not actor.is_admin:
        return render(request, 'core/no_access.html', {'club': club}, status=403)
    _ico = 'width="20" height="20" viewBox="0 0 15 15" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
    export_types = [
        ('members',     f'<svg {_ico}><circle cx="7.5" cy="5" r="3"/><path d="M1.5 14c0-3.3 2.7-6 6-6s6 2.7 6 6"/></svg>',
                        'Members',       'Name, email, role, credentials, account balance'),
        ('flights',     f'<svg {_ico}><path d="M13.5 1.5 1.5 6.5l4.5 2 1.5 4.5 2.5-2.5z"/><line x1="6" y1="8.5" x2="13.5" y2="1.5"/></svg>',
                        'Flight history','All completed flights — dates, aircraft, pilot, instructor, hours, charges'),
        ('aircraft',    f'<svg {_ico}><path d="M7.5 1.5 6 5.5 1 9l1 1 5-2 -.5 4-2 1 .5 1 3-1.5 3 1.5.5-1-2-1L9.5 9l5 2 1-1-5-3.5z"/></svg>',
                        'Aircraft',      'Fleet list, maintenance items and log'),
        ('financial',   f'<svg {_ico}><rect x="1.5" y="3.5" width="12" height="8.5" rx="1.5"/><line x1="1.5" y1="7" x2="13.5" y2="7"/><line x1="4" y1="10.5" x2="6.5" y2="10.5"/></svg>',
                        'Financial',     'Account transactions, payments, outstanding balances'),
        ('maintenance', f'<svg {_ico}><path d="M13 2.5a3 3 0 0 0-4.2 4.2L4 11.5a1 1 0 1 0 1.4 1.4l4.8-4.8A3 3 0 0 0 13 2.5z"/><line x1="11" y1="4" x2="12.5" y2="5.5"/></svg>',
                        'Maintenance log','Per-aircraft maintenance hour log and scheduled items'),
    ]
    return render(request, 'core/data_page.html', {
        'club': club, 'club_member': actor, 'is_instructor': actor.is_instructor,
        'export_types': export_types,
    })


@login_required
def export_data(request, club_slug, export_type):
    import csv, io, zipfile
    from django.http import HttpResponse, StreamingHttpResponse
    from .models import (Voucher, Account, AccountTransaction,
                         AircraftMaintenanceItem, MaintenanceLogEntry, MemberCredential)

    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not actor.is_admin:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    ALLOWED = {'members', 'flights', 'aircraft', 'maintenance', 'financial', 'all'}
    if export_type not in ALLOWED:
        return HttpResponse('Unknown export type', status=400)

    def csv_bytes(rows, headers):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(headers)
        w.writerows(rows)
        return buf.getvalue().encode('utf-8-sig')  # BOM for Excel compatibility

    # ── Members ──────────────────────────────────────────────────────────────
    def members_csv():
        hdrs = ['last_name', 'first_name', 'email', 'role', 'standing',
                'caa_number', 'phone_mobile', 'phone_home',
                'account_balance', 'membership_expires']
        rows = []
        for m in (ClubMember.objects.filter(club=club)
                  .select_related('user', 'role')
                  .prefetch_related('account')
                  .order_by('user__last_name')):
            bal = ''
            try:
                bal = str(m.account.balance)
            except Exception:
                pass
            rows.append([
                m.user.last_name, m.user.first_name, m.user.email,
                m.role.name if m.role else '', m.standing,
                m.caa_number, m.phone_mobile, m.phone_home,
                bal, '',
            ])
        return csv_bytes(rows, hdrs)

    # ── Flights ───────────────────────────────────────────────────────────────
    def flights_csv():
        hdrs = ['date', 'member', 'aircraft', 'flight_type', 'instructor',
                'scheduled_start', 'scheduled_end', 'actual_hours',
                'total_charge', 'amount_paid', 'balance_owing', 'outcome']
        rows = []
        qs = (FlightCompletion.objects.filter(booking__club=club)
              .select_related('booking__member__user', 'booking__aircraft',
                              'booking__flight_type', 'booking__instructor')
              .order_by('booking__scheduled_start'))
        for fc in qs:
            b = fc.booking
            instr = b.instructor.get_full_name() if b.instructor else ''
            rows.append([
                b.scheduled_start.date(),
                b.member.user.get_full_name(),
                b.aircraft.registration,
                str(b.flight_type),
                instr,
                b.scheduled_start.strftime('%H:%M'),
                b.scheduled_end.strftime('%H:%M'),
                fc.actual_flight_hours,
                fc.total_charge,
                fc.amount_paid,
                fc.balance_owing,
                fc.get_outcome_display(),
            ])
        return csv_bytes(rows, hdrs)

    # ── Aircraft ──────────────────────────────────────────────────────────────
    def aircraft_csv():
        hdrs = ['registration', 'type', 'serial', 'seats', 'engines',
                'status', 'is_leased', 'total_time_method',
                'maint_time_source', 'maint_time_fraction', 'maint_hours_initial',
                'hobbs_initial', 'tacho_initial', 'airswitch_initial']
        rows = []
        for ac in Aircraft.objects.filter(club=club).select_related('aircraft_type').order_by('registration'):
            rows.append([
                ac.registration,
                ac.aircraft_type.name if ac.aircraft_type else '',
                ac.serial_number,
                ac.seats, ac.engine_count,
                ac.get_status_display(),
                'Yes' if ac.is_leased else 'No',
                ac.get_total_time_method_display(),
                ac.maint_time_source, ac.maint_time_fraction,
                ac.maint_hours_initial or '',
                ac.hobbs_initial or '', ac.tacho_initial or '', ac.airswitch_initial or '',
            ])
        return csv_bytes(rows, hdrs)

    # ── Maintenance ───────────────────────────────────────────────────────────
    def maintenance_csv():
        # Items
        item_hdrs = ['aircraft', 'item_name', 'due_date', 'due_hours',
                     'interval_hours', 'interval_days', 'warn_hours', 'alert_hours',
                     'last_completed_date', 'last_completed_hours', 'urgency']
        item_rows = []
        for item in (AircraftMaintenanceItem.objects
                     .filter(aircraft__club=club)
                     .select_related('aircraft')
                     .order_by('aircraft__registration', 'name')):
            item_rows.append([
                item.aircraft.registration, item.name,
                item.due_date or '', item.due_hours or '',
                item.interval_hours or '', item.interval_days or '',
                item.warn_hours or '', item.alert_hours or '',
                item.last_completed_date or '', item.last_completed_hours or '',
                item.get_urgency_display(),
            ])
        # Log
        log_hdrs = ['aircraft', 'date', 'hobbs', 'tacho', 'airswitch',
                    'maint_hours_this_flight', 'maint_hours_cumulative', 'notes']
        log_rows = []
        for entry in (MaintenanceLogEntry.objects
                      .filter(aircraft__club=club)
                      .select_related('aircraft')
                      .order_by('aircraft__registration', 'date')):
            log_rows.append([
                entry.aircraft.registration, entry.date,
                entry.hobbs_reading or '', entry.tacho_reading or '',
                entry.airswitch_reading or '',
                entry.maint_hours_flight, entry.maint_hours_total,
                entry.notes,
            ])
        # Combine as two sections in one CSV
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['=== MAINTENANCE ITEMS ==='])
        w.writerow(item_hdrs)
        w.writerows(item_rows)
        w.writerow([])
        w.writerow(['=== MAINTENANCE LOG ==='])
        w.writerow(log_hdrs)
        w.writerows(log_rows)
        return buf.getvalue().encode('utf-8-sig')

    # ── Financial ─────────────────────────────────────────────────────────────
    def financial_csv():
        hdrs = ['date', 'member', 'type', 'direction', 'amount',
                'description', 'payment_method', 'reference']
        rows = []
        for tx in (AccountTransaction.objects
                   .filter(account__club_member__club=club)
                   .select_related('account__club_member__user')
                   .order_by('created_at')):
            rows.append([
                tx.created_at.date(),
                tx.account.club_member.user.get_full_name(),
                tx.get_transaction_type_display(),
                tx.direction,
                tx.amount,
                tx.description,
                tx.payment_method,
                tx.reference,
            ])
        return csv_bytes(rows, hdrs)

    # ── Excel helper: build a single .xlsx from named sheets ──────────────────
    def make_xlsx(sheets):
        """sheets = list of (sheet_name, headers, rows). Returns bytes."""
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        HDR_FILL = PatternFill('solid', fgColor='1E3A5F')
        HDR_FONT = Font(color='FFFFFF', bold=True, size=10)
        for sheet_name, headers, rows in sheets:
            ws = wb.create_sheet(sheet_name[:31])
            ws.append(headers)
            for cell in ws[1]:
                cell.font = HDR_FONT
                cell.fill = HDR_FILL
                cell.alignment = Alignment(horizontal='left')
            for row in rows:
                ws.append([str(v) if v is not None else '' for v in row])
            for col in ws.columns:
                max_len = max((len(str(c.value or '')) for c in col), default=8)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 3, 50)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.read()

    def members_data():
        hdrs = ['Last name', 'First name', 'Email', 'Role', 'Standing',
                'CAA number', 'Mobile', 'Home phone', 'Account balance']
        rows = []
        for m in (ClubMember.objects.filter(club=club)
                  .select_related('user', 'role').prefetch_related('account')
                  .order_by('user__last_name')):
            bal = ''
            try: bal = m.account.balance
            except Exception: pass
            rows.append([m.user.last_name, m.user.first_name, m.user.email,
                         m.role.name if m.role else '', m.standing,
                         m.caa_number, m.phone_mobile, m.phone_home, bal])
        return hdrs, rows

    def flights_data():
        hdrs = ['Date', 'Member', 'Aircraft', 'Flight type', 'Instructor',
                'Start', 'End', 'Actual hours', 'Total charge', 'Paid', 'Balance owing', 'Outcome']
        rows = []
        qs = (FlightCompletion.objects.filter(booking__club=club)
              .select_related('booking__member__user', 'booking__aircraft',
                              'booking__flight_type', 'booking__instructor')
              .order_by('booking__scheduled_start'))
        for fc in qs:
            b = fc.booking
            rows.append([b.scheduled_start.date(), b.member.user.get_full_name(),
                         b.aircraft.registration, str(b.flight_type),
                         b.instructor.get_full_name() if b.instructor else '',
                         b.scheduled_start.strftime('%H:%M'), b.scheduled_end.strftime('%H:%M'),
                         fc.actual_flight_hours, fc.total_charge, fc.amount_paid,
                         fc.balance_owing, fc.get_outcome_display()])
        return hdrs, rows

    def aircraft_data():
        hdrs = ['Registration', 'Type', 'Serial', 'Seats', 'Engines', 'Status',
                'Leased', 'Billing method', 'Maint source', 'Maint fraction', 'Maint hrs initial',
                'Hobbs initial', 'Tacho initial', 'Airswitch initial']
        rows = []
        for ac in Aircraft.objects.filter(club=club).select_related('aircraft_type').order_by('registration'):
            rows.append([ac.registration, ac.aircraft_type.name if ac.aircraft_type else '',
                         ac.serial_number, ac.seats, ac.engine_count, ac.get_status_display(),
                         'Yes' if ac.is_leased else 'No', ac.get_total_time_method_display(),
                         ac.maint_time_source, ac.maint_time_fraction, ac.maint_hours_initial or '',
                         ac.hobbs_initial or '', ac.tacho_initial or '', ac.airswitch_initial or ''])
        return hdrs, rows

    def maintenance_data():
        from .models import AircraftMaintenanceItem, MaintenanceLogEntry
        item_hdrs = ['Aircraft', 'Item', 'Due date', 'Due hours', 'Interval hrs',
                     'Interval days', 'Warn hrs', 'Alert hrs',
                     'Last done date', 'Last done hours', 'Urgency']
        item_rows = []
        for item in (AircraftMaintenanceItem.objects.filter(aircraft__club=club)
                     .select_related('aircraft').order_by('aircraft__registration', 'name')):
            item_rows.append([item.aircraft.registration, item.name,
                               item.due_date or '', item.due_hours or '',
                               item.interval_hours or '', item.interval_days or '',
                               item.warn_hours or '', item.alert_hours or '',
                               item.last_completed_date or '', item.last_completed_hours or '',
                               item.get_urgency_display()])
        log_hdrs = ['Aircraft', 'Date', 'Hobbs', 'Tacho', 'Airswitch',
                    'Maint hrs this flight', 'Maint hrs cumulative', 'Notes']
        log_rows = []
        for e in (MaintenanceLogEntry.objects.filter(aircraft__club=club)
                  .select_related('aircraft').order_by('aircraft__registration', 'date')):
            log_rows.append([e.aircraft.registration, e.date,
                              e.hobbs_reading or '', e.tacho_reading or '', e.airswitch_reading or '',
                              e.maint_hours_flight, e.maint_hours_total, e.notes])
        return (item_hdrs, item_rows), (log_hdrs, log_rows)

    def financial_data():
        from .models import AccountTransaction
        hdrs = ['Date', 'Member', 'Type', 'Direction', 'Amount',
                'Description', 'Payment method', 'Reference']
        rows = []
        for tx in (AccountTransaction.objects.filter(account__club_member__club=club)
                   .select_related('account__club_member__user').order_by('created_at')):
            rows.append([tx.created_at.date(), tx.account.club_member.user.get_full_name(),
                         tx.get_transaction_type_display(), tx.direction, tx.amount,
                         tx.description, tx.payment_method, tx.reference])
        return hdrs, rows

    # ── Dispatch ──────────────────────────────────────────────────────────────
    slug = club.slug
    ts = date.today().strftime('%Y%m%d')
    fmt = request.GET.get('fmt', 'csv')  # ?fmt=xlsx or default csv

    if export_type == 'all':
        if fmt == 'xlsx':
            m_hdrs, m_rows = members_data()
            f_hdrs, f_rows = flights_data()
            a_hdrs, a_rows = aircraft_data()
            (mi_hdrs, mi_rows), (ml_hdrs, ml_rows) = maintenance_data()
            fi_hdrs, fi_rows = financial_data()
            xlsx = make_xlsx([
                ('Members',           m_hdrs, m_rows),
                ('Flights',           f_hdrs, f_rows),
                ('Aircraft',          a_hdrs, a_rows),
                ('Maintenance Items', mi_hdrs, mi_rows),
                ('Maintenance Log',   ml_hdrs, ml_rows),
                ('Financial',         fi_hdrs, fi_rows),
            ])
            resp = HttpResponse(xlsx,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            resp['Content-Disposition'] = f'attachment; filename="{slug}_export_{ts}.xlsx"'
            return resp
        # CSV ZIP
        buf = io.BytesIO()
        def _csv(hdrs, rows):
            b = io.StringIO()
            w = csv.writer(b)
            w.writerow(hdrs)
            w.writerows(rows)
            return b.getvalue().encode('utf-8-sig')
        (mi_hdrs, mi_rows), (ml_hdrs, ml_rows) = maintenance_data()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f'{slug}_members_{ts}.csv',      _csv(*members_data()))
            zf.writestr(f'{slug}_flights_{ts}.csv',      _csv(*flights_data()))
            zf.writestr(f'{slug}_aircraft_{ts}.csv',     _csv(*aircraft_data()))
            zf.writestr(f'{slug}_maint_items_{ts}.csv',  _csv(mi_hdrs, mi_rows))
            zf.writestr(f'{slug}_maint_log_{ts}.csv',    _csv(ml_hdrs, ml_rows))
            zf.writestr(f'{slug}_financial_{ts}.csv',    _csv(*financial_data()))
        buf.seek(0)
        resp = HttpResponse(buf.read(), content_type='application/zip')
        resp['Content-Disposition'] = f'attachment; filename="{slug}_export_{ts}.zip"'
        return resp

    # Single-table export
    def _csv(hdrs, rows):
        b = io.StringIO()
        w = csv.writer(b)
        w.writerow(hdrs)
        w.writerows(rows)
        return b.getvalue().encode('utf-8-sig')

    data_map = {
        'members':     (members_data,     f'{slug}_members_{ts}'),
        'flights':     (flights_data,     f'{slug}_flights_{ts}'),
        'aircraft':    (aircraft_data,    f'{slug}_aircraft_{ts}'),
        'financial':   (financial_data,   f'{slug}_financial_{ts}'),
    }
    if export_type == 'maintenance':
        (mi_hdrs, mi_rows), (ml_hdrs, ml_rows) = maintenance_data()
        if fmt == 'xlsx':
            xlsx = make_xlsx([('Maintenance Items', mi_hdrs, mi_rows),
                              ('Maintenance Log', ml_hdrs, ml_rows)])
            resp = HttpResponse(xlsx,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            resp['Content-Disposition'] = f'attachment; filename="{slug}_maintenance_{ts}.xlsx"'
            return resp
        buf = io.StringIO()
        w2 = csv.writer(buf)
        w2.writerow(['=== MAINTENANCE ITEMS ===']); w2.writerow(mi_hdrs); w2.writerows(mi_rows)
        w2.writerow([]); w2.writerow(['=== MAINTENANCE LOG ===']); w2.writerow(ml_hdrs); w2.writerows(ml_rows)
        resp = HttpResponse(buf.getvalue().encode('utf-8-sig'), content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="{slug}_maintenance_{ts}.csv"'
        return resp

    fn, base = data_map[export_type]
    hdrs, rows = fn()
    if fmt == 'xlsx':
        sheet_name = export_type.capitalize()
        xlsx = make_xlsx([(sheet_name, hdrs, rows)])
        resp = HttpResponse(xlsx,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="{base}.xlsx"'
        return resp
    resp = HttpResponse(_csv(hdrs, rows), content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{base}.csv"'
    return resp


@login_required
def export_import_template(request, club_slug):
    """
    Generate a blank Excel import template: one worksheet per object type,
    with headers, format notes, and a few greyed-out example rows so the
    user knows exactly what columns and values to provide.
    A reference sheet lists the club's current flight types and aircraft types.
    """
    import io, openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not actor.is_admin:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    from .models import FlightType, AircraftType

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # ── Styles ────────────────────────────────────────────────────────────────
    HDR_FILL  = PatternFill('solid', fgColor='1E3A5F')
    HDR_FONT  = Font(color='FFFFFF', bold=True, size=10)
    HDR_ALIGN = Alignment(horizontal='left', vertical='center', wrap_text=True)

    NOTE_FILL = PatternFill('solid', fgColor='FFF3CD')   # amber — required field note
    EX_FILL   = PatternFill('solid', fgColor='F2F4F7')   # grey  — example rows
    EX_FONT   = Font(italic=True, color='9AA3AD', size=9)
    EX_ALIGN  = Alignment(horizontal='left')

    REF_FILL  = PatternFill('solid', fgColor='E8F0FB')   # blue-tint — reference rows
    REF_FONT  = Font(color='2B4DA0', size=9)

    THIN      = Side(style='thin', color='D6DAE0')
    BORDER    = Border(bottom=Side(style='thin', color='E3E6EA'))

    def add_sheet(name, headers, notes, examples, col_widths=None):
        """
        headers: list of strings (use * suffix for required fields)
        notes:   list of per-column notes (same length as headers, or empty string)
        examples: list of lists (rows of example data, rendered greyed-out)
        col_widths: optional list of explicit widths; otherwise auto-sized
        """
        ws = wb.create_sheet(name[:31])

        # Header row
        ws.append(headers)
        ws.row_dimensions[1].height = 28
        for i, cell in enumerate(ws[1], 1):
            cell.font  = HDR_FONT
            cell.fill  = HDR_FILL
            cell.alignment = HDR_ALIGN
            if col_widths and i <= len(col_widths):
                ws.column_dimensions[get_column_letter(i)].width = col_widths[i - 1]

        # Notes row (per-column hints, shown in amber)
        if any(notes):
            ws.append(notes)
            ws.row_dimensions[2].height = 14
            for cell in ws[2]:
                cell.font  = Font(italic=True, color='7A5800', size=8)
                cell.fill  = NOTE_FILL
                cell.alignment = Alignment(horizontal='left', wrap_text=True)

        # Example rows
        for ex in examples:
            ws.append(ex)
            row_idx = ws.max_row
            ws.row_dimensions[row_idx].height = 13
            for cell in ws[row_idx]:
                cell.font  = EX_FONT
                cell.fill  = EX_FILL
                cell.alignment = EX_ALIGN

        # Auto-width if not explicitly specified
        if not col_widths:
            for col in ws.columns:
                max_len = max((len(str(c.value or '')) for c in col), default=8)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 45)

        ws.freeze_panes = 'A2'   # freeze header row
        return ws

    # ── Sheet 1: Instructions ─────────────────────────────────────────────────
    ws_info = wb.create_sheet('Instructions', 0)
    instructions = [
        ('ClubHanger Import Template', True),
        (f'Generated for: {club.name}', False),
        ('', False),
        ('HOW TO USE THIS FILE', True),
        ('1. Fill in the coloured sheets (Members, Aircraft, Flights).', False),
        ('2. Delete the grey example rows before importing — they are just to show the format.', False),
        ('3. Columns marked * are required. Leave optional columns blank if not known.', False),
        ('4. Dates must be in YYYY-MM-DD format (e.g. 2024-06-15).', False),
        ('5. Emails must exactly match existing members when cross-referencing.', False),
        ('6. Aircraft registrations must exactly match aircraft already added to the system.', False),
        ('7. Flight types and aircraft types must exactly match what is configured in Settings.', False),
        ('', False),
        ('SHEET SUMMARY', True),
        ('Members  — one row per club member. Fill this in first.', False),
        ('Aircraft — one row per aircraft in the fleet.', False),
        ('Flights  — one row per completed historical flight.', False),
        ('Ref: Flight Types  — read-only reference, do not edit.', False),
        ('Ref: Aircraft Types — read-only reference, do not edit.', False),
    ]
    TITLE_FONT = Font(bold=True, size=11, color='1E3A5F')
    BODY_FONT  = Font(size=10, color='2B333D')
    ws_info.column_dimensions['A'].width = 72
    for i, (text, is_title) in enumerate(instructions, 1):
        cell = ws_info.cell(row=i, column=1, value=text)
        cell.font  = TITLE_FONT if is_title else BODY_FONT
        cell.alignment = Alignment(horizontal='left', wrap_text=True)
        ws_info.row_dimensions[i].height = 18 if is_title else 14
    ws_info.sheet_view.showGridLines = False

    # ── Sheet 2: Members ──────────────────────────────────────────────────────
    add_sheet(
        name='Members',
        headers=['Last name *', 'First name *', 'Email *',
                 'Role', 'CAA number', 'Mobile', 'Home phone',
                 'Date of birth', 'Medical expiry', 'BFR expiry',
                 'Opening balance ($)'],
        notes=['e.g. Smith', 'e.g. John', 'e.g. john@example.com',
               'Member / Instructor / Admin', 'e.g. 123456',
               'e.g. +64 21 123 4567', 'e.g. +64 9 555 1234',
               'YYYY-MM-DD', 'YYYY-MM-DD', 'YYYY-MM-DD',
               '0.00 for no balance'],
        examples=[
            ['Smith',   'John',   'john.smith@example.com',   'Member',     '123456', '+64 21 123 4567', '',              '1985-03-15', '2026-06-30', '2025-12-01', '0.00'],
            ['Nguyen',  'Sarah',  'sarah.nguyen@example.com', 'Instructor', '654321', '+64 27 987 6543', '+64 9 555 0000', '1978-07-22', '2027-03-15', '2026-04-01', '-45.00'],
        ],
        col_widths=[14, 14, 28, 12, 13, 18, 18, 14, 14, 14, 14],
    )

    # ── Sheet 3: Aircraft ─────────────────────────────────────────────────────
    ac_type_names = ', '.join(
        AircraftType.objects.filter(club=club).values_list('name', flat=True)
    ) or 'e.g. Cessna 172'
    add_sheet(
        name='Aircraft',
        headers=['Registration *', 'Aircraft type *', 'Seats', 'Engines',
                 'Serial number', 'Hobbs initial', 'Tacho initial',
                 'Air switch initial', 'Fuel consumption (L/hr)', 'Leased'],
        notes=['e.g. ZK-ABC', f'One of: {ac_type_names}', '1–20', '1–4',
               'Manufacturer serial', 'Hours at system entry', 'Hours at system entry',
               'Hours at system entry', 'e.g. 28.0', 'Yes / No'],
        examples=[
            ['ZK-ABC', 'Cessna 172', '4', '1', '17267890', '2340.5', '1987.2', '',       '28.0', 'No'],
            ['ZK-DEF', 'Piper PA-28', '4', '1', 'PA28-4512', '1250.0', '',      '980.0', '26.0', 'No'],
        ],
        col_widths=[15, 20, 8, 9, 16, 14, 14, 16, 20, 9],
    )

    # ── Sheet 4: Flights ──────────────────────────────────────────────────────
    ft_names = ', '.join(
        FlightType.objects.filter(club=club).values_list('name', flat=True)
    ) or 'e.g. Training, Solo'
    add_sheet(
        name='Flights',
        headers=['Date *', 'Member email *', 'Aircraft registration *', 'Flight type *',
                 'Instructor email', 'Hobbs start', 'Hobbs end',
                 'Tacho start', 'Tacho end', 'Air switch start', 'Air switch end',
                 'Charge ($)', 'Notes'],
        notes=['YYYY-MM-DD', 'Must match a member', 'Must match aircraft in system',
               f'One of: {ft_names}',
               'Leave blank for solo', 'Leave blank if not used', 'Leave blank if not used',
               'Leave blank if not used', 'Leave blank if not used',
               'Leave blank if not used', 'Leave blank if not used',
               '0.00 or leave blank', 'Outcome notes'],
        examples=[
            ['2024-01-15', 'john.smith@example.com',   'ZK-ABC', 'Training', 'sarah.nguyen@example.com', '2340.5', '2342.3', '', '', '', '', '125.00', 'First solo circuit'],
            ['2024-01-16', 'sarah.nguyen@example.com', 'ZK-DEF', 'Solo',     '',                         '1250.0', '1251.5', '', '', '', '', '78.00',  ''],
        ],
        col_widths=[12, 28, 22, 16, 28, 12, 12, 12, 12, 14, 14, 10, 28],
    )

    # ── Sheet 5: Ref — Flight Types ───────────────────────────────────────────
    ws_ft = wb.create_sheet('Ref: Flight Types')
    ws_ft.append(['Flight type name', 'Billable', 'Requires declaration'])
    for cell in ws_ft[1]:
        cell.font = HDR_FONT; cell.fill = HDR_FILL; cell.alignment = HDR_ALIGN
    for ft in FlightType.objects.filter(club=club).order_by('name'):
        row = ws_ft.max_row + 1
        ws_ft.append([ft.name,
                       'Yes' if ft.is_billable else 'No',
                       'Yes' if getattr(ft, 'requires_declaration', False) else 'No'])
        for cell in ws_ft[row]:
            cell.font = REF_FONT; cell.fill = REF_FILL
    for col in ws_ft.columns:
        ws_ft.column_dimensions[col[0].column_letter].width = max(
            (len(str(c.value or '')) for c in col), default=8) + 4

    # ── Sheet 6: Ref — Aircraft Types ─────────────────────────────────────────
    ws_at = wb.create_sheet('Ref: Aircraft Types')
    ws_at.append(['Aircraft type name', 'ICAO designator'])
    for cell in ws_at[1]:
        cell.font = HDR_FONT; cell.fill = HDR_FILL; cell.alignment = HDR_ALIGN
    for at in AircraftType.objects.filter(club=club).order_by('name'):
        row = ws_at.max_row + 1
        ws_at.append([at.name, getattr(at, 'icao_designator', '') or ''])
        for cell in ws_at[row]:
            cell.font = REF_FONT; cell.fill = REF_FILL
    for col in ws_at.columns:
        ws_at.column_dimensions[col[0].column_letter].width = max(
            (len(str(c.value or '')) for c in col), default=8) + 4

    # ── Respond ───────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    slug = club.slug
    resp = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = f'attachment; filename="{slug}_import_template.xlsx"'
    return resp


@login_required
def adsb_proxy(request, club_slug, aircraft_id):
    """
    Live position proxy. Sources tried in order:
      1. OpenSky Network — free, no auth, NZ bounding-box query (callsign match)
      2. adsb.fi         — free, no auth, /v2/registration/[reg] (ADSBExchange v2)
      3. ADSB.one        — free, no auth, /v2/reg/[reg] (ADSBExchange v2)
    All sources fall through on any error (429, timeout, network failure).
    Responses cached server-side 45s. 'tried' list returned for client diagnostics.
    """
    import urllib.request, urllib.error, ssl as _ssl, json as _json
    from django.core.cache import cache
    from django.http import JsonResponse

    club = get_object_or_404(Club, slug=club_slug)
    try:
        ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return JsonResponse({'error': 'Access denied'}, status=403)
    ac = get_object_or_404(Aircraft, club=club, id=aircraft_id)
    # Mode S callsign format: strip dash, uppercase  (ZK-ABC → ZKABC)
    callsign = ac.registration.replace('-', '').upper()
    # Official registration kept as-is for database-lookup APIs
    reg = ac.registration.upper()

    cache_key = f'adsb_{club_slug}_{aircraft_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    ctx = _ssl.create_default_context()
    try:
        import certifi as _certifi
        ctx = _ssl.create_default_context(cafile=_certifi.where())
    except ImportError:
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE

    def _get(url):
        req = urllib.request.Request(url, headers={'User-Agent': 'ClubHanger/1.0'})
        with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
            return _json.loads(r.read())

    def _parse_v2(ac_list, source_name):
        """Parse an ADSBExchange v2 'ac' array into our response dict."""
        for a in ac_list or []:
            alt = a.get('alt_baro')
            if alt == 'ground' or not alt:
                continue
            lat, lon = a.get('lat'), a.get('lon')
            if lat is None or lon is None:
                continue
            return {
                'found': True,
                'lat': lat, 'lon': lon,
                'alt_ft': round(float(alt)),
                'speed_kt': round(float(a['gs'])) if a.get('gs') else None,
                'track': round(float(a['track'])) if a.get('track') else None,
                'squawk': a.get('squawk'),
                'seen': round(a.get('seen', 0)),
                'registration': ac.registration,
                'source': source_name,
            }
        return None

    tried = []
    result = None

    # ── Source 1: OpenSky Network ──────────────────────────────────────────────
    # Bounding box covers NZ + Aus east coast. Callsign match (no dash).
    # State vector: [icao24, callsign, origin, time_pos, last_contact,
    #                lon, lat, baro_alt_m, on_ground, velocity_ms, track, ...]
    try:
        data = _get('https://opensky-network.org/api/states/all'
                    '?lamin=-50&lomin=160&lamax=-30&lomax=180')
        tried.append('OpenSky')
        for s in (data.get('states') or []):
            if (s[1] or '').strip().upper() == callsign and not s[8]:
                result = {
                    'found': True,
                    'lat': s[6], 'lon': s[5],
                    'alt_ft': round(float(s[7]) * 3.28084) if s[7] else None,
                    'speed_kt': round(float(s[9]) * 1.94384) if s[9] else None,
                    'track': round(s[10]) if s[10] else None,
                    'squawk': s[14] if len(s) > 14 else None,
                    'seen': 0,
                    'registration': ac.registration,
                    'source': 'OpenSky',
                }
                break
    except Exception:
        tried.append('OpenSky (failed)')

    # ── Source 2: adsb.fi (/v2/registration/[reg], ADSBExchange v2) ────────────
    if result is None:
        try:
            data = _get(f'https://opendata.adsb.fi/api/v2/registration/{reg}')
            tried.append('adsb.fi')
            result = _parse_v2(data.get('ac'), 'adsb.fi')
        except Exception:
            tried.append('adsb.fi (failed)')

    # ── Source 3: ADSB.one (/v2/reg/[callsign], ADSBExchange v2) ──────────────
    if result is None:
        try:
            data = _get(f'https://api.adsb.one/v2/reg/{callsign}')
            tried.append('ADSB.one')
            result = _parse_v2(data.get('ac'), 'ADSB.one')
        except Exception:
            tried.append('ADSB.one (failed)')

    if result is None:
        result = {
            'found': False,
            'registration': ac.registration,
            'note': f'{ac.registration} not detected in any ADS-B source',
        }
    result['tried'] = tried
    cache.set(cache_key, result, 45)
    return JsonResponse(result)


# ─────────────────────────────────────────────────────────────────────────────
# OCCURRENCE / SAFETY REPORTING
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def occurrence_submit(request, club_slug):
    """Any member can submit an occurrence report."""
    from .models import OccurrenceType, OccurrenceReport, Aircraft as _Aircraft
    from datetime import date as _date
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')

    error = success = ''
    occ_types = OccurrenceType.objects.filter(club=club, is_active=True)
    aircraft_list = _Aircraft.objects.filter(club=club, status='online').order_by('registration')

    # Recent bookings for this member (last 30 days) for optional link
    from datetime import timedelta
    recent_bookings = (Booking.objects
                       .filter(club=club, member=actor,
                               scheduled_start__date__gte=_date.today() - timedelta(days=30))
                       .exclude(status='cancelled')
                       .select_related('aircraft', 'flight_type')
                       .order_by('-scheduled_start')[:10])

    if request.method == 'POST':
        from decimal import InvalidOperation
        ot_id = request.POST.get('occurrence_type')
        date_str = request.POST.get('date_of_occurrence', '').strip()
        description = request.POST.get('description', '').strip()
        if not ot_id or not date_str or not description:
            error = 'Occurrence type, date, and description are required.'
        else:
            try:
                ot = OccurrenceType.objects.get(id=ot_id, club=club)
                from datetime import date as _d
                occ_date = _d.fromisoformat(date_str)
                time_str = request.POST.get('time_of_occurrence', '').strip()
                occ_time = None
                if time_str:
                    from datetime import time as _t
                    h, m = time_str.split(':')
                    occ_time = _t(int(h), int(m))
                ac_id = request.POST.get('aircraft')
                bk_id = request.POST.get('related_booking')
                ac = _Aircraft.objects.filter(id=ac_id, club=club).first() if ac_id else None
                bk = Booking.objects.filter(id=bk_id, club=club, member=actor).first() if bk_id else None
                report = OccurrenceReport.objects.create(
                    club=club,
                    occurrence_type=ot,
                    reported_by=actor,
                    status=OccurrenceReport.STATUS_SUBMITTED,
                    date_of_occurrence=occ_date,
                    time_of_occurrence=occ_time,
                    location=request.POST.get('location', '').strip(),
                    aircraft=ac,
                    related_booking=bk,
                    description=description,
                    immediate_action=request.POST.get('immediate_action', '').strip(),
                    is_safety_risk=request.POST.get('is_safety_risk') == 'on',
                )
                OccurrenceAuditEntry.objects.create(report=report, actor=actor, verb='Submitted')
                from .email_notifications import occurrence_submitted as _email_occ
                _email_occ(report)
                return redirect(f"{request.path}?saved=1")
            except Exception as e:
                error = f'Error saving report: {e}'

    is_inline = request.GET.get('inline') == '1'
    base_template = 'core/base_inline.html' if is_inline else 'core/base.html'
    return render(request, 'core/occurrence_submit.html', {
        'club': club, 'club_member': actor,
        'occ_types': occ_types,
        'aircraft_list': aircraft_list,
        'recent_bookings': recent_bookings,
        'error': error,
        'today': _date.today().isoformat(),
        'base_template': base_template,
        'is_inline': is_inline,
        'back': request.GET.get('back', ''),
    })


@login_required
def occurrence_list(request, club_slug):
    """Admin/instructor view — all reports with filter and review."""
    from .models import OccurrenceType, OccurrenceReport
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not actor.can_access_manage:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    f_status      = request.GET.get('status', '')
    f_type        = request.GET.get('type', '')
    f_safety_risk = request.GET.get('safety_risk', '')

    qs = (OccurrenceReport.objects
          .filter(club=club)
          .select_related('occurrence_type', 'reported_by__user', 'aircraft', 'reviewed_by')
          .order_by('-reported_at'))
    if f_status:
        qs = qs.filter(status=f_status)
    if f_type:
        qs = qs.filter(occurrence_type_id=f_type)
    if f_safety_risk == '1':
        qs = qs.filter(is_safety_risk=True)

    occ_types = OccurrenceType.objects.filter(club=club, is_active=True)
    return render(request, 'core/occurrence_list.html', {
        'club': club, 'club_member': actor,
        'reports': qs,
        'occ_types': occ_types,
        'f_status': f_status, 'f_type': f_type, 'f_safety_risk': f_safety_risk,
        'status_choices': OccurrenceReport.STATUS_CHOICES,
        'open_count': OccurrenceReport.objects.filter(club=club, status=OccurrenceReport.STATUS_SUBMITTED).count(),
    })


@login_required
def occurrence_detail(request, club_slug, report_id):
    """View + review a single occurrence report."""
    from .models import OccurrenceReport
    from django.utils import timezone as _tz
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')

    report = get_object_or_404(OccurrenceReport, club=club, id=report_id)

    # Members can only view their own; manage-access users see all
    if report.reported_by != actor and not actor.can_access_manage:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    is_inline = request.GET.get('inline') == '1' or request.POST.get('inline') == '1'
    now = _tz.now()

    if request.method == 'POST' and actor.can_access_manage:
        act = request.POST.get('action')
        notes = request.POST.get('review_notes', '').strip()

        if act == 'save_notes':
            if notes != report.review_notes:
                report.review_notes = notes
                if not report.reviewed_by:
                    report.reviewed_by = request.user
                    report.reviewed_at = now
                report.save(update_fields=['review_notes', 'reviewed_by', 'reviewed_at'])
                OccurrenceAuditEntry.objects.create(report=report, actor=actor, verb='Notes updated', note=notes[:200])

        elif act == 'set_safety_risk':
            report.is_safety_risk = request.POST.get('is_safety_risk') == '1'
            report.save(update_fields=['is_safety_risk'])
            OccurrenceAuditEntry.objects.create(report=report, actor=actor,
                verb='Flagged as safety risk' if report.is_safety_risk else 'Safety risk flag removed')

        elif act == 'mark_reviewed':
            report.status = OccurrenceReport.STATUS_REVIEWED
            report.reviewed_by = request.user
            report.reviewed_at = now
            report.review_notes = notes
            report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes'])
            OccurrenceAuditEntry.objects.create(report=report, actor=actor, verb='Marked reviewed', note=notes[:200])

        elif act == 'close_no_action':
            report.status = OccurrenceReport.STATUS_CLOSED
            report.reviewed_by = request.user
            report.reviewed_at = now
            report.review_notes = notes
            report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes'])
            OccurrenceAuditEntry.objects.create(report=report, actor=actor, verb='Closed — no action required', note=notes[:200])

        elif act == 'close':
            if report.all_actions_resolved:
                report.status = OccurrenceReport.STATUS_CLOSED
                report.save(update_fields=['status'])
                OccurrenceAuditEntry.objects.create(report=report, actor=actor, verb='Closed')

        elif act == 'reopen':
            report.status = OccurrenceReport.STATUS_SUBMITTED
            report.save(update_fields=['status'])
            OccurrenceAuditEntry.objects.create(report=report, actor=actor, verb='Reopened')

        elif act == 'add_action':
            desc = request.POST.get('action_desc', '').strip()
            if desc:
                assigned_id = request.POST.get('assigned_to') or None
                assigned = ClubMember.objects.filter(club=club, id=assigned_id).first() if assigned_id else None
                due_raw = request.POST.get('due_date', '').strip()
                due = None
                if due_raw:
                    try:
                        from datetime import date as _d; due = _d.fromisoformat(due_raw)
                    except ValueError:
                        pass
                oa = OccurrenceAction.objects.create(
                    report=report, description=desc,
                    assigned_to=assigned, due_date=due, created_by=actor,
                )
                verb_note = f"Action added: {desc[:80]}"
                if assigned:
                    verb_note += f" — assigned to {assigned.user.get_full_name()}"
                OccurrenceAuditEntry.objects.create(report=report, actor=actor, verb='Action added', note=verb_note)
                # Notify assigned instructor
                if assigned:
                    from .models import Notification as _Notif
                    from django.urls import reverse as _rev
                    _Notif.objects.create(
                        club=club, recipient=assigned,
                        title='Safety action assigned to you',
                        message=f"Action: {desc[:120]}",
                        action_url=_rev('core:occurrence_detail', args=[club.slug, report.id]),
                    )

        elif act == 'complete_action':
            oa = OccurrenceAction.objects.filter(id=request.POST.get('action_id'), report=report).first()
            if oa and oa.status == OccurrenceAction.STATUS_OPEN:
                oa.status = OccurrenceAction.STATUS_COMPLETE
                oa.completed_by = request.user
                oa.completed_at = now
                oa.save()
                OccurrenceAuditEntry.objects.create(report=report, actor=actor,
                    verb='Action completed', note=oa.description[:80])

        elif act == 'override_action':
            oa = OccurrenceAction.objects.filter(id=request.POST.get('action_id'), report=report).first()
            if oa and oa.status == OccurrenceAction.STATUS_OPEN:
                oa.status = OccurrenceAction.STATUS_OVERRIDDEN
                oa.override_note = request.POST.get('override_note', '').strip()
                oa.save()
                OccurrenceAuditEntry.objects.create(report=report, actor=actor,
                    verb='Action overridden', note=oa.override_note[:200])

        return redirect(f"{request.path}?{'inline=1&' if is_inline else ''}saved=1")

    instructors = ClubMember.objects.filter(club=club, is_on_instructor_roster=True).select_related('user')
    base_template = 'core/base_inline.html' if is_inline else 'core/base.html'
    return render(request, 'core/occurrence_detail.html', {
        'club': club, 'club_member': actor,
        'report': report,
        'actions': report.actions.select_related('assigned_to__user', 'completed_by').all(),
        'audit': report.audit_entries.select_related('actor__user').all(),
        'instructors': instructors,
        'back': request.GET.get('back', ''),
        'base_template': base_template,
        'is_inline': is_inline,
    })


@login_required
def occurrence_export(request, club_slug):
    """CSV export of all occurrence reports for this club."""
    import csv as _csv
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not actor.can_access_manage:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    from django.http import StreamingHttpResponse

    qs = (OccurrenceReport.objects
          .filter(club=club)
          .select_related('occurrence_type', 'reported_by__user', 'aircraft', 'reviewed_by')
          .order_by('-date_of_occurrence'))

    def _rows():
        yield ['ID', 'Type', 'Date', 'Time', 'Location', 'Aircraft',
               'Reported by', 'Status', 'Reported at',
               'Description', 'Immediate action', 'Review notes']
        for r in qs:
            yield [
                r.id,
                r.occurrence_type.name,
                r.date_of_occurrence.isoformat(),
                r.time_of_occurrence.strftime('%H:%M') if r.time_of_occurrence else '',
                r.location,
                r.aircraft.registration if r.aircraft else '',
                r.reported_by.user.get_full_name(),
                r.get_status_display(),
                r.reported_at.strftime('%Y-%m-%d %H:%M'),
                r.description,
                r.immediate_action,
                r.review_notes,
            ]

    class EchoWriter:
        def write(self, value): return value

    writer = _csv.writer(EchoWriter())
    response = StreamingHttpResponse(
        (writer.writerow(row) for row in _rows()),
        content_type='text/csv',
    )
    response['Content-Disposition'] = f'attachment; filename="occurrences-{club.slug}.csv"'
    return response


@login_required
def occurrence_actions(request, club_slug):
    """Safety action items — open OccurrenceActions assigned to anyone (manage) or just this user."""
    from .models import OccurrenceAction as _OA
    from django.utils import timezone as _tz
    club = get_object_or_404(Club, slug=club_slug)
    try:
        actor = ClubMember.objects.get(user=request.user, club=club)
    except ClubMember.DoesNotExist:
        return redirect('login')
    if not actor.can_access_manage:
        return render(request, 'core/no_access.html', {'club': club}, status=403)

    if request.method == 'POST':
        act = request.POST.get('action')
        oa = _OA.objects.filter(id=request.POST.get('action_id'), report__club=club).first()
        if oa and oa.status == _OA.STATUS_OPEN:
            if act == 'complete_action':
                oa.status = _OA.STATUS_COMPLETE
                oa.completed_by = request.user
                oa.completed_at = _tz.now()
                oa.save()
                from .models import OccurrenceAuditEntry
                OccurrenceAuditEntry.objects.create(report=oa.report, actor=actor,
                    verb='Action completed', note=oa.description[:80])
            elif act == 'override_action':
                note = request.POST.get('override_note', '').strip()
                if note:
                    oa.status = _OA.STATUS_OVERRIDDEN
                    oa.override_note = note
                    oa.save()
                    from .models import OccurrenceAuditEntry
                    OccurrenceAuditEntry.objects.create(report=oa.report, actor=actor,
                        verb='Action overridden', note=note[:200])
        return redirect('core:occurrence_actions', club_slug=club_slug)

    f_assigned = request.GET.get('assigned', '')
    qs = (_OA.objects
          .filter(report__club=club, status=_OA.STATUS_OPEN)
          .select_related('report__occurrence_type', 'report__reported_by__user',
                          'assigned_to__user', 'report__aircraft')
          .order_by('due_date', 'created_at'))
    if f_assigned:
        qs = qs.filter(assigned_to_id=f_assigned)

    from datetime import date as _date
    instructors = ClubMember.objects.filter(club=club, is_on_instructor_roster=True).select_related('user')
    return render(request, 'core/occurrence_actions.html', {
        'club': club, 'club_member': actor,
        'actions': qs,
        'f_assigned': f_assigned,
        'instructors': instructors,
        'today': _date.today(),
    })
