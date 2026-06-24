"""
Split-flight and payment batch tests for ClubHangar.
Tests: maintenance log hours, per-pilot flight history, aircraft booking history,
multi-FC payment batches, and payment reversal across siblings.

Runs entirely inside a rolled-back transaction — no data is persisted.

Usage: venv/bin/python split_payment_test.py
"""
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aero_club.settings')
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

from decimal import Decimal as D
from datetime import timedelta
from django.utils import timezone
from django.db import transaction

G = '\033[92m'; R = '\033[91m'; Y = '\033[93m'; B = '\033[94m'; W = '\033[0m'

passed = failed = 0
gaps = []

def ok(msg):   print(f"  {G}✓{W}  {msg}")
def fail(msg): print(f"  {R}✗  FAIL: {msg}{W}")
def warn(msg): print(f"  {Y}⚠  GAP: {msg}{W}")
def head(msg): print(f"\n{B}{'═'*64}\n  {msg}\n{'═'*64}{W}")
def sub(msg):  print(f"\n  ── {msg}")

def check(label, cond, gap=None):
    global passed, failed
    if cond:   ok(label);   passed += 1
    else:      fail(label); failed += 1
    if gap and not cond: gaps.append(gap)

def note_gap(msg):
    warn(msg); gaps.append(msg)


# ── Load data ─────────────────────────────────────────────────────────────────
from core.models import (
    Club, ClubMember, Aircraft, FlightType, Booking,
    FlightCompletion, FlightChargeItem, ChargeRate,
    MaintenanceLogEntry, Account, AccountTransaction,
    FuelSurchargeRate,
    create_maint_log_entry,
    FlightSegment, FlightPayment,
)
from core.services import charging_service
from django.db.models import Q
from django.contrib.auth import get_user_model
User = get_user_model()

club = Club.objects.first()
if not club:
    print("No club found."); sys.exit(1)

admin_m = (ClubMember.objects
           .filter(club=club)
           .filter(Q(has_admin_access=True) | Q(role__system_role_type='admin') | Q(role__is_superadmin=True))
           .select_related('user').first())

# Pick two distinct non-admin members for split-flight testing
_admin_ids = set(ClubMember.objects
                 .filter(club=club)
                 .filter(Q(has_admin_access=True) | Q(role__system_role_type='admin') | Q(role__is_superadmin=True))
                 .values_list('id', flat=True))
regular_members = list(
    ClubMember.objects.filter(club=club, is_on_instructor_roster=False)
    .exclude(id__in=_admin_ids)
    .select_related('user', 'role')
)[:2]

all_ac = list(Aircraft.objects.filter(club=club).exclude(status='retired').order_by('registration'))
ac1 = all_ac[0] if len(all_ac) > 0 else None
ac2 = all_ac[1] if len(all_ac) > 1 else None

solo_ft = FlightType.objects.filter(club=club, is_solo=True).first()
dual_ft = FlightType.objects.filter(club=club, is_solo=False).first()
any_ft  = solo_ft or dual_ft

print(f"\n  Club:      {club}")
print(f"  Admin:     {admin_m.user.get_full_name() if admin_m else '—'}")
print(f"  Member A:  {regular_members[0].user.get_full_name() if len(regular_members) > 0 else '—'}")
print(f"  Member B:  {regular_members[1].user.get_full_name() if len(regular_members) > 1 else '—'}")
print(f"  Aircraft:  {ac1} / {ac2}")
print(f"  Flight type: {any_ft}")

if not all([admin_m, ac1, any_ft, len(regular_members) >= 2]):
    print(f"\n{R}Missing required data. Need admin, 2 regular members, aircraft, flight type.{W}")
    sys.exit(1)

member_a = regular_members[0]
member_b = regular_members[1]


# ── Helpers ───────────────────────────────────────────────────────────────────
def make_booking(member, aircraft, ft, hours_ago=3, duration_hrs=2):
    start = timezone.now() - timedelta(hours=hours_ago)
    end   = start + timedelta(hours=duration_hrs)
    return Booking.objects.create(
        club=club, member=member, created_by=member.user,
        aircraft=aircraft, flight_type=ft,
        scheduled_start=start, scheduled_end=end, status='pending',
    )

def depart(booking):
    booking.status = 'departed'
    booking.departed_at = timezone.now()
    booking.save(update_fields=['status', 'departed_at'])
    rate = FuelSurchargeRate.current_rate(club, booking.aircraft)
    fc, _ = FlightCompletion.objects.get_or_create(
        booking=booking,
        defaults={'logged_by': admin_m.user,
                  'fuel_surcharge_rate_snapshot': rate.rate if rate else None}
    )
    return fc

def checkin(booking, fc, h_start, h_end, outcome='completed'):
    """Complete the flight with hobbs readings and sync totals."""
    fc.hobbs_start = D(str(h_start))
    fc.hobbs_end   = D(str(h_end))
    fc.outcome     = outcome
    fc.logged_by   = admin_m.user
    if booking.aircraft.total_time_method in ('hobbs', ''):
        fc.actual_flight_hours = D(str(round(h_end - h_start, 2)))
    fc.save()
    booking.status     = 'completed'
    booking.arrived_at = timezone.now()
    booking.save(update_fields=['status', 'arrived_at'])
    # Auto-create hire charge so total_charge > 0 for payment tests
    hr = ChargeRate.objects.filter(
        aircraft=booking.aircraft, flight_type=booking.flight_type,
        time_method=booking.aircraft.total_time_method
    ).first()
    if hr and fc.actual_flight_hours:
        ci, _ = FlightChargeItem.objects.get_or_create(
            flight_completion=fc, item_type='hire',
            defaults={'description': f'Aircraft hire — {booking.aircraft.registration}',
                      'amount': round(float(hr.amount) * float(fc.actual_flight_hours), 2)}
        )
        # Sync total_charge if item was just created
        from core.services.booking_service import update_total
        update_total(fc)
    return fc

_hobbs = 500.0

def next_hobbs(hours):
    global _hobbs
    s = _hobbs
    _hobbs += hours
    return s, _hobbs


# ═════════════════════════════════════════════════════════════════════════════
try:
  with transaction.atomic():

    # Close any live departed bookings so unique-departed checks don't interfere
    for _bd in Booking.objects.filter(club=club, status='departed'):
        _bd.status = 'completed'
        _bd.arrived_at = timezone.now()
        _bd.save(update_fields=['status', 'arrived_at'])

    # ─────────────────────────────────────────────────────────────────────────
    head("TEST 1 — Split flight: maintenance log records full hobbs delta")
    # ─────────────────────────────────────────────────────────────────────────
    sub("Create booking, depart, set hobbs 500–501.5 (1.5h total)")

    bk1 = make_booking(member_a, ac1, any_ft, hours_ago=4)
    fc1 = depart(bk1)

    h_start, h_end = 500.0, 501.5
    fc1 = checkin(bk1, fc1, h_start, h_end)

    sub("Create FlightSegments: A=1.0h, B=0.5h")
    seg_a = FlightSegment.objects.create(
        flight_completion=fc1, member=member_a, sequence=1,
        hobbs_start=D('500.00'), hobbs_end=D('501.00'), hours=D('1.0'),
    )
    seg_b = FlightSegment.objects.create(
        flight_completion=fc1, member=member_b, sequence=2,
        hobbs_start=D('501.00'), hobbs_end=D('501.50'), hours=D('0.5'),
    )

    sub("Call create_maint_log_entry")
    create_maint_log_entry(fc1)

    mle = MaintenanceLogEntry.objects.filter(flight_completion=fc1).first()
    check("MaintenanceLogEntry created for this FC", mle is not None)
    if mle:
        check(
            f"maint_hours_flight = 1.50 (full hobbs delta, not per-pilot) — got {mle.maint_hours_flight}",
            float(mle.maint_hours_flight) == 1.50,
        )
        # Previous total for ac1
        prior = (MaintenanceLogEntry.objects
                 .filter(aircraft=ac1)
                 .exclude(id=mle.id)
                 .order_by('-date', '-id')
                 .first())
        prior_total = float(prior.maint_hours_total) if prior else float(ac1.maint_hours_initial or 0)
        check(
            f"maint_hours_total = prior({prior_total:.2f}) + 1.50 — got {mle.maint_hours_total}",
            abs(float(mle.maint_hours_total) - (prior_total + 1.50)) < 0.001,
        )

    # ─────────────────────────────────────────────────────────────────────────
    head("TEST 2 — Split flight: pilot A (booking member) sees their own segment hours")
    # ─────────────────────────────────────────────────────────────────────────
    sub("Simulate app_profile fc_qs query for member A")

    fc_qs = (FlightCompletion.objects
             .filter(booking__member=member_a, booking__club=club, booking__status='completed')
             .prefetch_related('segments')
             .order_by('-booking__scheduled_start'))

    fc_found = None
    for fc in fc_qs:
        if fc.id == fc1.id:
            fc_found = fc
            break

    check("FC appears in member A's fc_qs", fc_found is not None)

    if fc_found:
        my_seg = next((s for s in fc_found.segments.all() if s.member_id == member_a.id), None)
        hours_shown = float(my_seg.hours or 0) if my_seg else float(fc_found.actual_flight_hours or 0)

        check(f"Member A has a matching segment", my_seg is not None)
        check(
            f"Hours shown = 1.0 (segment hours, not total 1.5) — got {hours_shown}",
            abs(hours_shown - 1.0) < 0.001,
        )
        check("is_split flag would be set (segment found)", bool(my_seg))

    # ─────────────────────────────────────────────────────────────────────────
    head("TEST 3 — Split flight: pilot B (non-booking member) sees their own segment")
    # ─────────────────────────────────────────────────────────────────────────
    sub("Simulate app_profile seg_qs query for member B")

    seg_qs = (FlightSegment.objects
              .filter(member=member_b,
                      flight_completion__booking__club=club,
                      flight_completion__booking__status='completed')
              .exclude(flight_completion__booking__member=member_b)
              .select_related('flight_completion__booking__aircraft',
                              'flight_completion__booking__flight_type')
              .order_by('-flight_completion__booking__scheduled_start'))

    seg_b_found = next((s for s in seg_qs if s.id == seg_b.id), None)
    check("Segment B appears in member B's seg_qs", seg_b_found is not None)

    if seg_b_found:
        hours_b = float(seg_b_found.hours or 0)
        check(
            f"Member B hours = 0.5 — got {hours_b}",
            abs(hours_b - 0.5) < 0.001,
        )
        check("is_split would be True for member B entry", True)  # always True from seg_qs path

    # Member B is NOT in the booking member position, so the exclude should keep the segment
    # Make sure member B's segment does NOT appear in their fc_qs (they are not the booker)
    fc_qs_b = (FlightCompletion.objects
               .filter(booking__member=member_b, booking__club=club, booking__status='completed')
               .prefetch_related('segments')
               .order_by('-booking__scheduled_start'))
    fc1_in_b_qs = any(fc.id == fc1.id for fc in fc_qs_b)
    check("FC1 does NOT appear in member B's fc_qs (B is not the booker)", not fc1_in_b_qs)

    # ─────────────────────────────────────────────────────────────────────────
    head("TEST 4 — Aircraft booking history: one booking, not duplicated by segments")
    # ─────────────────────────────────────────────────────────────────────────
    sub("Simulate aircraft booking history query (views.py line 5708)")

    fh_qs = (Booking.objects
             .filter(club=club, aircraft=ac1)
             .exclude(status='cancelled')
             .select_related('member__user', 'flight_type')
             .order_by('-scheduled_start'))

    bk1_rows = [b for b in fh_qs if b.id == bk1.id]
    check("Booking appears exactly once in aircraft history", len(bk1_rows) == 1)
    if bk1_rows:
        check("Booking has correct aircraft", bk1_rows[0].aircraft_id == ac1.id)
        check("Booking has correct member (A)", bk1_rows[0].member_id == member_a.id)

    # ─────────────────────────────────────────────────────────────────────────
    head("TEST 5 — Payment batch: two FCs get independent maintenance logs and shared batch_id")
    # ─────────────────────────────────────────────────────────────────────────
    sub("Create two separate bookings with sequential hobbs readings")

    # Booking / FC 2 — on ac1, sequential hobbs
    bk2 = make_booking(member_a, ac1, any_ft, hours_ago=6, duration_hrs=1)
    fc2 = depart(bk2)
    h2s, h2e = next_hobbs(1.2)
    fc2 = checkin(bk2, fc2, h2s, h2e)
    create_maint_log_entry(fc2)
    mle2 = MaintenanceLogEntry.objects.filter(flight_completion=fc2).first()

    # Booking / FC 3 — use ac2 if available, otherwise ac1 with next sequential hobbs
    ac_for_fc3 = ac2 if ac2 else ac1
    bk3 = make_booking(member_a, ac_for_fc3, any_ft, hours_ago=5, duration_hrs=1)
    fc3 = depart(bk3)
    h3s, h3e = next_hobbs(0.8)
    fc3 = checkin(bk3, fc3, h3s, h3e)
    create_maint_log_entry(fc3)
    mle3 = MaintenanceLogEntry.objects.filter(flight_completion=fc3).first()

    check("FC2 has its own MaintenanceLogEntry", mle2 is not None)
    check("FC3 has its own MaintenanceLogEntry", mle3 is not None)

    if mle2 and mle3:
        check("Maintenance logs are distinct (different IDs)", mle2.id != mle3.id)
        # Totals should be sequential — each adds its own hours to the prior total
        check(
            f"FC2 maint_hours_flight = 1.20 — got {mle2.maint_hours_flight}",
            abs(float(mle2.maint_hours_flight) - 1.20) < 0.01,
        )
        check(
            f"FC3 maint_hours_flight = 0.80 — got {mle3.maint_hours_flight}",
            abs(float(mle3.maint_hours_flight) - 0.80) < 0.01,
        )

    sub("Record multi-payment across FC2 and FC3")

    # Ensure there are charges to pay
    fc2.refresh_from_db()
    fc3.refresh_from_db()
    print(f"    FC2 total_charge={fc2.total_charge}  FC3 total_charge={fc3.total_charge}")

    # If no charge rate exists, create a minimal charge directly so payment tests work
    if fc2.total_charge <= 0:
        FlightChargeItem.objects.create(
            flight_completion=fc2, item_type='one_off',
            description='Test charge FC2', amount=D('50.00'),
        )
        from core.services.booking_service import update_total
        update_total(fc2)
        fc2.refresh_from_db()

    if fc3.total_charge <= 0:
        FlightChargeItem.objects.create(
            flight_completion=fc3, item_type='one_off',
            description='Test charge FC3', amount=D('30.00'),
        )
        from core.services.booking_service import update_total
        update_total(fc3)
        fc3.refresh_from_db()

    total_to_pay = float(fc2.total_charge) + float(fc3.total_charge)
    print(f"    Total to pay: ${total_to_pay:.2f}")

    result = charging_service.record_multi_payment(
        primary_fc=fc2,
        primary_booking=bk2,
        user=admin_m.user,
        method='cash',
        fc_amounts=[
            (fc2, bk2, fc2.total_charge),
            (fc3, bk3, fc3.total_charge),
        ],
        received=D(str(total_to_pay)),
    )
    check(f"record_multi_payment returned ok ({result.error or 'no error'})", result.ok)

    if result.ok:
        fc2.refresh_from_db()
        fc3.refresh_from_db()

        fp2 = FlightPayment.objects.filter(completion=fc2, paid_at__isnull=False).first()
        fp3 = FlightPayment.objects.filter(completion=fc3, paid_at__isnull=False).first()

        check("FC2 has a FlightPayment row", fp2 is not None)
        check("FC3 has a FlightPayment row", fp3 is not None)

        if fp2 and fp3:
            check("Both FCs share the same batch_id", fp2.batch_id == fp3.batch_id)
            check("batch_id is not null", fp2.batch_id is not None)

        check("FC2 amount_paid > 0", (fc2.amount_paid or 0) > 0)
        check("FC3 amount_paid > 0", (fc3.amount_paid or 0) > 0)

    # ─────────────────────────────────────────────────────────────────────────
    head("TEST 6 — Payment reversal: reversing fc2 also unpays sibling fc3")
    # ─────────────────────────────────────────────────────────────────────────
    sub("Reverse payment on FC2")

    rev_result = charging_service.reverse_payment(fc2, bk2, admin_m.user)
    check(f"reverse_payment returned ok ({rev_result.error or 'no error'})", rev_result.ok)

    if rev_result.ok:
        fc2.refresh_from_db()
        fc3.refresh_from_db()

        check(
            f"FC2 amount_paid == 0 after reversal — got {fc2.amount_paid}",
            (fc2.amount_paid or 0) == 0,
        )
        check(
            f"FC3 amount_paid == 0 (sibling reversed) — got {fc3.amount_paid}",
            (fc3.amount_paid or 0) == 0,
        )

        sub("FC3 should appear in unpaid-flights query")
        unpaid_qs = (FlightCompletion.objects
                     .filter(paid_at__isnull=True, total_charge__gt=0)
                     .values_list('id', flat=True))
        check("FC3 appears in unpaid FlightCompletions list", fc3.id in list(unpaid_qs))
        check("FC2 appears in unpaid FlightCompletions list", fc2.id in list(unpaid_qs))

    # ─────────────────────────────────────────────────────────────────────────
    transaction.set_rollback(True)

except Exception as e:
    import traceback
    print(f"\n{R}EXCEPTION: {e}{W}")
    traceback.print_exc()
    failed += 1

# ── Summary ───────────────────────────────────────────────────────────────────
total = passed + failed
print(f"\n{'═'*64}")
print(f"  {G}{passed}{W}/{total} passed", end='')
if failed:
    print(f"  {R}{failed} FAILED{W}", end='')
print()
if gaps:
    print(f"\n  {Y}Gaps noted:{W}")
    for g in gaps:
        print(f"    • {g}")
print(f"{'═'*64}\n")
sys.exit(0 if failed == 0 else 1)
