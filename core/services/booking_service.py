"""
Booking lifecycle service.

All state-changing operations on bookings live here. Views parse HTTP requests
and permission-check actors; they then call these functions and translate the
returned ServiceResult into HTTP responses.

Rules:
- No Django request/response objects. Accept model instances and plain values only.
- Every function returns a ServiceResult.
- All DB writes happen inside the function; callers should wrap in @transaction.atomic
  where needed (checkin_booking already is).
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
    booking.status = 'confirmed'
    booking.confirmed_by = user
    booking.confirmed_at = timezone.now()
    booking.save()
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

    requires_decl = booking.flight_type.requires_declaration
    has_decl = hasattr(booking, 'declaration') and not booking.declaration.is_draft
    if requires_decl and not has_decl and not no_declaration_reason:
        return ServiceResult(ok=False, error='Declaration required',
                             data={'needs_reason': True})

    booking.status = 'departed'
    booking.departed_at = timezone.now()
    if requires_decl and not has_decl:
        booking.departed_without_declaration = True
        booking.departed_without_declaration_reason = no_declaration_reason
    booking.save()

    club = booking.club
    fuel_rate = FuelSurchargeRate.current_rate(club, booking.aircraft)
    FlightCompletion.objects.get_or_create(
        booking=booking,
        defaults={
            'logged_by': user,
            'fuel_surcharge_rate_snapshot': fuel_rate.rate if fuel_rate else None,
        }
    )
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
    if booking.scheduled_end > timezone.now():
        return ServiceResult(
            ok=False,
            error='Cannot check in a flight that has not yet finished — '
                  'wait until the scheduled end time has passed',
        )

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
        elif method in ('tacho', 'tacho_less_5') and tacho_start and tacho_end:
            h = float(tacho_end) - float(tacho_start)
            fc.actual_flight_hours = round(h * 0.95, 2) if method == 'tacho_less_5' else round(h, 2)
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
    hire_rate = ChargeRate.objects.filter(
        aircraft=ac, flight_type=booking.flight_type,
        time_method=ac.total_time_method
    ).first()
    if hire_rate and hours:
        FlightChargeItem.objects.get_or_create(
            flight_completion=fc, item_type='hire',
            defaults={'description': f'Aircraft hire — {ac.registration}',
                      'amount': round(float(hire_rate.amount) * float(hours), 2)}
        )
    if fc.fuel_surcharge_rate_snapshot and hours:
        FlightChargeItem.objects.get_or_create(
            flight_completion=fc, item_type='fuel',
            defaults={'description': 'Fuel levy',
                      'amount': round(float(fc.fuel_surcharge_rate_snapshot) * float(hours), 2)}
        )
    if fc.instructor_rate_snapshot and hours and booking.instructor:
        FlightChargeItem.objects.get_or_create(
            flight_completion=fc, item_type='instructor',
            defaults={'description': f'Instructor fee — {booking.instructor.get_full_name()}',
                      'amount': round(float(fc.instructor_rate_snapshot) * float(hours), 2)}
        )
    for sc in ac.surcharges.all():
        FlightChargeItem.objects.get_or_create(
            flight_completion=fc, item_type='surcharge',
            defaults={'description': sc.name, 'amount': sc.amount}
        )

    update_total(fc)
    audit(booking, user, 'completed')
    return ServiceResult(ok=True, data={'charges_url': f'/manage/{club.slug}/bookings/{booking.id}/'})


def cancel(booking, user, release_slot: bool = False) -> ServiceResult:
    """
    Cancel a booking (also used for member self-cancellation / instructor rejection).
    Caller verifies actor permission.
    """
    booking.status = 'cancelled'
    if release_slot:
        booking.slot_released = True
        booking.slot_released_at = timezone.now()
        booking.slot_released_by = user
    booking.save()
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

    # Aircraft conflict
    if Booking.objects.filter(
        club=club, aircraft=aircraft,
        scheduled_start__lt=end_dt, scheduled_end__gt=start_dt,
    ).exclude(status='cancelled').exists():
        return ServiceResult(ok=False, error='Aircraft already booked at that time')

    # Instructor conflict
    if instructor and Booking.objects.filter(
        club=club, instructor=instructor,
        scheduled_start__lt=end_dt, scheduled_end__gt=start_dt,
    ).exclude(status='cancelled').exists():
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

    # Aircraft conflict (exclude self, ignore cancelled)
    if Booking.objects.filter(
        club=club, aircraft=aircraft,
        scheduled_start__lt=end_dt, scheduled_end__gt=start_dt,
    ).exclude(id=booking.id).exclude(status='cancelled').exists():
        return ServiceResult(ok=False, error='Aircraft already booked at that time')

    # Instructor conflict
    if instructor and Booking.objects.filter(
        club=club, instructor=instructor,
        scheduled_start__lt=end_dt, scheduled_end__gt=start_dt,
    ).exclude(id=booking.id).exclude(status='cancelled').exists():
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

    booking.aircraft = aircraft
    booking.instructor = instructor
    booking.scheduled_start = start_dt
    booking.scheduled_end = end_dt
    booking.description = description
    booking.blockout_override = bool(hits and override)
    booking.save()

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

    # Aircraft conflict
    if Booking.objects.filter(
        club=club, aircraft=target_aircraft,
        scheduled_start__lt=new_end_dt, scheduled_end__gt=new_start_dt,
    ).exclude(id=booking.id).exclude(status='cancelled').exists():
        return ServiceResult(ok=False, error='Aircraft not available at new time')

    # Instructor conflict (only if we're explicitly setting one)
    if instructor is not None and instructor and Booking.objects.filter(
        club=club, instructor=instructor,
        scheduled_start__lt=new_end_dt, scheduled_end__gt=new_start_dt,
    ).exclude(id=booking.id).exclude(status='cancelled').exists():
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

    booking.scheduled_start = new_start_dt
    booking.scheduled_end   = new_end_dt
    if aircraft:
        booking.aircraft = aircraft
    if instructor is not None:
        booking.instructor = target_instructor
    booking.blockout_override = bool(hits and override)
    booking.save()

    recompute_blockout_conflict(booking)
    audit(booking, actor.user, 'field_changed', notes='Rescheduled')
    if hits and override:
        audit(booking, actor.user, 'warning_acknowledged',
              notes=f"Staff override of block-out on reschedule: {msg}")
    return ServiceResult(ok=True)
