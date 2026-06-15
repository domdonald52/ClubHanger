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

    if item_type == 'one_off' and description:
        if FlightChargeItem.objects.filter(
            flight_completion=fc, item_type='one_off', description__iexact=description
        ).exists():
            return ServiceResult(ok=False, error=f'A charge "{description}" already exists. Delete it first to add a new one.')

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


def _debit_account(acct, booking, fc, pay_amount, method, user):
    """Create AccountTransaction and debit account balance for a credit payment."""
    from ..models import AccountTransaction
    AccountTransaction.objects.create(
        account=acct,
        transaction_type='flight',
        direction='debit',
        amount=pay_amount,
        description=f'Flight {booking.aircraft.registration} {booking.scheduled_start.date()}',
        flight_completion=fc,
        payment_method=method,
        created_by=user,
    )
    acct.apply_transaction(pay_amount, 'debit')


def _check_credit_headroom(acct, pay_amount):
    """Return error string if credit payment would exceed the account's credit limit, else None."""
    projected = acct.balance - pay_amount
    if acct.credit_limit is not None and projected < -acct.credit_limit:
        shortfall = abs(projected) - acct.credit_limit
        return (
            f'Insufficient account balance. '
            f'Current balance: ${acct.balance}, payment: ${pay_amount:.2f}, '
            f'credit limit: ${acct.credit_limit}. '
            f'Account would be ${shortfall:.2f} short.'
        )
    return None


def record_payment(fc, booking, user, amount, method: str, member=None) -> ServiceResult:
    """
    Record an immediate payment against a FlightCompletion.

    Creates a FlightPayment row (paid_at=now) for `member` (defaults to booking.member).
    If method=='credit', debits the member's account balance.
    Syncs the denormalised fc payment cache.
    """
    from django.utils import timezone
    from ..models import Account, FlightPayment

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

    payee = member or booking.member
    acct, _ = Account.objects.get_or_create(club_member=payee, defaults={'balance': 0})

    if method == 'credit':
        err = _check_credit_headroom(acct, pay_amount)
        if err:
            return ServiceResult(ok=False, error=err)

    fp = FlightPayment.objects.create(
        completion=fc,
        member=payee,
        amount=pay_amount,
        method=method,
        paid_at=timezone.now(),
        recorded_by=user,
    )

    if method == 'credit':
        _debit_account(acct, booking, fc, pay_amount, method, user)

    fc._sync_payment_cache()

    if fc.is_paid:
        msg = 'Payment recorded — fully settled.'
    else:
        msg = f'Payment of ${pay_amount:.2f} recorded. Balance owing: ${fc.balance_owing:.2f}.'

    return ServiceResult(ok=True, data={'message': msg, 'fully_paid': fc.is_paid, 'payment_id': fp.id})


def allocate_payment(fc, booking, user, amount, method: str, member=None) -> ServiceResult:
    """
    Create a pending FlightPayment allocation (paid_at=None).
    Money not yet collected — records the intended split upfront.
    """
    from ..models import FlightPayment

    try:
        alloc_amount = round(Decimal(str(amount)), 2)
    except (InvalidOperation, TypeError):
        return ServiceResult(ok=False, error='Invalid amount')

    if alloc_amount <= 0:
        return ServiceResult(ok=False, error='Amount must be greater than zero')

    payee = member or booking.member
    fp = FlightPayment.objects.create(
        completion=fc,
        member=payee,
        amount=alloc_amount,
        method=method,
        paid_at=None,
        recorded_by=user,
    )
    return ServiceResult(ok=True, data={'payment_id': fp.id})


def record_allocated_payment(fc, booking, user, payment_id) -> ServiceResult:
    """Mark a pending FlightPayment as paid (set paid_at=now)."""
    from django.utils import timezone
    from ..models import Account, FlightPayment

    try:
        fp = fc.payments.get(id=payment_id, paid_at__isnull=True)
    except FlightPayment.DoesNotExist:
        return ServiceResult(ok=False, error='Pending payment not found')

    pay_amount = fp.amount
    payee = fp.member
    acct, _ = Account.objects.get_or_create(club_member=payee, defaults={'balance': 0})

    if fp.method == 'credit':
        err = _check_credit_headroom(acct, pay_amount)
        if err:
            return ServiceResult(ok=False, error=err)

    fp.paid_at = timezone.now()
    fp.save(update_fields=['paid_at'])

    if fp.method == 'credit':
        _debit_account(acct, booking, fc, pay_amount, fp.method, user)

    fc._sync_payment_cache()

    if fc.is_paid:
        msg = f'Payment of ${pay_amount:.2f} recorded — fully settled.'
    else:
        msg = f'Payment of ${pay_amount:.2f} recorded. Balance owing: ${fc.balance_owing:.2f}.'

    return ServiceResult(ok=True, data={'message': msg, 'fully_paid': fc.is_paid})


def remove_payment_allocation(fc, payment_id) -> ServiceResult:
    """Delete a pending (unpaid) FlightPayment allocation."""
    from ..models import FlightPayment
    deleted, _ = FlightPayment.objects.filter(
        completion=fc, id=payment_id, paid_at__isnull=True
    ).delete()
    if not deleted:
        return ServiceResult(ok=False, error='Pending allocation not found')
    return ServiceResult(ok=True)


def reverse_payment(fc, booking, user, payment_id=None) -> ServiceResult:
    """
    Reverse a recorded payment on a FlightCompletion.

    If payment_id given: reverse that specific FlightPayment row.
    Otherwise: reverse all paid rows (backward compat).
    For credit payments: restores account balance via AccountTransaction.
    """
    from ..models import Account, AccountTransaction, FlightPayment

    if payment_id:
        rows = list(fc.payments.filter(id=payment_id, paid_at__isnull=False))
    else:
        rows = list(fc.payments.filter(paid_at__isnull=False))

    if not rows:
        return ServiceResult(ok=False, error='No payment found to reverse')

    for fp in rows:
        if fp.method == 'credit':
            acct, _ = Account.objects.get_or_create(club_member=fp.member, defaults={'balance': 0})
            AccountTransaction.objects.create(
                account=acct,
                transaction_type='adjustment',
                direction='credit',
                amount=fp.amount,
                description=(
                    f'Payment reversal — {booking.aircraft.registration} '
                    f'{booking.scheduled_start.strftime("%-d %b %Y")}'
                ),
                flight_completion=fc,
                payment_method=fp.method,
                created_by=user,
            )
            acct.apply_transaction(fp.amount, 'credit')
        fp.paid_at = None
        fp.save(update_fields=['paid_at'])

    fc._sync_payment_cache()
    total_reversed = sum(fp.amount for fp in rows)
    return ServiceResult(
        ok=True,
        data={'message': f'Payment of ${total_reversed:.2f} reversed. Flight is now unpaid.'}
    )


def record_multi_payment(primary_fc, primary_booking, user, method: str,
                         fc_amounts: list, received: Decimal) -> ServiceResult:
    """
    Record a single payment session across multiple FlightCompletions.

    fc_amounts: [(fc, booking, requested_amount), ...] ordered primary-first then oldest.
    received:   actual money collected — distributed top-down; remainder stays outstanding.

    For credit payments: account balance checked against `received` upfront.
    Each fc gets its own FlightPayment row and AccountTransaction.
    """
    from django.utils import timezone
    from ..models import Account, FlightPayment

    if received <= 0:
        return ServiceResult(ok=False, error='Amount received must be greater than zero')

    payee = primary_booking.member
    acct, _ = Account.objects.get_or_create(club_member=payee, defaults={'balance': 0})

    if method == 'credit':
        err = _check_credit_headroom(acct, received)
        if err:
            return ServiceResult(ok=False, error=err)

    remaining = received
    messages = []

    for fc, bk, requested in fc_amounts:
        if remaining <= 0:
            break
        to_pay = min(remaining, requested, fc.balance_owing)
        if to_pay <= 0:
            continue

        FlightPayment.objects.create(
            completion=fc,
            member=payee,
            amount=to_pay,
            method=method,
            paid_at=timezone.now(),
            recorded_by=user,
        )

        if method == 'credit':
            _debit_account(acct, bk, fc, to_pay, method, user)

        fc._sync_payment_cache()
        remaining -= to_pay
        messages.append(f'{bk.aircraft.registration} {bk.scheduled_start.strftime("%-d %b")}: ${to_pay:.2f}')

    total_applied = received - remaining
    msg = f'Payment of ${total_applied:.2f} recorded across {len(messages)} flight(s): ' + ', '.join(messages)
    if remaining > 0:
        msg += f'. ${remaining:.2f} not applied (no remaining balance).'

    return ServiceResult(ok=True, data={'message': msg, 'total_applied': float(total_applied)})


def record_refund(fc, booking, user, amount, method: str) -> ServiceResult:
    """
    Record a refund against an overpaid FlightCompletion.

    'credit'  — adds the refund amount back to the member's account balance.
    Any other method (eftpos, cash) — just logs the refund; physical refund
    is handled offline.

    The refund amount must not exceed the overpayment (amount_paid - total_charge).
    """
    from django.utils import timezone
    from ..models import Account, AccountTransaction

    try:
        refund_amount = round(Decimal(str(amount)), 2)
    except (InvalidOperation, TypeError):
        return ServiceResult(ok=False, error='Invalid refund amount')

    if refund_amount <= 0:
        return ServiceResult(ok=False, error='Refund amount must be greater than zero')

    overpayment = (fc.amount_paid or Decimal('0')) - fc.total_charge
    if overpayment <= 0:
        return ServiceResult(ok=False, error='No overpayment to refund')
    if refund_amount > overpayment:
        return ServiceResult(
            ok=False,
            error=f'Refund ${refund_amount:.2f} exceeds overpayment of ${overpayment:.2f}',
        )

    acct, _ = Account.objects.get_or_create(
        club_member=booking.member, defaults={'balance': 0}
    )

    if method == 'credit':
        AccountTransaction.objects.create(
            account=acct,
            transaction_type='adjustment',
            direction='credit',
            amount=refund_amount,
            description=(
                f'Overpayment refund — {booking.aircraft.registration} '
                f'{booking.scheduled_start.strftime("%-d %b %Y")}'
            ),
            flight_completion=fc,
            payment_method='account',
            created_by=user,
        )
        acct.apply_transaction(refund_amount, 'credit')
        msg = f'${refund_amount:.2f} credited to member account.'
    else:
        # Physical refund — no AccountTransaction (would corrupt account balance).
        # The reduction in fc.amount_paid is the audit record.
        msg = f'${refund_amount:.2f} refund recorded ({method}). Physical refund handled offline.'

    fc.amount_paid = (fc.amount_paid or Decimal('0')) - refund_amount
    fc.save(update_fields=['amount_paid'])

    return ServiceResult(ok=True, data={'message': msg})
