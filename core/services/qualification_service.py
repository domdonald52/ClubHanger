"""
Qualification / eligibility checking service.

Checks whether a member is eligible to fly a given booking. Returns a list
of EligibilityItem records — each with a severity and human-readable message.

Severities:
  'ok'      — check passed, show green
  'warn'    — issue exists but staff can override (advisory)
  'block'   — member should not proceed; staff can override with recorded reason
  'info'    — neutral information (no action needed)

Rules (all pinned in decisions log):
  1. PPL — credential must exist and not be expired
  2. Medical — appropriate class credential must exist, not expired, and not
     approaching expiry by config.medical_warning_days
  3. BFR (Flight Review) — must have a Flight Review credential within
     config.bfr_interval_months
  4. Type rating — credential matching aircraft type must exist and not be expired
  5. Recency — for solo/private flights, warn if member hasn't flown the
     aircraft TYPE at this club in config.recency_warning_days days

Block/warn rule (confirmed in decisions):
  Members are blocked on any 'block' item.
  Instructors/admins see 'block' items as warn-and-override (recorded in audit).
  Staff always can override; override must be recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional


@dataclass
class EligibilityItem:
    check: str               # machine key, e.g. 'ppl', 'medical', 'bfr', 'type_rating', 'recency'
    label: str               # human label for the check
    severity: str            # 'ok' | 'warn' | 'block' | 'info'
    message: str             # detail shown to user
    can_override: bool = True  # False only for checks that are genuinely hard stops


@dataclass
class EligibilityResult:
    items: List[EligibilityItem] = field(default_factory=list)

    @property
    def has_blocks(self) -> bool:
        return any(i.severity == 'block' for i in self.items)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity in ('warn', 'block') for i in self.items)

    @property
    def all_ok(self) -> bool:
        return not self.has_warnings


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def check_eligibility(booking, config=None) -> EligibilityResult:
    """
    Run all eligibility checks for a booking.

    booking — a Booking instance (must have .member, .aircraft, .flight_type pre-fetched)
    config  — ClubConfig instance; fetched automatically if not provided
    """
    from ..models import ClubConfig

    if config is None:
        config, _ = ClubConfig.objects.get_or_create(club=booking.club)

    result = EligibilityResult()
    member = booking.member

    if member is None:
        result.items.append(EligibilityItem(
            check='member', label='Member', severity='block',
            message='No member assigned to this booking',
        ))
        return result

    today = date.today()
    credentials = list(member.user.credentials.select_related('credential_type', 'aircraft_type').all())

    _check_ppl(result, credentials, today)
    _check_medical(result, credentials, today, config, member)
    _check_bfr(result, credentials, today, config)
    _check_type_rating(result, credentials, today, booking)
    _check_recency(result, booking, today, config)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Individual checks
# ─────────────────────────────────────────────────────────────────────────────

def _check_ppl(result: EligibilityResult, credentials, today: date) -> None:
    ppl = _latest(credentials, 'ppl')
    if ppl is None:
        result.items.append(EligibilityItem(
            check='ppl', label='PPL',
            severity='block',
            message='No PPL credential recorded for this member',
        ))
    elif ppl.expiry_date and ppl.expiry_date < today:
        result.items.append(EligibilityItem(
            check='ppl', label='PPL',
            severity='block',
            message=f'PPL expired on {ppl.expiry_date:%d %b %Y}',
        ))
    else:
        result.items.append(EligibilityItem(
            check='ppl', label='PPL', severity='ok',
            message=f'PPL current{_expiry_suffix(ppl)}',
        ))


def _check_medical(result: EligibilityResult, credentials, today: date,
                   config, member) -> None:
    MEDICAL_TYPES = [
        ('medical_c1', 'Class 1 Medical',
         config.medical_class1_under40, config.medical_class1_over40),
        ('medical_c2', 'Class 2 Medical',
         config.medical_class2_under40, config.medical_class2_over40),
        ('medical_c3', 'Class 3 Medical',
         config.medical_class3_under40, config.medical_class3_over40),
        ('dlr9', 'DLR9 Medical', None, None),
    ]

    # Find the best (highest-class) current medical
    dob = getattr(member, 'date_of_birth', None)
    age = (today - dob).days // 365 if dob else None

    found_any = False
    for cred_type, label, months_u40, months_o40 in MEDICAL_TYPES:
        med = _latest(credentials, cred_type)
        if med is None:
            continue
        found_any = True

        # Determine validity period
        if months_u40 is not None and age is not None:
            validity_months = months_o40 if age >= 40 else months_u40
        elif months_u40 is not None:
            validity_months = months_u40  # conservative: assume shorter if DOB unknown
        else:
            validity_months = None

        if med.expiry_date and med.expiry_date < today:
            result.items.append(EligibilityItem(
                check='medical', label=label, severity='block',
                message=f'{label} expired on {med.expiry_date:%d %b %Y}',
            ))
        elif med.expiry_date:
            days_left = (med.expiry_date - today).days
            if days_left <= config.medical_warning_days:
                result.items.append(EligibilityItem(
                    check='medical', label=label, severity='warn',
                    message=f'{label} expires in {days_left} day{"s" if days_left != 1 else ""} '
                            f'({med.expiry_date:%d %b %Y})',
                ))
            else:
                result.items.append(EligibilityItem(
                    check='medical', label=label, severity='ok',
                    message=f'{label} current — expires {med.expiry_date:%d %b %Y}',
                ))
        else:
            result.items.append(EligibilityItem(
                check='medical', label=label, severity='info',
                message=f'{label} recorded (no expiry date set)',
            ))
        return  # stop at the first found medical class

    if not found_any:
        result.items.append(EligibilityItem(
            check='medical', label='Medical',
            severity='block',
            message='No medical certificate recorded for this member',
        ))


def _check_bfr(result: EligibilityResult, credentials, today: date, config) -> None:
    fr = _latest(credentials, 'fr')
    if fr is None:
        result.items.append(EligibilityItem(
            check='bfr', label='Flight Review',
            severity='block',
            message='No Flight Review (BFR) recorded for this member',
        ))
        return

    if fr.expiry_date:
        if fr.expiry_date < today:
            result.items.append(EligibilityItem(
                check='bfr', label='Flight Review', severity='block',
                message=f'Flight Review expired on {fr.expiry_date:%d %b %Y}',
            ))
        elif (fr.expiry_date - today).days <= 30:
            result.items.append(EligibilityItem(
                check='bfr', label='Flight Review', severity='warn',
                message=f'Flight Review expires in {(fr.expiry_date - today).days} days',
            ))
        else:
            result.items.append(EligibilityItem(
                check='bfr', label='Flight Review', severity='ok',
                message=f'Flight Review current — expires {fr.expiry_date:%d %b %Y}',
            ))
        return

    # No explicit expiry — derive from issue date + interval
    if fr.issue_date:
        due_date = fr.issue_date + timedelta(days=config.bfr_interval_months * 30)
        if due_date < today:
            result.items.append(EligibilityItem(
                check='bfr', label='Flight Review', severity='block',
                message=f'Flight Review overdue (issued {fr.issue_date:%d %b %Y}, '
                        f'{config.bfr_interval_months}-month interval)',
            ))
        elif (due_date - today).days <= 30:
            result.items.append(EligibilityItem(
                check='bfr', label='Flight Review', severity='warn',
                message=f'Flight Review due in {(due_date - today).days} days',
            ))
        else:
            result.items.append(EligibilityItem(
                check='bfr', label='Flight Review', severity='ok',
                message=f'Flight Review current — due {due_date:%d %b %Y}',
            ))
    else:
        result.items.append(EligibilityItem(
            check='bfr', label='Flight Review', severity='info',
            message='Flight Review recorded but no issue or expiry date set',
        ))


def _check_type_rating(result: EligibilityResult, credentials, today: date, booking) -> None:
    aircraft_type = booking.aircraft.aircraft_type if booking.aircraft else None
    if aircraft_type is None:
        result.items.append(EligibilityItem(
            check='type_rating', label='Type Rating', severity='info',
            message='Aircraft has no type assigned — type rating check skipped',
        ))
        return

    type_ratings = [
        c for c in credentials
        if c.credential_type.category == 'type_rating'
        and c.aircraft_type_id == aircraft_type.id
    ]
    if not type_ratings:
        result.items.append(EligibilityItem(
            check='type_rating', label='Type Rating',
            severity='block',
            message=f'No type rating recorded for {aircraft_type.name}',
        ))
        return

    # Use the most recent / latest-expiry rating
    rating = sorted(type_ratings, key=lambda c: c.expiry_date or date.max)[-1]
    if rating.expiry_date and rating.expiry_date < today:
        result.items.append(EligibilityItem(
            check='type_rating', label='Type Rating', severity='block',
            message=f'Type rating for {aircraft_type.name} expired {rating.expiry_date:%d %b %Y}',
        ))
    else:
        result.items.append(EligibilityItem(
            check='type_rating', label='Type Rating', severity='ok',
            message=f'Type rating for {aircraft_type.name} current{_expiry_suffix(rating)}',
        ))


def _check_recency(result: EligibilityResult, booking, today: date, config) -> None:
    """
    Warn if the member hasn't flown this aircraft TYPE at this club within
    config.recency_warning_days days. Only applies to solo/private flights.
    """
    from ..models import FlightCompletion, Booking as _Booking

    ft = booking.flight_type
    if ft is None or not (ft.is_solo or (not ft.is_training)):
        return  # only check for solo/private flights

    aircraft_type = booking.aircraft.aircraft_type if booking.aircraft else None
    if aircraft_type is None:
        return

    cutoff = today - timedelta(days=config.recency_warning_days)
    recent = _Booking.objects.filter(
        club=booking.club,
        member=booking.member,
        aircraft__aircraft_type=aircraft_type,
        status='completed',
        arrived_at__date__gte=cutoff,
    ).exclude(id=booking.id).exists()

    if not recent:
        result.items.append(EligibilityItem(
            check='recency', label='Recency',
            severity='warn',
            message=(
                f'No completed flight on a {aircraft_type.name} at this club '
                f'in the last {config.recency_warning_days} days'
            ),
        ))
    else:
        result.items.append(EligibilityItem(
            check='recency', label='Recency', severity='ok',
            message=f'Recent {aircraft_type.name} flight on record',
        ))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _latest(credentials, code):
    """Return the most recently issued credential of a given type code, or None."""
    matching = [c for c in credentials if c.credential_type.code == code]
    if not matching:
        return None
    return sorted(matching, key=lambda c: c.issue_date or date.min, reverse=True)[0]


def _expiry_suffix(cred) -> str:
    if cred.expiry_date:
        return f' — expires {cred.expiry_date:%d %b %Y}'
    return ''
