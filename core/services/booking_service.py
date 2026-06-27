"""
Booking lifecycle service.

All state-changing operations on bookings live here. Views parse HTTP requests
and permission-check actors; they then call these functions and translate the
returned ServiceResult into HTTP responses.

Rules:
- No Django request/response objects. Accept model instances and plain values only.
- Every function returns a ServiceResult.
- All DB writes happen inside the function; callers should wrap in @transaction.atomic
  where needed.
- Audit every state change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from django.db import transaction as _transaction
from django.utils import timezone


# ─────────────────────────────────────────────────────────────────────────────
# Return type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ServiceResult:
    ok: bool
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def audit(booking, user, event_type: str, notes: str = '',
          field_name: str = '', old_value: str = '', new_value: str = '') -> None:
    """Write a booking audit log entry, swallowing errors so it never blocks an action."""
    try:
        from ..models import BookingAuditLog
        BookingAuditLog.objects.create(
            booking=booking, user=user, event_type=event_type,
            notes=notes, field_name=field_name,
            old_value=str(old_value), new_value=str(new_value),
        )
    except Exception as exc:
        print(f"audit log failed: {exc}")


def update_total(fc) -> None:
    """Recompute FlightCompletion.total_charge from its charge items."""
    from django.db.models import Sum
    total = fc.charge_items.aggregate(t=Sum('amount'))['t'] or 0
    fc.total_charge = total
    fc.save(update_fields=['total_charge'])


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle operations
# ─────────────────────────────────────────────────────────────────────────────

def confirm(booking, user) -> ServiceResult:
    """
    Confirm a pending booking.
    Caller must verify the actor has permission before calling.
    """
    from ..models import Booking as _Booking
    _Booking.objects.filter(pk=booking.pk).update(
        status='confirmed',
        confirmed_by=user,
        confirmed_at=timezone.now(),
    )
    booking.refresh_from_db()
    return ServiceResult(ok=True)


def depart(booking, user, no_declaration_reason: str = '') -> ServiceResult:
    """
    Transition a confirmed booking to departed.

    Validates declaration requirement and sets up the FlightCompletion shell
    with a fuel surcharge rate snapshot.
    """
    from ..models import FlightCompletion, FuelSurchargeRate

    if booking.status != 'confirmed':
        return ServiceResult(ok=False, error='Booking is not confirmed')

    # Subscription / standing gate — must be enforced here, not only at the view layer
    from datetime import date as _date
    _bm = booking.member
    if _bm.standing in ('suspended', 'lapsed', 'resigned'):
        return ServiceResult(
            ok=False,
            error=f'Member standing is {_bm.get_standing_display()} — departure blocked.',
        )
    if _bm.subscription_expires and _bm.subscription_expires < _date.today():
        return ServiceResult(
            ok=False,
            error=f'Membership subscription expired {_bm.subscription_expires.strftime("%-d %b %Y")} — departure blocked.',
        )

    from ..models import Booking as _Booking, Booking
    _active = _Booking.objects.filter(status='departed', club=booking.club).exclude(id=booking.id)
    _hint = ' Find it in Bookings → Active tab, then check it in or contact the club.'

    _ac_clash = _active.filter(aircraft=booking.aircraft).select_related('member__user').first()
    if _ac_clash:
        _who = _ac_clash.member.user.get_full_name()
        _when = _ac_clash.departed_at.strftime('%-d %b, %H:%M') if _ac_clash.departed_at else 'unknown time'
        return ServiceResult(
            ok=False,
            error=f'{booking.aircraft.registration} is already checked out — {_who} departed {_when}. Check in that flight first.{_hint}'
        )
    _mem_clash = _active.filter(member=booking.member).select_related('aircraft').first()
    if _mem_clash:
        _reg = _mem_clash.aircraft.registration if _mem_clash.aircraft else 'unknown aircraft'
        _when = _mem_clash.departed_at.strftime('%-d %b, %H:%M') if _mem_clash.departed_at else 'unknown time'
        return ServiceResult(
            ok=False,
            error=f'{booking.member.user.get_full_name()} is already checked out on {_reg} (departed {_when}). Check in that flight first.{_hint}'
        )
    if booking.instructor:
        _instr_clash = _active.filter(instructor=booking.instructor).select_related('aircraft', 'member__user').first()
        if _instr_clash:
            _reg = _instr_clash.aircraft.registration if _instr_clash.aircraft else 'unknown aircraft'
            _mbr = _instr_clash.member.user.get_full_name()
            _when = _instr_clash.departed_at.strftime('%-d %b, %H:%M') if _instr_clash.departed_at else 'unknown time'
            return ServiceResult(
                ok=False,
                error=f'{booking.instructor.get_full_name()} is already checked out with {_mbr} on {_reg} (departed {_when}). Check in that flight first.{_hint}'
            )

    requires_decl = booking.flight_type.requires_declaration
    has_decl = hasattr(booking, 'declaration') and not booking.declaration.is_draft
    if requires_decl and not has_decl and not no_declaration_reason:
        return ServiceResult(ok=False, error='Declaration required',
                             data={'needs_reason': True})

    fuel_rate = FuelSurchargeRate.current_rate(booking.club, booking.aircraft)
    _vals = {
        'status': 'departed',
        'departed_at': timezone.now(),
        'fuel_rate_snapshot': fuel_rate.rate if fuel_rate else None,
        'departed_aircraft_id': booking.aircraft_id,
    }
    if requires_decl and not has_decl:
        _vals['departed_without_declaration'] = True
        _vals['departed_without_declaration_reason'] = no_declaration_reason
    Booking.objects.filter(pk=booking.pk).update(**_vals)
    booking.refresh_from_db()
    audit(booking, user, 'departed')
    return ServiceResult(ok=True)


def check_in(
    booking,
    user,
    outcome: str,
    outcome_notes: str = '',
    hobbs_start=None,
    hobbs_end=None,
    tacho_start=None,
    tacho_end=None,
    airswitch_start=None,
    airswitch_end=None,
) -> ServiceResult:
    """
    Complete the check-in for a departed flight.

    Validates meter readings, records hours, auto-generates charge items,
    and transitions the booking to completed.
    Must be called inside a transaction (views use @transaction.atomic).
    """
    from ..models import FlightCompletion, FlightChargeItem, ChargeRate, ClubMember

    if booking.status != 'departed':
        return ServiceResult(ok=False, error='Booking has not departed')

    ac = booking.aircraft
    if ac.records_hobbs and (not hobbs_start or not hobbs_end):
        return ServiceResult(ok=False, error='Hobbs start and end are required for this aircraft')
    if ac.records_tacho and (not tacho_start or not tacho_end):
        return ServiceResult(ok=False, error='Tacho start and end are required for this aircraft')
    if ac.records_airswitch and (not airswitch_start or not airswitch_end):
        return ServiceResult(ok=False, error='Air switch start and end are required for this aircraft')

    try:
        if hobbs_start and hobbs_end and float(hobbs_end) <= float(hobbs_start):
            return ServiceResult(ok=False, error='Hobbs end must be greater than start')
        if tacho_start and tacho_end and float(tacho_end) <= float(tacho_start):
            return ServiceResult(ok=False, error='Tacho end must be greater than start')
        if airswitch_start and airswitch_end and float(airswitch_end) <= float(airswitch_start):
            return ServiceResult(ok=False, error='Air switch end must be greater than start')
    except ValueError:
        return ServiceResult(ok=False, error='Invalid meter reading values')

    club = booking.club
    fc, _ = FlightCompletion.objects.get_or_create(booking=booking, defaults={'logged_by': user})
    fc.outcome       = outcome
    fc.outcome_notes = outcome_notes
    fc.hobbs_start      = hobbs_start
    fc.hobbs_end        = hobbs_end
    fc.tacho_start      = tacho_start
    fc.tacho_end        = tacho_end
    fc.airswitch_start  = airswitch_start
    fc.airswitch_end    = airswitch_end
    fc.logged_by = user

    method = ac.total_time_method
    try:
        if method == 'hobbs' and hobbs_start and hobbs_end:
            fc.actual_flight_hours = round(float(hobbs_end) - float(hobbs_start), 2)
        elif method == 'tacho' and tacho_start and tacho_end:
            fc.actual_flight_hours = round(float(tacho_end) - float(tacho_start), 2)
        elif method == 'airswitch' and airswitch_start and airswitch_end:
            fc.actual_flight_hours = round(float(airswitch_end) - float(airswitch_start), 2)
    except (ValueError, TypeError):
        pass

    if booking.instructor:
        instr_member = ClubMember.objects.filter(user=booking.instructor, club=club).first()
        if instr_member and instr_member.instructor_grade:
            fc.instructor_rate_snapshot = instr_member.instructor_grade.hourly_rate

    fc.save()
    booking.status = 'completed'
    booking.arrived_at = timezone.now()
    booking.save(update_fields=['status', 'arrived_at'])

    hours = fc.actual_flight_hours
    if hours:
        hire_rate = ChargeRate.objects.filter(
            aircraft=ac, flight_type=booking.flight_type,
            time_method=ac.total_time_method
        ).first()
        if hire_rate:
            FlightChargeItem.objects.get_or_create(
                flight_completion=fc, item_type='hire',
                defaults={'description': f'Aircraft hire — {ac.registration}',
                          'amount': round(float(hire_rate.amount) * float(hours), 2)}
            )
        if fc.fuel_surcharge_rate_snapshot:
            FlightChargeItem.objects.get_or_create(
                flight_completion=fc, item_type='fuel',
                defaults={'description': 'Fuel levy',
                          'amount': round(float(fc.fuel_surcharge_rate_snapshot) * float(hours), 2)}
            )
        if fc.instructor_rate_snapshot and booking.instructor:
            FlightChargeItem.objects.get_or_create(
                flight_completion=fc, item_type='instructor',
                defaults={'description': f'Instructor fee — {booking.instructor.get_full_name()}',
                          'amount': round(float(fc.instructor_rate_snapshot) * float(hours), 2)}
            )
        for sc in ac.surcharges.all():
            FlightChargeItem.objects.get_or_create(
                flight_completion=fc, item_type='surcharge',
                defaults={'description': sc.name, 'amount': float(sc.amount)}
            )

    from ..models import create_maint_log_entry
    create_maint_log_entry(fc)
    for _mi in ac.maintenance_items.all():
        _mi.recalc_urgency()
        _mi.save(update_fields=['urgency'])

    update_total(fc)
    audit(booking, user, 'completed')
    return ServiceResult(ok=True, data={'charges_url': f'/manage/{club.slug}/bookings/{booking.id}/'})


def cancel(booking, user, release_slot: bool = False,
           reason: str = '', reason_other: str = '') -> ServiceResult:
    """
    Cancel a booking (also used for member self-cancellation / instructor rejection).
    Caller verifies actor permission.
    """
    if booking.status == 'departed':
        return ServiceResult(
            ok=False,
            error='Cannot cancel a flight that has departed. Use "Undo departure" to return it to confirmed, then cancel.'
        )
    from ..models import Booking as _Booking
    _cancel_vals = {
        'status': 'cancelled',
        'cancellation_reason': reason,
        'cancellation_reason_other': reason_other if reason == 'other' else '',
    }
    if release_slot:
        _cancel_vals['slot_released'] = True
        _cancel_vals['slot_released_at'] = timezone.now()
        _cancel_vals['slot_released_by'] = user
    _Booking.objects.filter(pk=booking.pk).update(**_cancel_vals)
    booking.refresh_from_db()
    from .notification_service import notify_booking_cancelled, notify_slot_released
    notify_booking_cancelled(booking)
    if release_slot:
        notify_slot_released(booking)
    return ServiceResult(ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Block-out check
# ─────────────────────────────────────────────────────────────────────────────

def check_blockout(club, aircraft, instructor, start_dt, end_dt, actor, override,
                   exclude_booking_id=None):
    """
    Scope-aware block-out conflict check for a prospective booking slot.

    Returns (blocked: bool, message: str, hits: list[BlockOut], is_soft: bool).

    Hard block-outs (BlockOutType.is_hard=True, or no type): members are blocked
    outright; staff can override with confirmation.
    Soft block-outs (BlockOutType.is_hard=False): everyone gets a warning and can
    confirm to proceed — no staff-only gate.
    """
    from ..models import BlockOut

    class _Probe:
        pass
    probe = _Probe()
    probe.club = club
    probe.aircraft_id = aircraft.id if aircraft else None
    probe.instructor_id = instructor.id if instructor else None
    probe.scheduled_start = start_dt
    probe.scheduled_end = end_dt

    hits = [
        bo for bo in BlockOut.objects.filter(club=club)
                                     .prefetch_related('aircraft', 'instructors', 'blockout_type')
        if bo.overlaps_booking(probe)
    ]
    if not hits:
        return (False, '', [], False)

    def _name(h):
        return h.blockout_type.name if h.blockout_type else (h.label or 'block-out')

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


# ─────────────────────────────────────────────────────────────────────────────
# Create
# ─────────────────────────────────────────────────────────────────────────────

def create(club, actor, aircraft, start_dt, end_dt, flight_type, instructor=None,
           booking_member=None, description='', override=False) -> ServiceResult:
    """
    Create a new booking after all conflict and block-out checks.

    actor       — the ClubMember performing the action (may differ from booking_member).
    booking_member — who the booking is FOR; defaults to actor.
    """
    from ..models import Booking, FlightType as _FT

    if booking_member is None:
        booking_member = actor

    # Past-booking guard — admins/instructors may backdate
    if start_dt < timezone.now() and not (actor.is_admin or actor.is_instructor):
        return ServiceResult(ok=False, error='Bookings cannot be made in the past')

    # Standing guard — suspended/lapsed/resigned members cannot self-book
    if (not (actor.is_admin or actor.is_instructor)
            and booking_member.standing not in ('active', 'non_member')):
        return ServiceResult(
            ok=False,
            error=f'Bookings are not available — membership status is {booking_member.get_standing_display()}.'
        )

    # Subscription expiry guard — block member self-booking; admins/instructors may override
    from datetime import date as _date
    if (not (actor.is_admin or actor.is_instructor)
            and booking_member.subscription_expires
            and booking_member.subscription_expires < _date.today()):
        return ServiceResult(
            ok=False,
            error=f'Membership subscription expired on {booking_member.subscription_expires.strftime("%d %b %Y")}. '
                  'Please renew before booking.'
        )

    # Aircraft conflict — completed bookings that returned early free the aircraft from arrived_at
    if Booking.objects.filter(
        club=club, aircraft=aircraft,
        scheduled_start__lt=end_dt, scheduled_end__gt=start_dt,
    ).exclude(status='cancelled').exclude(
        status='completed', arrived_at__isnull=False, arrived_at__lte=start_dt,
    ).exists():
        return ServiceResult(ok=False, error='Aircraft already booked at that time')

    # Instructor conflict — same early-return logic
    if instructor and Booking.objects.filter(
        club=club, instructor=instructor,
        scheduled_start__lt=end_dt, scheduled_end__gt=start_dt,
    ).exclude(status='cancelled').exclude(
        status='completed', arrived_at__isnull=False, arrived_at__lte=start_dt,
    ).exists():
        return ServiceResult(ok=False, error='Instructor already booked at that time')

    # Block-out check
    blocked, msg, hits, is_soft = check_blockout(
        club, aircraft, instructor, start_dt, end_dt, actor, override
    )
    if blocked:
        can_override = is_soft or bool(actor.is_admin or actor.is_instructor)
        return ServiceResult(ok=False, error=msg,
                             data={'blockout': True, 'can_override': can_override, 'soft': is_soft})

    # Solo flight types must not carry an instructor
    if flight_type and flight_type.is_solo:
        instructor = None

    booking = Booking.objects.create(
        club=club,
        member=booking_member,
        aircraft=aircraft,
        scheduled_start=start_dt,
        scheduled_end=end_dt,
        created_by=actor.user,
        instructor=instructor,
        status='pending',
        flight_type=flight_type,
        description=description,
        blockout_override=bool(hits and override),
    )
    audit(booking, actor.user, 'created', notes='Booking created')
    if hits and override:
        audit(booking, actor.user, 'warning_acknowledged',
              notes=f"Staff override of block-out: {msg}")
    from .notification_service import notify_instructor_new_booking
    notify_instructor_new_booking(booking)
    return ServiceResult(ok=True, data={'booking_id': booking.id})


# ─────────────────────────────────────────────────────────────────────────────
# Edit
# ─────────────────────────────────────────────────────────────────────────────

def edit(booking, actor, aircraft, start_dt, end_dt, flight_type=None,
         instructor=None, booking_member=None, description='', override=False) -> ServiceResult:
    """
    Edit an existing booking's time, aircraft, instructor, member, flight type, description.
    actor — the ClubMember performing the edit.
    """
    from ..models import Booking, recompute_blockout_conflict

    club = booking.club

    # Aircraft conflict (exclude self, ignore cancelled, early-returned completed)
    if Booking.objects.filter(
        club=club, aircraft=aircraft,
        scheduled_start__lt=end_dt, scheduled_end__gt=start_dt,
    ).exclude(id=booking.id).exclude(status='cancelled').exclude(
        status='completed', arrived_at__isnull=False, arrived_at__lte=start_dt,
    ).exists():
        return ServiceResult(ok=False, error='Aircraft already booked at that time')

    # Instructor conflict
    if instructor and Booking.objects.filter(
        club=club, instructor=instructor,
        scheduled_start__lt=end_dt, scheduled_end__gt=start_dt,
    ).exclude(id=booking.id).exclude(status='cancelled').exclude(
        status='completed', arrived_at__isnull=False, arrived_at__lte=start_dt,
    ).exists():
        return ServiceResult(ok=False, error='Instructor already booked at that time')

    # Block-out check
    blocked, msg, hits, is_soft = check_blockout(
        club, aircraft, instructor, start_dt, end_dt, actor, override,
        exclude_booking_id=booking.id
    )
    if blocked:
        can_override = is_soft or bool(actor.is_admin or actor.is_instructor)
        return ServiceResult(ok=False, error=msg,
                             data={'blockout': True, 'can_override': can_override, 'soft': is_soft})

    if booking_member:
        booking.member = booking_member
    if flight_type:
        booking.flight_type = flight_type
    # Solo flight types must not carry an instructor
    if booking.flight_type and booking.flight_type.is_solo:
        instructor = None

    Booking.objects.filter(pk=booking.pk).update(
        member=booking.member,
        flight_type=booking.flight_type,
        aircraft=aircraft,
        instructor=instructor,
        scheduled_start=start_dt,
        scheduled_end=end_dt,
        description=description,
        blockout_override=bool(hits and override),
    )
    booking.refresh_from_db()

    recompute_blockout_conflict(booking)
    audit(booking, actor.user, 'field_changed', notes='Booking edited')
    if hits and override:
        audit(booking, actor.user, 'warning_acknowledged',
              notes=f"Staff override of block-out on edit: {msg}")
    return ServiceResult(ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Reschedule
# ─────────────────────────────────────────────────────────────────────────────

def reschedule(booking, actor, new_start_dt, duration_minutes,
               aircraft=None, instructor=None, override=False) -> ServiceResult:
    """
    Move a booking to a new time (and optionally a new aircraft or instructor).
    actor — the ClubMember performing the reschedule.
    """
    from ..models import Booking, recompute_blockout_conflict
    from datetime import timedelta

    club = booking.club
    new_end_dt = new_start_dt + timedelta(minutes=duration_minutes)
    target_aircraft  = aircraft  or booking.aircraft
    target_instructor = instructor if instructor is not None else booking.instructor

    # Aircraft conflict (early-returned completed bookings free the aircraft from arrived_at)
    _ac_conflict = Booking.objects.filter(
        club=club, aircraft=target_aircraft,
        scheduled_start__lt=new_end_dt, scheduled_end__gt=new_start_dt,
    ).exclude(id=booking.id).exclude(status='cancelled').exclude(
        status='completed', arrived_at__isnull=False, arrived_at__lte=new_start_dt,
    ).first()
    if _ac_conflict:
        from django.utils import timezone as _tz
        _s = _tz.localtime(_ac_conflict.scheduled_start).strftime('%H:%M')
        _e = _tz.localtime(_ac_conflict.scheduled_end).strftime('%H:%M')
        _who = _ac_conflict.member.user.get_full_name() if _ac_conflict.member else '(no member)'
        return ServiceResult(ok=False, error=f'Aircraft not available — conflicts with {_who} {_s}–{_e} ({_ac_conflict.status})')

    # Instructor conflict (only if we're explicitly setting one)
    if instructor is not None and instructor and Booking.objects.filter(
        club=club, instructor=instructor,
        scheduled_start__lt=new_end_dt, scheduled_end__gt=new_start_dt,
    ).exclude(id=booking.id).exclude(status='cancelled').exclude(
        status='completed', arrived_at__isnull=False, arrived_at__lte=new_start_dt,
    ).exists():
        return ServiceResult(ok=False, error='Instructor not available at new time')

    # Block-out check
    blocked, msg, hits, is_soft = check_blockout(
        club, target_aircraft, target_instructor, new_start_dt, new_end_dt,
        actor, override, exclude_booking_id=booking.id
    )
    if blocked:
        can_override = is_soft or bool(actor.is_admin or actor.is_instructor)
        return ServiceResult(ok=False, error=msg,
                             data={'blockout': True, 'can_override': can_override, 'soft': is_soft})

    _reschedule_vals = {
        'scheduled_start': new_start_dt,
        'scheduled_end': new_end_dt,
        'blockout_override': bool(hits and override),
    }
    if aircraft:
        _reschedule_vals['aircraft'] = aircraft
    if instructor is not None:
        _reschedule_vals['instructor'] = target_instructor
    from ..models import Booking as _Booking
    _Booking.objects.filter(pk=booking.pk).update(**_reschedule_vals)
    booking.refresh_from_db()

    recompute_blockout_conflict(booking)
    audit(booking, actor.user, 'field_changed', notes='Rescheduled')
    if hits and override:
        audit(booking, actor.user, 'warning_acknowledged',
              notes=f"Staff override of block-out on reschedule: {msg}")
    return ServiceResult(ok=True)
