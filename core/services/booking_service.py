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
