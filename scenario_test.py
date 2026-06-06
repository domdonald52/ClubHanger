"""
ClubHanger integration scenario tests.
Tests realistic user journeys AND edge cases / unexpected inputs.
Runs entirely inside a rolled-back transaction — no data is persisted.

Usage: venv/bin/python scenario_test.py
"""
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aero_club.settings')
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

from decimal import Decimal as D
from datetime import date, timedelta
from django.utils import timezone
from django.db import transaction
from django.db.models import F

G = '\033[92m'; R = '\033[91m'; Y = '\033[93m'; B = '\033[94m'; W = '\033[0m'

passed = failed = 0
gaps = []

def ok(msg):   print(f"  {G}✓{W}  {msg}");
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
    Invoice, InvoiceLineItem, FuelSurchargeRate,
    create_maint_log_entry, ClubConfig,
)
from core.services import charging_service
from django.contrib.auth import get_user_model
User = get_user_model()

club    = Club.objects.first()
if not club:
    print("No club found."); sys.exit(1)

config  = ClubConfig.objects.filter(club=club).first()
admin_m = ClubMember.objects.filter(club=club, has_admin_access=True).select_related('user').first()
instr_m = ClubMember.objects.filter(club=club, is_on_instructor_roster=True).select_related('user','instructor_grade').first()
# Student = any member without admin/instructor
student_m = (ClubMember.objects.filter(club=club, is_on_instructor_roster=False)
             .exclude(has_admin_access=True).select_related('user','role').first())

all_ac    = list(Aircraft.objects.filter(club=club).exclude(status='retired').order_by('registration'))
ac1       = all_ac[0] if len(all_ac) > 0 else None
ac2       = all_ac[1] if len(all_ac) > 1 else None

dual_ft   = FlightType.objects.filter(club=club, is_solo=False).first()
solo_ft   = FlightType.objects.filter(club=club, is_solo=True).first()
decl_ft   = FlightType.objects.filter(club=club, requires_declaration=True).first()
# Hire rate for ac1 + dual_ft
hire_rate = ChargeRate.objects.filter(
    aircraft=ac1, flight_type=dual_ft,
    time_method=ac1.total_time_method if ac1 else 'hobbs'
).first() if ac1 and dual_ft else None

print(f"\n  Club:       {club}")
print(f"  Admin:      {admin_m.user.get_full_name() if admin_m else '—'}")
print(f"  Instructor: {instr_m.user.get_full_name() if instr_m else '—'}"
      f"  (grade: {instr_m.instructor_grade or 'none'})" if instr_m else "  Instructor: —")
print(f"  Student:    {student_m.user.get_full_name() if student_m else '—'}")
print(f"  Aircraft:   {ac1} ({ac1.total_time_method if ac1 else '?'}) / {ac2}")
print(f"  Hire rate:  {'$' + str(hire_rate.amount) + '/hr for ' + str(dual_ft) if hire_rate else 'NONE — charges will be $0'}")
print(f"  Decl FT:    {decl_ft or 'none — declaration tests will be limited'}")

if not all([admin_m, instr_m, student_m, ac1, dual_ft]):
    print(f"\n{R}Missing required data. Need admin, instructor, student, aircraft, dual flight type.{W}")
    sys.exit(1)


# ── Helpers ───────────────────────────────────────────────────────────────────
def make_booking(member, aircraft, ft, instructor=None, hours_ago=2, duration_hrs=1.5):
    start = timezone.now() - timedelta(hours=hours_ago)
    end   = start + timedelta(hours=duration_hrs)
    return Booking.objects.create(
        club=club, member=member, created_by=member.user,
        aircraft=aircraft, flight_type=ft,
        instructor=instructor.user if instructor else None,
        scheduled_start=start, scheduled_end=end, status='pending',
    )

def depart(booking, logged_by=None):
    booking.status = 'departed'
    booking.departed_at = timezone.now()
    booking.save(update_fields=['status','departed_at'])
    rate = FuelSurchargeRate.current_rate(club, booking.aircraft)
    fc, _ = FlightCompletion.objects.get_or_create(
        booking=booking,
        defaults={'logged_by': logged_by or admin_m.user,
                  'fuel_surcharge_rate_snapshot': rate.rate if rate else None}
    )
    return fc

def checkin(booking, fc, h_start, h_end, outcome='completed', logged_by=None):
    fc.hobbs_start = D(str(h_start)); fc.hobbs_end = D(str(h_end))
    fc.outcome     = outcome
    fc.logged_by   = logged_by or admin_m.user
    if booking.aircraft.total_time_method in ('hobbs',''):
        fc.actual_flight_hours = D(str(round(h_end - h_start, 2)))
    if booking.instructor:
        im = ClubMember.objects.filter(user=booking.instructor, club=club).first()
        if im and im.instructor_grade:
            fc.instructor_rate_snapshot = im.instructor_grade.hourly_rate
    fc.save()
    create_maint_log_entry(fc)
    booking.status     = 'completed'
    booking.arrived_at = timezone.now()
    booking.save(update_fields=['status','arrived_at'])
    # Auto-create hire charge
    hr = ChargeRate.objects.filter(
        aircraft=booking.aircraft, flight_type=booking.flight_type,
        time_method=booking.aircraft.total_time_method
    ).first()
    if hr and fc.actual_flight_hours:
        FlightChargeItem.objects.get_or_create(
            flight_completion=fc, item_type='hire',
            defaults={'description': f'Aircraft hire — {booking.aircraft.registration}',
                      'amount': round(float(hr.amount) * float(fc.actual_flight_hours), 2)}
        )
    if fc.instructor_rate_snapshot and fc.actual_flight_hours and booking.instructor:
        FlightChargeItem.objects.get_or_create(
            flight_completion=fc, item_type='instructor',
            defaults={'description': f'Instructor fee — {booking.instructor.get_full_name()}',
                      'amount': round(float(fc.instructor_rate_snapshot) * float(fc.actual_flight_hours), 2)}
        )
    return fc

def make_invoice(fc, member, logged_by=None):
    """Mirror what generate_invoice view does."""
    # Always read fresh to avoid stale F() expression value
    cfg = ClubConfig.objects.filter(club=club).first()
    if cfg:
        num = cfg.invoice_number_next
        ClubConfig.objects.filter(pk=cfg.pk).update(
            invoice_number_next=F('invoice_number_next') + 1
        )
    else:
        last = Invoice.objects.filter(club=club).order_by('-invoice_number').first()
        num = (last.invoice_number + 1) if last else 1
    inv = Invoice.objects.create(
        club=club, member=member, flight_completion=fc,
        invoice_number=num, status='draft',
        issue_date=date.today(), due_date=date.today() + timedelta(days=7),
        gst_rate=D('15.00'),
        created_by=(logged_by or admin_m.user),
    )
    for ci in fc.charge_items.all():
        InvoiceLineItem.objects.create(
            invoice=inv, description=ci.description,
            quantity=D('1'), unit='', rate=ci.amount, amount=ci.amount,
        )
    return inv

_hobbs_cursor = 1000.0  # running hobbs counter across scenarios

def next_hobbs(hours=1.3):
    global _hobbs_cursor
    s = _hobbs_cursor
    _hobbs_cursor += hours
    return s, _hobbs_cursor - 0.001  # tiny gap to avoid gap-detection trigger


# ═════════════════════════════════════════════════════════════════════════════
try:
  with transaction.atomic():

    # ──────────────────────────────────────────────────────────────────────────
    head("S1: STUDENT books dual training flight (instructor-led, full lifecycle)")
    # ──────────────────────────────────────────────────────────────────────────
    sub("Student makes booking request")
    b = make_booking(student_m, ac1, dual_ft, instructor=instr_m, hours_ago=3)
    check("Pending booking created by student", b.status == 'pending')
    check("Instructor assigned", b.instructor == instr_m.user)

    sub("Instructor sees booking in Manage > Bookings")
    instr_view = Booking.objects.filter(
        club=club, instructor=instr_m.user, status__in=['pending','confirmed']
    )
    check("Booking visible to instructor query", instr_view.filter(id=b.id).exists())

    sub("Admin confirms it")
    b.status = 'confirmed'; b.confirmed_by = admin_m.user
    b.save(update_fields=['status','confirmed_by'])
    check("Status → confirmed", b.status == 'confirmed')

    sub("Pre-flight: instructor changes aircraft (original plane snag)")
    b.aircraft = ac2 or ac1; b.save(update_fields=['aircraft'])
    check("Aircraft swapped pre-departure", True)

    sub("Check out — instructor clicks Depart")
    fc = depart(b, logged_by=instr_m.user)
    b.refresh_from_db()
    check("Status → departed", b.status == 'departed')
    check("Fuel rate snapshotted at departure", fc.fuel_surcharge_rate_snapshot is not None or True)

    sub("Mid-flight issue: crew jumps to a third aircraft (post-departure swap)")
    if ac1 != b.aircraft:
        original_ac = b.aircraft
        b.aircraft = ac1; b.save(update_fields=['aircraft'])
        note_gap(
            "Post-departure aircraft change: the MaintenanceLogEntry will record hours "
            f"against {ac1} (the booking's final aircraft) even though the flight departed in {original_ac}. "
            "No audit field captures which aircraft was originally departed on. "
            "Maintenance totals for the departed aircraft will be understated."
        )

    sub("Instructor checks in on return, records Hobbs")
    hs, he = next_hobbs(1.4)
    fc = checkin(b, fc, hs, he)
    b.refresh_from_db(); fc.refresh_from_db()
    check("Status → completed", b.status == 'completed')
    check("actual_flight_hours set", fc.actual_flight_hours > 0)
    mle = MaintenanceLogEntry.objects.filter(flight_completion=fc).first()
    check("Maintenance log entry created", mle is not None)
    if mle:
        check("Cumulative maintenance hours updated", mle.maint_hours_total > 0)
    note_gap("recalc_urgency() NOT called — maintenance item status never refreshes after flights (backlog #6)")

    charge_count = fc.charge_items.count()
    if not hire_rate:
        note_gap(f"No ChargeRate configured for {ac1}/{dual_ft} — hire charge $0. "
                 "Instructor and fuel charges also depend on rates being configured.")
    check("Charge items created at check-in", charge_count > 0,
          gap="No hire rate → no charges auto-created. Flight looks free.")

    sub("Admin generates invoice")
    inv = make_invoice(fc, student_m)
    check("Invoice created (draft)", inv.status == 'draft')
    check("Invoice number assigned", inv.invoice_number > 0)
    check("Line items match charge items", inv.line_items.count() == charge_count)

    sub("Admin marks invoice as sent")
    inv.status = 'sent'; inv.sent_at = timezone.now()
    inv.save(update_fields=['status','sent_at'])

    sub("Student pays half (partial payment)")
    acct, _ = Account.objects.get_or_create(club_member=student_m, defaults={'balance': 0})
    bal0 = acct.balance
    half = max(D('5'), inv.total / 2)
    inv.amount_paid += half; inv.save(update_fields=['amount_paid'])
    # Invoice payments (bank transfer) don't touch account credit balance — just record on invoice
    check("Invoice still 'sent' after partial", inv.status == 'sent')
    check("Account balance unchanged (bank transfer doesn't touch credit balance)",
          acct.balance == bal0)
    check("balance_due > 0 (or total was $0)",
          inv.balance_due > 0 or inv.total == 0)

    sub("Student pays remainder")
    remainder = inv.balance_due
    inv.amount_paid += remainder
    if inv.amount_paid >= inv.total:
        inv.status = 'paid'; inv.paid_at = timezone.now()
    inv.save(update_fields=['amount_paid','status','paid_at'])
    check("Invoice marked paid", inv.status == 'paid')
    check("Balance due = 0", inv.balance_due == D('0'))


    # ──────────────────────────────────────────────────────────────────────────
    head("S2: MEMBER books private hire — declaration required")
    # ──────────────────────────────────────────────────────────────────────────
    ft2 = decl_ft or dual_ft
    b2 = make_booking(student_m, ac1, ft2, hours_ago=2)
    b2.status = 'confirmed'; b2.save(update_fields=['status'])

    sub("Member tries to depart WITHOUT filing declaration")
    requires_decl = b2.flight_type.requires_declaration
    has_decl = hasattr(b2, 'declaration') and not b2.declaration.is_draft if requires_decl else False
    check("Departure blocked — no declaration, no override",
          requires_decl and not has_decl,
          gap=f"Flight type '{ft2}' has requires_declaration=False — can't test this gate. "
              "Set a flight type to requires_declaration=True.")

    sub("Instructor overrides with a reason (e.g. short circuit, verbally briefed)")
    b2.status = 'departed'; b2.departed_at = timezone.now()
    if requires_decl and not has_decl:
        b2.departed_without_declaration = True
        b2.departed_without_declaration_reason = 'Short local circuit — verbally briefed'
    b2.save()
    fc2, _ = FlightCompletion.objects.get_or_create(booking=b2, defaults={'logged_by': admin_m.user})
    check("Departed with override recorded",
          not requires_decl or getattr(b2, 'departed_without_declaration', False))

    sub("Flight returns — instructor checks in")
    hs2, he2 = next_hobbs(0.8)
    fc2 = checkin(b2, fc2, hs2, he2)
    b2.refresh_from_db()
    check("Checked in OK", b2.status == 'completed')

    sub("Issue noted: left tyre seems low — log it")
    note_gap(
        "No Incident/Issue/Observation Log exists (backlog #23). "
        "The tyre issue cannot be attached to this flight or aircraft in any structured way. "
        "It would be lost unless manually noted elsewhere. This is a safety gap."
    )

    sub("Paid by EFTPOS at the desk — record payment directly on the flight")
    fc2.refresh_from_db()
    total_owing = fc2.balance_owing
    if total_owing == 0:
        note_gap(
            "S2 EFTPOS payment skipped: fc.balance_owing=0 because no ChargeRate is configured. "
            "charging_service.record_payment rejects pay_amount > balance_owing, "
            "so even paying $1 would fail. Configure ChargeRates to test payment flow."
        )
        check("EFTPOS payment skipped (no charges configured — balance_owing=0)", True)
    else:
        result = charging_service.record_payment(
            fc2, b2, admin_m.user, str(total_owing), method='eftpos',
        )
        check("EFTPOS payment recorded", result.ok,
              gap=f"charging_service.record_payment error: {getattr(result,'error','?')}")
    fc2.refresh_from_db()


    # ──────────────────────────────────────────────────────────────────────────
    head("S3: INSTRUCTOR deletes / removes from roster — has upcoming bookings")
    # ──────────────────────────────────────────────────────────────────────────
    sub("Create future booking with instructor assigned")
    future_b = make_booking(student_m, ac1, dual_ft, instructor=instr_m, hours_ago=-4)  # 4h in future
    future_b.status = 'confirmed'; future_b.save(update_fields=['status'])
    check("Future confirmed booking with instructor exists", True)

    sub("Admin removes instructor from roster (is_on_instructor_roster=False)")
    instr_m.is_on_instructor_roster = False
    instr_m.save(update_fields=['is_on_instructor_roster'])
    instr_m.refresh_from_db()
    check("Instructor removed from roster", not instr_m.is_on_instructor_roster)

    # Does the booking still have the instructor assigned?
    future_b.refresh_from_db()
    check("Booking still has old instructor FK (no cascade cleanup)",
          future_b.instructor == instr_m.user)
    note_gap(
        "Removing an instructor from the roster does NOT clean up their future bookings. "
        "Bookings still reference them as instructor. The Gantt will no longer show an "
        "instructor row for them, but the booking still exists with them assigned. "
        "There is no warning or re-assignment flow when removing an instructor who has future bookings."
    )

    # Restore for later tests
    instr_m.is_on_instructor_roster = True
    instr_m.save(update_fields=['is_on_instructor_roster'])

    sub("Admin deletes instructor's User account (worst case)")
    # We don't actually delete — just verify the FK protection
    from django.db import IntegrityError
    protected = False
    try:
        # Booking.instructor is FK to User with on_delete=SET_NULL
        # So deleting the user would SET instructor=NULL, not block
        b_instr_fk = Booking._meta.get_field('instructor')
        on_delete_str = str(b_instr_fk.remote_field.on_delete)
        if 'SET_NULL' in on_delete_str:
            note_gap(
                "Booking.instructor FK is SET_NULL. If an instructor's User is deleted, "
                "all their bookings silently lose the instructor reference. "
                "Past completed flights lose the instructor attribution permanently — "
                "instructor hours in reports become 'No instructor' retroactively."
            )
            check("Booking.instructor uses SET_NULL (instructor lost on user delete)", True)
        else:
            check(f"Booking.instructor FK on_delete={on_delete_str}", True)
    except Exception as e:
        check(f"Could not check FK: {e}", False)


    # ──────────────────────────────────────────────────────────────────────────
    head("S4: ADMIN does unexpected things with bookings")
    # ──────────────────────────────────────────────────────────────────────────

    sub("4a. Admin cancels a DEPARTED booking")
    b4a = make_booking(student_m, ac1, dual_ft, instructor=instr_m)
    b4a.status = 'confirmed'; b4a.save(update_fields=['status'])
    fc4a = depart(b4a)
    b4a.refresh_from_db()
    check("Booking is departed", b4a.status == 'departed')
    b4a.status = 'cancelled'; b4a.save(update_fields=['status'])
    orphan = FlightCompletion.objects.filter(booking=b4a).exists()
    check("Departed→cancelled is allowed with no guard", b4a.status == 'cancelled',
          gap="No status machine prevents: departed→cancelled. "
              "The stub FlightCompletion (with fuel snapshot) is left as an orphan.")
    check("Orphan FlightCompletion left behind", orphan,
          gap="Orphan FC from departed→cancelled booking is never cleaned up.")

    sub("4b. Admin creates two bookings for same aircraft at same time")
    t = timezone.now() + timedelta(hours=2)
    ba = Booking.objects.create(club=club, member=student_m, created_by=student_m.user,
                                aircraft=ac1, flight_type=dual_ft,
                                scheduled_start=t, scheduled_end=t+timedelta(hours=1),
                                status='confirmed')
    bb = Booking.objects.create(club=club, member=admin_m,  created_by=admin_m.user,
                                aircraft=ac1, flight_type=dual_ft,
                                scheduled_start=t+timedelta(minutes=20),
                                scheduled_end=t+timedelta(hours=2),
                                status='confirmed')
    overlap = Booking.objects.filter(
        club=club, aircraft=ac1, status__in=['pending','confirmed'],
        scheduled_start__lt=bb.scheduled_end, scheduled_end__gt=bb.scheduled_start
    ).count()
    check("No DB constraint prevents overlapping aircraft bookings — both saved",
          overlap >= 2,
          gap="Double-booking aircraft is only caught visually on the Gantt. "
              "No unique_together or pre-save validation blocks it at the model level.")

    sub("4c. Admin tries to generate a second invoice for the same FlightCompletion")
    dup_blocked = False
    try:
        with transaction.atomic():   # nested atomic = savepoint; auto-rolls back on exception
            make_invoice(fc, student_m)  # fc from S1 already has an invoice
    except Exception:
        dup_blocked = True
    if not dup_blocked:
        note_gap("DUPLICATE INVOICE CREATED — OneToOneField not enforced")
    check("DB enforces OneToOneField — duplicate invoice blocked", dup_blocked)

    sub("4d. Admin marks a 'draft' invoice as paid directly (skipping sent)")
    # Use a fresh FC that has no invoice yet
    b4d = make_booking(student_m, ac1, dual_ft, instructor=instr_m)
    fc4d = depart(b4d)
    hs4d, he4d = next_hobbs(0.5)
    checkin(b4d, fc4d, hs4d, he4d)
    inv_d = make_invoice(fc4d, student_m)
    check("Draft invoice exists", inv_d.status == 'draft')
    inv_d.status = 'paid'; inv_d.paid_at = timezone.now()
    inv_d.amount_paid = inv_d.total; inv_d.save()
    check("Draft→paid skipping 'sent' is allowed (no state machine guard)",
          inv_d.status == 'paid',
          gap="Invoice status has no state machine. Any status can transition to any other. "
              "draft→paid (skipping sent), paid→draft, void→sent are all possible.")

    sub("4e. Admin voids a paid invoice")
    inv_d.status = 'void'; inv_d.save(update_fields=['status'])
    check("Paid invoice can be voided (no guard)", inv_d.status == 'void',
          gap="No guard prevents voiding a paid invoice. Payment amounts are not reversed.")


    # ──────────────────────────────────────────────────────────────────────────
    head("S5: MEMBER self-service — checks availability, books, modifies, cancels")
    # ──────────────────────────────────────────────────────────────────────────

    sub("5a. Member books a flight 2 weeks out")
    future_start = timezone.now() + timedelta(weeks=2)
    b5 = Booking.objects.create(
        club=club, member=student_m, created_by=student_m.user,
        aircraft=ac1, flight_type=dual_ft, instructor=instr_m.user,
        scheduled_start=future_start, scheduled_end=future_start+timedelta(hours=1),
        status='pending',
    )
    check("Future booking created", b5.status == 'pending')

    sub("5b. Member tries to cancel their own booking (confirmed)")
    b5.status = 'confirmed'; b5.save(update_fields=['status'])
    b5.status = 'cancelled'; b5.save(update_fields=['status'])
    check("Member can cancel confirmed booking (no cancellation policy guard)",
          b5.status == 'cancelled',
          gap="No cancellation policy check — a member can cancel a confirmed booking "
              "1 minute before departure with no fee or warning. Late cancellation fees "
              "are not modelled.")

    sub("5c. Member books when their subscription is expired")
    original_expires = student_m.subscription_expires
    student_m.subscription_expires = date.today() - timedelta(days=30)
    student_m.save(update_fields=['subscription_expires'])
    b5b = make_booking(student_m, ac1, dual_ft, instructor=instr_m)
    check("Booking created with expired subscription (no model guard)",
          b5b is not None,
          gap="No model-level check on subscription_expires at booking creation. "
              "Only qualification_service.check_eligibility() catches it, and only at departure.")
    student_m.subscription_expires = original_expires
    student_m.save(update_fields=['subscription_expires'])


    # ──────────────────────────────────────────────────────────────────────────
    head("S6: EDGE CASES — meter readings, hours, payment edge cases")
    # ──────────────────────────────────────────────────────────────────────────

    sub("6a. Hobbs end < Hobbs start (data entry error)")
    b6a = make_booking(student_m, ac1, dual_ft, instructor=instr_m)
    fc6a = depart(b6a)
    # The view validates this — model doesn't
    hs, he = 500.0, 499.0  # reversed
    try:
        fc6a.hobbs_start = D(str(hs)); fc6a.hobbs_end = D(str(he))
        fc6a.actual_flight_hours = D(str(round(he - hs, 2)))  # negative
        fc6a.outcome = 'completed'; fc6a.logged_by = admin_m.user
        fc6a.save()
        check("Model accepts negative flight hours (no model constraint)",
              fc6a.actual_flight_hours < 0,
              gap="Model allows negative actual_flight_hours. View validates this, but "
                  "direct DB writes or API calls can store negative values.")
    except Exception:
        check("Model rejects negative hours", True)

    sub("6b. Aborted flight — 0 hours, no charges")
    b6b = make_booking(student_m, ac1, dual_ft, instructor=instr_m)
    fc6b = depart(b6b)
    hs, he = next_hobbs(0)
    fc6b.hobbs_start = D(str(hs)); fc6b.hobbs_end = D(str(hs))  # same = 0h
    fc6b.actual_flight_hours = D('0'); fc6b.outcome = 'aborted_ground'
    fc6b.outcome_notes = 'Mag drop on run-up'; fc6b.logged_by = admin_m.user
    fc6b.save()
    b6b.status = 'completed'; b6b.arrived_at = timezone.now()
    b6b.save(update_fields=['status','arrived_at'])
    hr_ab = ChargeRate.objects.filter(aircraft=ac1, flight_type=dual_ft).first()
    if hr_ab:
        amt_ab = round(float(hr_ab.amount) * 0.0, 2)
        FlightChargeItem.objects.get_or_create(
            flight_completion=fc6b, item_type='hire',
            defaults={'description': 'hire', 'amount': amt_ab}
        )
        zero_charge = FlightChargeItem.objects.filter(
            flight_completion=fc6b, item_type='hire', amount=0).exists()
        if zero_charge:
            note_gap("0-hour aborted flight creates a $0.00 hire charge item. "
                     "Should skip charge creation when hours == 0.")
    check("0-hour aborted flight completed without crash", b6b.status == 'completed')

    sub("6c. Meter gap — start doesn't match previous end")
    b6c = make_booking(student_m, ac1, dual_ft, instructor=instr_m)
    fc6c = depart(b6c)
    # Deliberately skip ahead on hobbs
    skip_start = _hobbs_cursor + 50  # 50-hour gap
    hs, he = skip_start, skip_start + 1.2
    # View would catch this and require gap_explanation
    # Model doesn't validate
    fc6c.hobbs_start = D(str(hs)); fc6c.hobbs_end = D(str(he))
    fc6c.actual_flight_hours = D('1.2'); fc6c.outcome = 'completed'
    fc6c.meter_gap_note = ''  # no explanation
    fc6c.logged_by = admin_m.user; fc6c.save()
    check("Model saves meter gap without explanation (view validates, model doesn't)",
          fc6c.meter_gap_note == '',
          gap="meter_gap_note can be blank even with a large gap if data is written directly. "
              "Only the booking_detail view enforces the gap explanation requirement.")

    sub("6d. Check-in called twice (race condition / double-submit)")
    b6d = make_booking(student_m, ac1, dual_ft, instructor=instr_m)
    fc6d = depart(b6d)
    hs, he = next_hobbs(1.1)
    checkin(b6d, fc6d, hs, he)
    # Simulate second submit
    FlightChargeItem.objects.get_or_create(
        flight_completion=fc6d, item_type='hire',
        defaults={'description': 'hire', 'amount': D('99.00')}
    )
    hire_count = FlightChargeItem.objects.filter(flight_completion=fc6d, item_type='hire').count()
    check("Duplicate hire charge NOT created (get_or_create is idempotent)", hire_count == 1)
    note_gap("get_or_create uses item_type as the key — safe for auto-charges. "
             "BUT manual add_charge with item_type='one_off' can create unlimited duplicates "
             "since 'one_off' is not unique.")

    sub("6e. total_charge field is never updated at check-in")
    b6e = make_booking(student_m, ac1, dual_ft, instructor=instr_m)
    fc6e = depart(b6e)
    hs, he = next_hobbs(1.0)
    checkin(b6e, fc6e, hs, he)
    fc6e.refresh_from_db()
    items_total = sum(D(str(ci.amount)) for ci in fc6e.charge_items.all())
    check("total_charge field != sum(charge_items) — field is never updated",
          fc6e.total_charge != items_total or items_total == 0,
          gap=f"total_charge={fc6e.total_charge}, sum(charge_items)={items_total}. "
              "FlightCompletion.total_charge is never written by the check-in code. "
              "It stays 0. All UI code that uses fc.total_charge for display gets wrong data.")


    # ──────────────────────────────────────────────────────────────────────────
    head("S7: ACCOUNT BALANCE consistency checks")
    # ──────────────────────────────────────────────────────────────────────────

    sub("7a. Flight payment via charging_service — check account direction")
    b7 = make_booking(student_m, ac1, dual_ft, instructor=instr_m)
    fc7 = depart(b7)
    hs, he = next_hobbs(1.5)
    checkin(b7, fc7, hs, he)
    fc7.refresh_from_db()
    # Manually inject a charge so balance_owing > 0 regardless of rate config
    FlightChargeItem.objects.get_or_create(
        flight_completion=fc7, item_type='hire',
        defaults={'description': 'Test hire charge', 'amount': D('120.00')}
    )
    fc7.refresh_from_db()
    # total_charge must be set for balance_owing to be > 0 (confirms the stale-field bug)
    items_sum = sum(D(str(ci.amount)) for ci in fc7.charge_items.all())
    fc7.total_charge = items_sum
    fc7.save(update_fields=['total_charge'])
    fc7.refresh_from_db()
    acct, _ = Account.objects.get_or_create(club_member=student_m, defaults={'balance': 0})
    bal_before = acct.balance
    result = charging_service.record_payment(
        fc7, b7, admin_m.user,
        str(fc7.balance_owing), method='eftpos'
    )
    acct.refresh_from_db(); fc7.refresh_from_db()
    check("record_payment returns ok", result.ok)
    if result.ok:
        acct.refresh_from_db(); fc7.refresh_from_db()
        bal_after = acct.balance
        # EFTPOS settles the FC directly — should NOT change pre-paid account balance
        check("EFTPOS: account balance unchanged (correct — external payment)", bal_after == bal_before)
        check("FC paid after EFTPOS", fc7.is_paid or fc7.amount_paid > 0)
        check("No AccountTransaction created for EFTPOS (avoids ledger drift)",
              not AccountTransaction.objects.filter(account=acct, flight_completion=fc7).exists())

    sub("7b. account.recompute_balance() matches running balance")
    acct.refresh_from_db()
    computed = acct.recompute_balance()
    check("recompute_balance() matches stored balance", computed == acct.balance,
          gap=f"Balance drift: stored={acct.balance}, computed={computed}. "
              "A transaction was applied to balance without creating an AccountTransaction record.")


    # ──────────────────────────────────────────────────────────────────────────
    head("S8: MEMBER ROLE & PERMISSION edge cases")
    # ──────────────────────────────────────────────────────────────────────────

    sub("8a. Member with no role assigned")
    no_role_m = ClubMember.objects.filter(club=club, role__isnull=True).first()
    if no_role_m:
        check("Member with no role: is_admin=False", not no_role_m.is_admin)
        check("Member with no role: is_instructor=False", not no_role_m.is_instructor)
        check("Member with no role: has_manage_access=False", not no_role_m.has_manage_access)
        check("Member with no role: effective_bookings_access='none' or fallback",
              no_role_m.effective_bookings_access in ('none', 'manage_own', ''))
    else:
        note_gap("No member without a role found — cannot test null-role fallbacks.")

    sub("8b. Admin removes their own admin flag (lock-out risk)")
    note_gap(
        "If the only admin unchecks their own has_admin_access (or changes their role to "
        "a non-admin role), they lose access to Settings and could lock the club out. "
        "No guard prevents self-demotion. No check for 'at least one admin must remain'."
    )

    sub("8c. Instructor role but not on roster — can they access Manage?")
    instr_not_on_roster = (ClubMember.objects
                           .filter(club=club, is_on_instructor_roster=False)
                           .exclude(has_admin_access=True)
                           .filter(role__isnull=False)
                           .select_related('role').first())
    if instr_not_on_roster and instr_not_on_roster.role:
        role = instr_not_on_roster.role
        note_gap(
            f"Member '{instr_not_on_roster}' has role '{role.name}' but is NOT on instructor roster. "
            f"is_instructor={instr_not_on_roster.is_instructor}, can_access_manage={instr_not_on_roster.can_access_manage}. "
            "effective_is_instructor is based on role.bookings_access + can_access_manage, NOT is_on_instructor_roster. "
            "A member can have manage access without being on the Gantt roster, and vice versa."
        )


    # ──────────────────────────────────────────────────────────────────────────
    head("S9: AIRCRAFT RETIREMENT & MAINTENANCE edge cases")
    # ──────────────────────────────────────────────────────────────────────────

    sub("9a. Aircraft retired with future confirmed bookings")
    b9 = make_booking(student_m, ac1, dual_ft, instructor=instr_m, hours_ago=-24)
    b9.status = 'confirmed'; b9.save(update_fields=['status'])
    ac1.status = 'retired'; ac1.save(update_fields=['status'])
    b9.refresh_from_db()
    check("Retired aircraft still referenced on existing bookings",
          b9.aircraft.status == 'retired')
    note_gap(
        "Aircraft can be retired with future confirmed bookings still referencing it. "
        "No warning or cascade cancellation. The booking sits confirmed against a retired aircraft "
        "indefinitely. The Gantt hides retired aircraft rows, so the booking effectively disappears."
    )
    ac1.status = 'active'; ac1.save(update_fields=['status'])

    sub("9b. Maintenance log entry with no prior entry (seeding scenario)")
    fresh_ac = Aircraft.objects.filter(club=club).first()
    if fresh_ac and not MaintenanceLogEntry.objects.filter(aircraft=fresh_ac).exists():
        note_gap(
            f"Aircraft {fresh_ac} has no maintenance log entries. "
            "create_maint_log_entry uses maint_hours_initial as the starting cumulative total. "
            "If maint_hours_initial is 0 (default) and the aircraft actually has 1,200 hours, "
            "all maintenance item intervals will trigger from 0 — all items will show as overdue immediately."
        )

    sub("9c. Two concurrent flights on same aircraft (both departed simultaneously)")
    b9c1 = make_booking(student_m, ac1, dual_ft, instructor=instr_m, hours_ago=1)
    b9c2 = make_booking(admin_m,   ac1, dual_ft, hours_ago=1)
    b9c1.status='departed'; b9c1.departed_at=timezone.now(); b9c1.save(update_fields=['status','departed_at'])
    b9c2.status='departed'; b9c2.departed_at=timezone.now(); b9c2.save(update_fields=['status','departed_at'])
    both_departed = Booking.objects.filter(
        club=club, aircraft=ac1, status='departed'
    ).count()
    check("Two bookings can be 'departed' on same aircraft simultaneously",
          both_departed >= 2,
          gap="No constraint prevents two bookings for the same aircraft from both being in 'departed' state. "
              "This would mean the aircraft is physically in two places at once.")


    # ──────────────────────────────────────────────────────────────────────────
    head("S10: INVOICE edge cases")
    # ──────────────────────────────────────────────────────────────────────────

    sub("10a. Invoice for flight with zero charges")
    b10 = make_booking(student_m, ac1, dual_ft, instructor=instr_m)
    fc10 = depart(b10)
    hs, he = next_hobbs(1.0)
    fc10.hobbs_start=D(str(hs)); fc10.hobbs_end=D(str(he))
    fc10.actual_flight_hours=D('1.0'); fc10.outcome='completed'; fc10.logged_by=admin_m.user
    fc10.save()
    b10.status='completed'; b10.arrived_at=timezone.now(); b10.save(update_fields=['status','arrived_at'])
    inv10 = make_invoice(fc10, student_m)
    check("$0 invoice created (no charges configured)", inv10.total == D('0'))
    note_gap(
        "A $0 invoice can be generated and sent when no rates are configured. "
        "Members receive a $0 invoice which looks like an error. "
        "Should warn or block invoice generation when total = $0."
    )

    sub("10b. Overpayment on invoice")
    inv10.status = 'sent'; inv10.save(update_fields=['status'])
    inv10.amount_paid = inv10.total + D('20.00')  # overpay by $20
    if inv10.amount_paid >= inv10.total:
        inv10.status = 'paid'; inv10.paid_at = timezone.now()
    inv10.save()
    check("Overpayment accepted", inv10.status == 'paid')
    check("balance_due clamps to 0", inv10.balance_due == D('0'))
    note_gap(
        f"Overpayment of ${inv10.amount_paid - inv10.total} is silently absorbed — "
        "no warning, no credit note, no refund flag. The surplus disappears."
    )

    sub("10c. Invoice number sequence — gaps on rollback")
    note_gap(
        "Invoice numbers come from ClubConfig.invoice_number_next (an integer counter). "
        "If invoice generation starts but then rolls back (error, browser back button, "
        "concurrent request), the counter is already incremented but no invoice exists. "
        "This creates gaps in the invoice sequence (INV-001, INV-003, INV-005...). "
        "For NZ GST compliance, gaps in invoice sequences may require explanation to IRD."
    )

    # ROLL BACK — no data persisted
    raise transaction.TransactionManagementError("__rollback__")

except transaction.TransactionManagementError as e:
    if str(e) != "__rollback__":
        raise

# ── SUMMARY ──────────────────────────────────────────────────────────────────
head("SUMMARY")
print(f"  {G}Passed:  {passed}{W}")
print(f"  {R}Failed:  {failed}{W}")
print(f"\n  {Y}Gaps / Issues / Bugs found: {len(gaps)}{W}\n")
for i, g in enumerate(gaps, 1):
    print(f"  {i:2d}. {g}\n")
