"""
ClubHangar access-control helpers.

All "can an actor do X?" logic lives here so that adding or changing a role
only requires editing this file. Views should import and call these functions
rather than inlining flag combinations.

Usage pattern (walrus operator, Python 3.8+):

    from .permissions import require_staff, require_admin, require_manage

    if err := require_staff(actor, club, request):
        return err

For AJAX/API endpoints use the _api variants, which return JsonResponse.
"""

from django.shortcuts import render
from django.http import JsonResponse


# ---------------------------------------------------------------------------
# Boolean predicates — what is this actor?
# ---------------------------------------------------------------------------

def is_staff(actor) -> bool:
    """Admin or instructor. Can access all Manage pages and perform operational actions."""
    return actor.is_admin or actor.is_instructor


def is_admin_only(actor) -> bool:
    """Strictly admin (not instructor). Required for financial and configuration actions."""
    return actor.is_admin


def can_manage(actor) -> bool:
    """Has manage-access permission (admin, or role-granted manage access)."""
    return actor.can_access_manage


def can_access_fleet(actor) -> bool:
    return actor.can_access_fleet


def can_access_safety(actor) -> bool:
    return actor.can_access_safety


def can_access_reports(actor) -> bool:
    return actor.can_access_reports


# ---------------------------------------------------------------------------
# Booking-specific predicates
# ---------------------------------------------------------------------------

def can_depart_booking(actor, booking) -> bool:
    """
    Staff can depart any booking. A plain member may only depart their own
    solo (no-instructor) booking.
    """
    if is_staff(actor):
        return True
    is_own = (booking.member == actor)
    is_solo = bool(booking.flight_type and booking.flight_type.is_solo)
    return is_own and is_solo and not booking.instructor_id


def can_edit_booking(actor, booking) -> bool:
    """
    Staff can edit any booking. A plain member may only edit their own
    booking while it is still pending.
    """
    if is_staff(actor):
        return True
    return booking.member == actor and booking.status == 'pending'


def can_view_booking(actor, booking) -> bool:
    """Staff can view any booking. Members can view their own."""
    return is_staff(actor) or (booking.member == actor)


# ---------------------------------------------------------------------------
# Booking block — financial eligibility check
# ---------------------------------------------------------------------------

def check_booking_block(club_member, config):
    """
    Returns (blocked: bool, message: str).

    Instructors are always exempt — they fly professionally and should never
    be locked out by their own account. Admins are also exempt.

    Conditions (each independent, all must be enabled via config):
      1. Credit limit: balance < -(booking_block_credit_limit)
      2. Unpaid flight charges older than booking_block_unpaid_flight_days days
      3. Unpaid invoices older than booking_block_invoice_days days
    """
    if not config.booking_block_enabled:
        return False, ''

    # Instructors and admins are exempt
    if club_member.is_instructor or club_member.is_admin:
        return False, ''

    from datetime import date, timedelta
    from .models import FlightCompletion, Invoice

    reasons = []

    # 1. Unpaid flight charges
    if config.booking_block_unpaid_flight_days is not None:
        cutoff = date.today() - timedelta(days=config.booking_block_unpaid_flight_days)
        has_old_flight = FlightCompletion.objects.filter(
            booking__member=club_member,
            booking__club=club_member.club,
            total_charge__gt=0,
            paid_at__isnull=True,
            booking__scheduled_start__date__lte=cutoff,
        ).exclude(payment_method='invoice').exists()
        if has_old_flight:
            reasons.append('unpaid_flights')

    # 2. Overdue invoices
    if config.booking_block_invoice_days is not None:
        cutoff = date.today() - timedelta(days=config.booking_block_invoice_days)
        has_old_invoice = Invoice.objects.filter(
            member=club_member,
            club=club_member.club,
            status='sent',
            issue_date__lte=cutoff,
        ).exists()
        if has_old_invoice:
            reasons.append('overdue_invoice')

    if not reasons:
        return False, ''

    # Build message — custom text takes priority, then billing contact
    if config.booking_block_message.strip():
        message = config.booking_block_message.strip()
    else:
        parts = ['Your account has an outstanding balance that needs to be resolved before you can make a booking.']
        contacts = []
        if config.billing_phone:
            contacts.append(f'call {config.billing_phone}')
        if config.billing_email:
            contacts.append(f'email {config.billing_email}')
        if contacts:
            parts.append('Please ' + ' or '.join(contacts) + ' to discuss.')
        else:
            parts.append('Please contact the club to discuss.')
        message = ' '.join(parts)

    return True, message


# ---------------------------------------------------------------------------
# Full-page view gates — return a 403 render or None
# ---------------------------------------------------------------------------

def _no_access(request, club):
    return render(request, 'core/no_access.html', {'club': club}, status=403)


def require_staff(actor, club, request):
    """Return a 403 page if actor is not admin or instructor, else None."""
    if not is_staff(actor):
        return _no_access(request, club)
    return None


def require_admin(actor, club, request):
    """Return a 403 page if actor is not an admin, else None."""
    if not is_admin_only(actor):
        return _no_access(request, club)
    return None


def require_manage(actor, club, request):
    """Return a 403 page if actor lacks manage access, else None."""
    if not can_manage(actor):
        return _no_access(request, club)
    return None


# ---------------------------------------------------------------------------
# AJAX/API view gates — return a JsonResponse 403 or None
# ---------------------------------------------------------------------------

def _api_403(msg='Not authorized'):
    return JsonResponse({'error': msg}, status=403)


def require_staff_api(actor):
    """Return a 403 JsonResponse if actor is not staff, else None."""
    if not is_staff(actor):
        return _api_403()
    return None


def require_admin_api(actor):
    if not is_admin_only(actor):
        return _api_403()
    return None
