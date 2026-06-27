"""
Availability search engine for aero club booking system.
Finds free slots matching user criteria across date range.
"""

from datetime import datetime, timedelta, time
from django.db.models import Q
from django.utils import timezone
from .models import Booking, BookingStatus, Aircraft, AircraftStatus, ClubMember, Role


def _subtract_intervals(span_start, span_end, busy):
    """Given a span and a list of (start,end) busy intervals, return free sub-spans."""
    busy = sorted([b for b in busy if b[1] > span_start and b[0] < span_end])
    free = []
    cursor = span_start
    for b_start, b_end in busy:
        b_start = max(b_start, span_start)
        b_end = min(b_end, span_end)
        if b_start > cursor:
            free.append((cursor, b_start))
        cursor = max(cursor, b_end)
    if cursor < span_end:
        free.append((cursor, span_end))
    return free


def _blockout_intervals_for(club, day, aircraft, day_start, day_end):
    """All block-out (start,end) datetimes affecting this aircraft on this day."""
    from .models import BlockOut
    intervals = []
    for bo in BlockOut.objects.filter(club=club).prefetch_related('aircraft', 'instructors'):
        if not bo.applies_on(day):
            continue
        if not bo.affects_aircraft(aircraft):
            continue
        if bo.all_day:
            intervals.append((day_start, day_end))
        elif bo.start_time and bo.end_time:
            s = _aware(datetime.combine(day, bo.start_time))
            e = _aware(datetime.combine(day, bo.end_time))
            intervals.append((s, e))
    return intervals


def _aware(dt):
    if timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt


def _intersect(a, b):
    """Intersect two lists of (start,end) intervals."""
    out = []
    for a0, a1 in a:
        for b0, b1 in b:
            s = max(a0, b0)
            e = min(a1, b1)
            if e > s:
                out.append((s, e))
    return out


def _instructor_available_intervals(club_member, day, day_start, day_end):
    """
    Return the [(start, end)] intervals within [day_start, day_end] for which this
    instructor is declared on-roster.  If no availability records exist the instructor
    is assumed available for the full operating window.
    """
    from .models import InstructorAvailability
    windows = list(InstructorAvailability.objects.filter(club_member=club_member))
    if not windows:
        return [(day_start, day_end)]  # unconstrained

    intervals = []
    for w in windows:
        iv = w.interval_on(day, day_start, day_end)
        if iv and iv[1] > iv[0]:
            intervals.append(iv)

    if not intervals:
        return []  # has records but none apply today → not on roster
    return sorted(intervals)


def _instructor_busy(club, instr_user, day_start, day_end, day):
    """Busy intervals for an instructor: their bookings + block-outs affecting them."""
    from .models import BlockOut
    busy = []
    for b in Booking.objects.filter(
        club=club, instructor=instr_user,
        scheduled_start__lt=day_end, scheduled_end__gt=day_start,
    ).exclude(status=BookingStatus.CANCELLED):
        busy.append((b.scheduled_start, b.scheduled_end))
    for bo in BlockOut.objects.filter(club=club).prefetch_related('instructors'):
        if not bo.applies_on(day):
            continue
        if not bo.affects_instructor(instr_user):
            continue
        if bo.all_day or not (bo.start_time and bo.end_time):
            busy.append((day_start, day_end))
        else:
            busy.append((_aware(datetime.combine(day, bo.start_time)),
                         _aware(datetime.combine(day, bo.end_time))))
    return busy


def find_free_spans_with_instructors(club, date_start, date_end, aircraft=None,
                                     aircraft_type=None, instructor=None, min_minutes=None):
    """
    Dual-flight availability: spans where an aircraft AND an instructor are both free.

    Returns list of dicts, one per (day, aircraft):
      { 'date', 'aircraft', 'instructor_rows': [ {instructor, spans:[{start,end,minutes,...}]} ] }
    Aircraft with no instructor free in any span are omitted.
    """
    from .models import ClubConfig, ClubMember
    config, _ = ClubConfig.objects.get_or_create(club=club)
    op_start, op_end = config.operating_hours_start, config.operating_hours_end

    if aircraft:
        ac_list = [aircraft]
    elif aircraft_type:
        ac_list = list(Aircraft.objects.filter(club=club, aircraft_type__name=aircraft_type, status=AircraftStatus.ONLINE))
    else:
        ac_list = list(Aircraft.objects.filter(club=club, status=AircraftStatus.ONLINE))

    # Instructors to consider
    instr_members = ClubMember.objects.filter(club=club, role__name__iexact='instructor').select_related('user')
    if instructor:
        instr_members = instr_members.filter(user=instructor)
    instr_users = [m.user for m in instr_members]
    member_map = {m.user_id: m for m in instr_members}

    _now = timezone.now()
    _today = timezone.localdate()
    results = []
    day = date_start
    while day <= date_end:
        day_start = _aware(datetime.combine(day, op_start))
        day_end = _aware(datetime.combine(day, op_end))
        if day == _today:
            day_start = max(day_start, _now)
        if day_start >= day_end:
            day += timedelta(days=1)
            continue

        # Precompute each instructor's free intervals (availability window minus busy)
        instr_free = {}
        for u in instr_users:
            cm = member_map.get(u.id)
            avail = _instructor_available_intervals(cm, day, day_start, day_end) if cm else [(day_start, day_end)]
            if not avail:
                instr_free[u.id] = []  # not on roster today
                continue
            busy = _instructor_busy(club, u, day_start, day_end, day)
            free = []
            for av_s, av_e in avail:
                free.extend(_subtract_intervals(av_s, av_e, busy))
            instr_free[u.id] = free

        for ac in ac_list:
            ac_busy = []
            for b in Booking.objects.filter(
                aircraft=ac, scheduled_start__lt=day_end, scheduled_end__gt=day_start,
            ).exclude(status=BookingStatus.CANCELLED):
                ac_busy.append((b.scheduled_start, b.scheduled_end))
            ac_busy.extend(_blockout_intervals_for(club, day, ac, day_start, day_end))
            ac_free = _subtract_intervals(day_start, day_end, ac_busy)
            if not ac_free:
                continue

            instructor_rows = []
            for u in instr_users:
                inter = _intersect(ac_free, instr_free.get(u.id, []))
                spans = []
                for s, e in inter:
                    mins = int((e - s).total_seconds() // 60)
                    if min_minutes and mins < min_minutes:
                        continue
                    spans.append({'start': s, 'end': e, 'minutes': mins})
                if spans:
                    instructor_rows.append({'instructor': u, 'spans': spans})

            if instructor_rows:
                results.append({'date': day, 'aircraft': ac, 'instructor_rows': instructor_rows})

        day += timedelta(days=1)

    return results


def find_free_spans(club, date_start, date_end, aircraft=None, aircraft_type=None,
                    min_minutes=None):
    """
    Return continuous free spans per day per aircraft, subtracting bookings and
    block-outs. Operating hours come from ClubConfig.

    Returns list of dicts:
      { 'date': date, 'aircraft': Aircraft, 'spans': [ {start,end,minutes} ] }
    """
    from .models import ClubConfig
    config, _ = ClubConfig.objects.get_or_create(club=club)
    op_start = config.operating_hours_start
    op_end = config.operating_hours_end

    if aircraft:
        ac_list = [aircraft]
    elif aircraft_type:
        ac_list = list(Aircraft.objects.filter(club=club, aircraft_type__name=aircraft_type, status=AircraftStatus.ONLINE))
    else:
        ac_list = list(Aircraft.objects.filter(club=club, status=AircraftStatus.ONLINE))

    _now = timezone.now()
    _today = timezone.localdate()
    results = []
    day = date_start
    while day <= date_end:
        day_start = _aware(datetime.combine(day, op_start))
        day_end = _aware(datetime.combine(day, op_end))
        if day == _today:
            day_start = max(day_start, _now)
        if day_start >= day_end:
            day += timedelta(days=1)
            continue

        for ac in ac_list:
            busy = []
            # bookings
            for b in Booking.objects.filter(
                aircraft=ac, scheduled_start__lt=day_end, scheduled_end__gt=day_start,
            ).exclude(status=BookingStatus.CANCELLED):
                busy.append((b.scheduled_start, b.scheduled_end))
            # block-outs
            busy.extend(_blockout_intervals_for(club, day, ac, day_start, day_end))

            free = _subtract_intervals(day_start, day_end, busy)
            spans = []
            for s, e in free:
                mins = int((e - s).total_seconds() // 60)
                if min_minutes and mins < min_minutes:
                    continue
                spans.append({'start': s, 'end': e, 'minutes': mins})

            if spans:
                results.append({'date': day, 'aircraft': ac, 'spans': spans})

        day += timedelta(days=1)

    return results


def find_available_slots(club, date_start, date_end, aircraft=None, aircraft_type=None, 
                         instructor=None, duration_minutes=90, solo_only=False):
    """
    Find available slots matching criteria.
    
    Args:
        club: Club object
        date_start: datetime.date
        date_end: datetime.date
        aircraft: specific Aircraft object or None
        aircraft_type: aircraft type name string (e.g. 'PA38', 'C152') or None
        instructor: specific User (instructor) object or None
        duration_minutes: default 90
        solo_only: True to exclude instructor requirement
    
    Returns:
        List of dicts:
        {
            'datetime_start': datetime,
            'datetime_end': datetime,
            'aircraft': Aircraft,
            'instructor': User or None,
            'duration_minutes': int,
        }
    """
    
    results = []
    current_date = date_start
    
    while current_date <= date_end:
        # Get aircraft to search
        if aircraft:
            aircraft_list = [aircraft]
        elif aircraft_type:
            aircraft_list = Aircraft.objects.filter(
                club=club,
                aircraft_type__name=aircraft_type,
                status=AircraftStatus.ONLINE
            )
        else:
            aircraft_list = Aircraft.objects.filter(club=club, status=AircraftStatus.ONLINE)
        
        # For each aircraft, find free slots
        for ac in aircraft_list:
            # Get bookings for this aircraft on this day
            day_start = datetime.combine(current_date, time(7, 0))
            day_end = datetime.combine(current_date, time(21, 0))

            bookings = Booking.objects.filter(
                aircraft=ac,
                scheduled_start__gte=day_start,
                scheduled_start__lte=day_end + timedelta(days=1)
            )

            # For today, start from now (not the opening time)
            from datetime import date as _date
            current_time = max(day_start, datetime.now()) if current_date == _date.today() else day_start
            
            while current_time + timedelta(minutes=duration_minutes) <= day_end:
                slot_end = current_time + timedelta(minutes=duration_minutes)
                
                # Check if aircraft slot is free
                aircraft_conflict = bookings.filter(
                    scheduled_start__lt=slot_end,
                    scheduled_end__gt=current_time
                ).exists()
                
                if aircraft_conflict:
                    current_time += timedelta(minutes=30)
                    continue
                
                # If instructor specified, check their availability
                if instructor and not solo_only:
                    instructor_bookings = Booking.objects.filter(
                        Q(instructor=instructor) | Q(confirmed_by=instructor),
                        club=club,
                        scheduled_start__lt=slot_end,
                        scheduled_end__gt=current_time
                    )
                    
                    if instructor_bookings.exists():
                        current_time += timedelta(minutes=30)
                        continue
                
                # Slot is available!
                results.append({
                    'datetime_start': current_time,
                    'datetime_end': slot_end,
                    'aircraft': ac,
                    'instructor': instructor,
                    'duration_minutes': duration_minutes,
                })
                
                current_time += timedelta(minutes=30)
        
        current_date += timedelta(days=1)
    
    return results


def get_date_range(range_type):
    """
    Get start and end dates based on range type.

    Args:
        range_type: 'today', 'this_week', 'next_week', 'this_month', 'next_month'

    Returns:
        (date_start, date_end) tuple
    """
    from django.utils import timezone

    today = timezone.localdate()

    if range_type == 'today':
        return today, today

    elif range_type == 'this_week':
        # Monday to Sunday of this week
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return start, end
    
    elif range_type == 'next_week':
        # Monday to Sunday of next week
        start = today - timedelta(days=today.weekday()) + timedelta(days=7)
        end = start + timedelta(days=6)
        return start, end
    
    elif range_type == 'this_month':
        # First to last day of this month
        start = today.replace(day=1)
        if today.month == 12:
            end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        return start, end
    
    elif range_type == 'next_month':
        # First to last day of next calendar month
        if today.month == 12:
            start = today.replace(year=today.year + 1, month=1, day=1)
        else:
            start = today.replace(month=today.month + 1, day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = start.replace(month=start.month + 1, day=1) - timedelta(days=1)
        return start, end

    else:
        # Default: this week
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return start, end
