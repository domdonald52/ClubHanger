"""
Charging and payment service.

Handles adding/removing charge line items on a FlightCompletion and
recording payments against it.  No HTTP objects; callers own request
parsing and response building.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional

from .booking_service import ServiceResult, update_total


def add_charge(
    fc,
    item_type: str,
    description: str,
    amount,
    *,
    aerodrome=None,
    fee_type=None,
    custom_icao: str = '',
    custom_name: str = '',
    quantity: int = 1,
    unit_amount=None,
) -> ServiceResult:
    """
    Add a charge line item to a FlightCompletion.
    Also creates a FlightLandingEntry for landing-fee items.
    """
    from ..models import FlightChargeItem, FlightLandingEntry

    if not description or not amount:
        return ServiceResult(ok=False, error='Description and amount are required')

    try:
        amount_d = Decimal(str(amount))
    except InvalidOperation:
        return ServiceResult(ok=False, error='Invalid amount')

    ci = FlightChargeItem.objects.create(
        flight_completion=fc,
        item_type=item_type,
        description=description,
        amount=amount_d,
    )

    if item_type == 'landing':
        FlightLandingEntry.objects.create(
            flight_completion=fc,
            aerodrome=aerodrome,
            custom_icao=custom_icao,
            custom_name=custom_name,
            fee_type=fee_type,
            fee_type_name=description,
            quantity=quantity,
            unit_amount=unit_amount if unit_amount is not None else amount_d,
            total_fee=amount_d,
        )

    update_total(fc)
    return ServiceResult(ok=True, data={'item_id': ci.id})


def delete_charge(fc, item_id) -> ServiceResult:
    """Remove a charge line item from a FlightCompletion."""
    from ..models import FlightChargeItem

    deleted, _ = FlightChargeItem.objects.filter(
        flight_completion=fc, id=item_id
    ).delete()
    if not deleted:
        return ServiceResult(ok=False, error='Charge item not found')
    update_total(fc)
    return ServiceResult(ok=True)


def record_payment(fc, booking, user, amount, method: str) -> ServiceResult:
    """
    Record a payment against a FlightCompletion.

    Validates:
    - amount > 0
    - amount ≤ balance owing
    - if method == 'credit': account balance must cover the payment

    On success: updates fc.amount_paid, creates AccountTransaction,
    updates Account.balance for credit payments.
    """
    from django.utils import timezone
    from ..models import Account, AccountTransaction

    try:
        pay_amount = round(Decimal(str(amount)), 2)
    except (InvalidOperation, TypeError):
        return ServiceResult(ok=False, error='Invalid payment amount')

    if pay_amount <= 0:
        return ServiceResult(ok=False, error='Payment amount must be greater than zero')
    if pay_amount > fc.balance_owing:
        return ServiceResult(
            ok=False,
            error=f'Payment amount ${pay_amount:.2f} exceeds the balance owing (${fc.balance_owing})',
        )

    acct, _ = Account.objects.get_or_create(
        club_member=booking.member, defaults={'balance': 0}
    )

    if method == 'credit':
        projected = acct.balance - pay_amount
        if acct.credit_limit is not None and projected < -acct.credit_limit:
            shortfall = abs(projected) - acct.credit_limit
            return ServiceResult(
                ok=False,
                error=(
                    f'Insufficient account balance. '
                    f'Current balance: ${acct.balance}, payment: ${pay_amount:.2f}, '
                    f'credit limit: ${acct.credit_limit}. '
                    f'Account would be ${shortfall:.2f} short.'
                ),
            )

    fc.amount_paid = (fc.amount_paid or Decimal('0')) + pay_amount
    fc.payment_method = method
    if fc.paid_at is None:
        fc.paid_at = timezone.now()
    fc.save(update_fields=['payment_method', 'paid_at', 'total_charge', 'amount_paid'])

    AccountTransaction.objects.create(
        account=acct,
        transaction_type='flight',
        direction='debit',
        amount=pay_amount,
        description=(
            f'Flight {booking.aircraft.registration} {booking.scheduled_start.date()}'
            + (f' (partial — ${fc.balance_owing:.2f} remaining)' if fc.is_partially_paid else '')
        ),
        flight_completion=fc,
        payment_method=method,
        created_by=user,
    )
    if method == 'credit':
        acct.apply_transaction(pay_amount, 'debit')

    if fc.is_paid:
        msg = 'Payment recorded — fully settled.'
    else:
        msg = f'Partial payment of ${pay_amount:.2f} recorded. Balance owing: ${fc.balance_owing:.2f}.'

    return ServiceResult(ok=True, data={'message': msg, 'fully_paid': fc.is_paid})
