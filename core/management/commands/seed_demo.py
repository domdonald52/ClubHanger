"""
seed_demo — populate a fresh database with a realistic Wellington Aero Club demo.

Creates:
  • Club + config + charge rates
  • Roles, membership categories, flight types
  • 6 aircraft (4 × 2-seat, 2 × 4-seat)
  • 4 instructors + 18 members in various states
  • ~1,100 bookings: near-fully-booked for next 3 weeks, sporadic after
  • FlightCompletion + FlightChargeItem records for all past completed flights
  • Account top-ups, flight charge debits, partial payments
  • Outstanding and overdue invoices for invoice-paying members
  • Block-outs (maintenance, lunch breaks, events)

Usage:
  python manage.py seed_demo           # idempotent, skips if data exists
  python manage.py seed_demo --reset   # wipe and regenerate everything
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import random

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from core.models import (
    Club, ClubConfig, Role, MembershipCategory, ClubMember, Account,
    AccountTransaction, Aircraft, AircraftType, ChargeRate, FlightType,
    Booking, BookingStatus, FlightCompletion, FlightChargeItem, FlightPayment,
    Invoice, InvoiceLineItem, BlockOutType, BlockOut,
)

User = get_user_model()
NZ = ZoneInfo('Pacific/Auckland')

# ── Club ─────────────────────────────────────────────────────────────────────

CLUB = {
    "name": "Wellington Aero Club",
    "slug": "wellington-aero-club",
    "phone": "04 388 8000",
    "email": "office@wellingtonaero.example",
    "timezone": "Pacific/Auckland",
    "currency": "NZD",
}

# ── Aircraft fleet ────────────────────────────────────────────────────────────

AIRCRAFT_FLEET = [
    dict(registration="ZK-WAC", type_name="PA38 Tomahawk", seats=2,
         total_time_method="tacho", records_tacho=True,  records_hobbs=False,
         fuel_consumption_per_hour="22.0", hobbs_initial="4210.3"),
    dict(registration="ZK-TAW", type_name="Cessna 152",    seats=2,
         total_time_method="hobbs", records_tacho=False, records_hobbs=True,
         fuel_consumption_per_hour="19.0", hobbs_initial="6831.7"),
    dict(registration="ZK-BCX", type_name="Cessna 172S",   seats=4,
         total_time_method="hobbs", records_tacho=False, records_hobbs=True,
         fuel_consumption_per_hour="34.0", hobbs_initial="2941.6"),
]

# Hire rate per aircraft (NZD/hr, applied to actual flight hours)
HIRE_RATE = {
    "ZK-WAC": Decimal("190"),
    "ZK-TAW": Decimal("185"),
    "ZK-BCX": Decimal("290"),
}
INSTRUCTOR_RATE = Decimal("85")

# ── Membership categories ─────────────────────────────────────────────────────

MEMBER_CATEGORIES = [
    ("Instructor",               True),
    ("Full Member",              True),
    ("Commercial Pilot",         True),
    ("Student Pilot",            True),
    ("Life Member (Flying)",     True),
    ("Life Member (Non-Flying)", True),
    ("Gateway Project",          False),
    ("Young Eagles",             False),
    ("Trial Flight",             False),
]

ROLES = ["Admin", "Instructor", "Member"]

FLIGHT_TYPES = [
    {"name": "Student Dual",  "code": "DUAL",  "is_training": True},
    {"name": "Solo Hire",     "code": "SOLO",  "is_training": False},
    {"name": "Trial Flight",  "code": "TRIAL", "is_training": False},
    {"name": "Cross-Country", "code": "XC",    "is_training": False},
]

# ── People ────────────────────────────────────────────────────────────────────
# (username, first, last, role, category, standing, sub_expires, resigned_at,
#  pay_method, top_up_amount)
# pay_method: 'credit' | 'invoice'

PEOPLE = [
    # Admins
    ("dominic","Dominic","Hales",     "Admin",      "Full Member",         "active", "2027-03-31",None, "credit",  Decimal("0")),
    ("alex",   "Alex",   "Reed",      "Admin",      "Commercial Pilot",    "active", "2027-03-31",None, "credit",  Decimal("0")),
    # Instructors (always exempt from credit limit)
    ("sean",   "Sean",   "Kemp",      "Instructor", "Instructor",          "active", "2027-03-31",None, "credit",  Decimal("0")),
    ("jane",   "Jane",   "Park",      "Instructor", "Instructor",          "active", "2026-12-31",None, "credit",  Decimal("0")),
    ("mark",   "Mark",   "Thomson",   "Instructor", "Instructor",          "active", "2027-03-31",None, "credit",  Decimal("0")),
    ("kate",   "Kate",   "Wilson",    "Instructor", "Instructor",          "active", "2027-03-31",None, "credit",  Decimal("0")),
    # Active credit-paying members
    ("mike",   "Mike",   "Lowe",      "Member",     "Student Pilot",       "active", "2027-03-31",None, "credit",  Decimal("1200")),
    ("rita",   "Rita",   "Singh",     "Member",     "Full Member",         "active", "2027-03-31",None, "credit",  Decimal("2500")),
    ("emma",   "Emma",   "Bradley",   "Member",     "Student Pilot",       "active", "2027-03-31",None, "credit",  Decimal("800")),
    ("sophie", "Sophie", "Nguyen",    "Member",     "Student Pilot",       "active", "2027-03-31",None, "credit",  Decimal("600")),
    ("raj",    "Raj",    "Patel",     "Member",     "Student Pilot",       "active", "2027-03-31",None, "credit",  Decimal("1500")),
    ("anna",   "Anna",   "Fischer",   "Member",     "Student Pilot",       "active", "2027-03-31",None, "credit",  Decimal("900")),
    ("james",  "James",  "Tahi",      "Member",     "Student Pilot",       "active", "2027-03-31",None, "credit",  Decimal("700")),
    ("aroha",  "Aroha",  "Williams",  "Member",     "Full Member",         "active", "2027-03-31",None, "credit",  Decimal("1800")),
    ("lisa",   "Lisa",   "Chen",      "Member",     "Student Pilot",       "active", "2027-03-31",None, "credit",  Decimal("500")),
    ("paulo",  "Paulo",  "Ferreira",  "Member",     "Full Member",         "active", "2027-03-31",None, "credit",  Decimal("2000")),
    ("tom",    "Tom",    "Hargreaves","Member",     "Student Pilot",       "active", "2027-03-31",None, "credit",  Decimal("1100")),
    # Invoice-paying members (accounts settle by invoice, not pre-paid credit)
    ("hamish", "Hamish", "McKenzie",  "Member",     "Commercial Pilot",    "active", "2027-03-31",None, "invoice", Decimal("0")),
    ("chris",  "Chris",  "Park",      "Member",     "Full Member",         "active", "2027-03-31",None, "invoice", Decimal("0")),
    ("david",  "David",  "Morrison",  "Member",     "Full Member",         "active", "2027-03-31",None, "invoice", Decimal("0")),
    ("ben",    "Ben",    "Walker",    "Member",     "Full Member",         "active", "2027-03-31",None, "invoice", Decimal("0")),
    # Edge-case members (for UI demos)
    ("sarah",  "Sarah",  "Williams",  "Member",     "Commercial Pilot",    "active", "2026-07-05",None, "credit",  Decimal("400")),
    ("grace",  "Grace",  "Okafor",    "Member",     "Student Pilot",       "pending",None,         None, "credit",  Decimal("0")),
    ("bob",    "Bob",    "Morris",    "Member",     "Life Member (Flying)","resigned","2025-12-31","2025-12-15","credit",Decimal("0")),
]

DEFAULT_PASSWORD = "clubhangar2026"
INVOICE_PAYERS = {"hamish", "chris", "david", "ben"}

# ── Booking generation ────────────────────────────────────────────────────────

SLOTS = [(8,0),(9,30),(11,0),(12,30),(14,0),(15,30)]
SLOT_MINS = 90

FILL = {
    (True,  True,  "busy"):   0.90,
    (True,  False, "busy"):   0.78,
    (False, True,  "busy"):   0.68,
    (False, False, "busy"):   0.58,
    (True,  True,  "past"):   0.88,
    (True,  False, "past"):   0.75,
    (False, True,  "past"):   0.65,
    (False, False, "past"):   0.55,
    (True,  True,  "sparse"): 0.28,
    (True,  False, "sparse"): 0.20,
    (False, True,  "sparse"): 0.18,
    (False, False, "sparse"): 0.12,
}

# ── Block-out config ──────────────────────────────────────────────────────────

BLOCKOUT_TYPES = [
    dict(name="100-hour Check",       target="aircraft",   is_hard=True,  color="#f59e0b"),
    dict(name="Annual Inspection",    target="aircraft",   is_hard=True,  color="#ef4444"),
    dict(name="Instructor Leave",     target="instructor", is_hard=True,  color="#8b5cf6"),
    dict(name="Lunch Break",          target="instructor", is_hard=False, color="#6b7280"),
    dict(name="Club Event / Air Day", target="all",        is_hard=True,  color="#3b82f6"),
    dict(name="Maintenance Morning",  target="aircraft",   is_hard=True,  color="#f97316"),
]


class Command(BaseCommand):
    help = "Seed a full demo dataset for Wellington Aero Club."

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='Delete existing data and regenerate')

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(42)
        reset = options['reset']

        club = self._setup_club()

        if reset:
            self.stdout.write("  Wiping existing demo data...")
            BlockOut.objects.filter(club=club).delete()
            BlockOutType.objects.filter(club=club).delete()
            FlightCompletion.objects.filter(booking__club=club).delete()
            Invoice.objects.filter(club=club).delete()
            Booking.objects.filter(club=club).delete()
            ChargeRate.objects.filter(aircraft__club=club).delete()
            Aircraft.objects.filter(club=club).delete()
            AircraftType.objects.filter(club=club).delete()
            ClubMember.objects.filter(club=club).delete()
            User.objects.filter(username__in=[p[0] for p in PEOPLE]).delete()

        roles, cats = self._setup_taxonomy(club)
        aircraft    = self._setup_fleet(club)
        ft          = self._setup_flight_types(club)
        members     = self._setup_people(club, roles, cats)
        admin_user  = User.objects.filter(is_superuser=True).first()

        self._setup_charge_rates(club, aircraft, ft)

        # ── Bookings ──────────────────────────────────────────────────────────
        if Booking.objects.filter(club=club).exists():
            self.stdout.write("  Bookings exist — skipping (use --reset to regenerate)")
        else:
            self._generate_bookings(club, aircraft, members, ft, admin_user)

        # ── Analytics (completions, accounts, invoices) ────────────────────────
        if FlightCompletion.objects.filter(booking__club=club).exists():
            self.stdout.write("  Completions exist — skipping analytics")
        else:
            self._setup_accounts(club, members, admin_user)
            self._setup_completions(club, aircraft, members, admin_user)
            self._setup_invoices(club, members, admin_user)

        # ── Block-outs ─────────────────────────────────────────────────────────
        if BlockOut.objects.filter(club=club).exists():
            self.stdout.write("  Block-outs exist — skipping")
        else:
            self._setup_blockouts(club, aircraft, members, admin_user)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Login: dominic / {DEFAULT_PASSWORD}\n"
            f"Management app: /manage/{club.slug}/\n"
            f"Mobile app:     /app/{club.slug}/"
        ))

    # ── Setup helpers ─────────────────────────────────────────────────────────

    def _setup_club(self):
        club, created = Club.objects.get_or_create(slug=CLUB["slug"], defaults=CLUB)
        self.stdout.write(f"Club: {club.name} ({'created' if created else 'exists'})")
        ClubConfig.objects.get_or_create(
            club=club,
            defaults={
                "default_booking_duration": 90,
                "time_slot_interval": 30,
                "operating_hours_start": time(8, 0),
                "operating_hours_end": time(18, 0),
                "invoice_number_prefix": "WAC-",
            },
        )
        return club

    def _setup_taxonomy(self, club):
        roles = {n: Role.objects.get_or_create(club=club, name=n)[0] for n in ROLES}
        cats  = {
            n: MembershipCategory.objects.get_or_create(
                club=club, name=n, defaults={"is_member": m})[0]
            for n, m in MEMBER_CATEGORIES
        }
        self.stdout.write(f"  Roles: {len(roles)}  Categories: {len(cats)}")
        return roles, cats

    def _setup_fleet(self, club):
        objs = []
        for spec in AIRCRAFT_FLEET:
            spec = dict(spec)  # don't mutate module-level constant
            type_name = spec.pop("type_name")
            ac_type, _ = AircraftType.objects.get_or_create(club=club, name=type_name)
            ac, created = Aircraft.objects.get_or_create(
                club=club, registration=spec["registration"],
                defaults={**spec, "aircraft_type": ac_type},
            )
            objs.append(ac)
            self.stdout.write(
                f"  {'Created' if created else 'Exists':8} {ac.registration} "
                f"({type_name}, {ac.seats}-seat)"
            )
        return objs

    def _setup_flight_types(self, club):
        ft_map = {}
        for spec in FLIGHT_TYPES:
            ft, _ = FlightType.objects.get_or_create(
                club=club, code=spec["code"], defaults=spec)
            ft_map[spec["code"]] = ft
        return ft_map

    def _setup_people(self, club, roles, cats):
        members = []
        for row in PEOPLE:
            username, first, last, role_name, cat_name, standing, exp_str, res_str, pay_m, _ = row
            user, u_created = User.objects.get_or_create(
                username=username,
                defaults={"first_name": first, "last_name": last},
            )
            if u_created:
                user.first_name, user.last_name = first, last
                user.set_password(DEFAULT_PASSWORD)
                if role_name == "Admin":
                    user.is_staff = user.is_superuser = True
                user.save()
            exp  = date.fromisoformat(exp_str) if exp_str else None
            res  = date.fromisoformat(res_str) if res_str else None
            m, _ = ClubMember.objects.get_or_create(
                user=user, club=club,
                defaults={
                    "role": roles[role_name],
                    "membership_category": cats.get(cat_name),
                    "standing": standing,
                    "subscription_expires": exp,
                    "resigned_at": res,
                },
            )
            ClubMember.objects.filter(pk=m.pk).update(
                role=roles[role_name],
                membership_category=cats.get(cat_name),
                standing=standing,
                subscription_expires=exp,
                resigned_at=res,
            )
            members.append(m)
        self.stdout.write(f"  People: {len(members)}")
        return members

    def _setup_charge_rates(self, club, aircraft, ft):
        created = 0
        for ac in aircraft:
            rate = HIRE_RATE.get(ac.registration)
            if not rate:
                continue
            for code in ("DUAL", "SOLO", "XC", "TRIAL"):
                ftype = ft.get(code)
                if not ftype:
                    continue
                _, c = ChargeRate.objects.get_or_create(
                    aircraft=ac,
                    flight_type=ftype,
                    time_method=ac.total_time_method,
                    defaults={"club": club, "amount": rate},
                )
                if c:
                    created += 1
        self.stdout.write(f"  Charge rates: {created} created")

    # ── Booking generation ────────────────────────────────────────────────────

    def _generate_bookings(self, club, aircraft, members, ft, admin_user):
        today = datetime.now(tz=NZ).date()
        instructors    = [m for m in members if m.role and m.role.name == "Instructor"]
        flying_members = [m for m in members if m.standing == "active"
                          and m.role and m.role.name == "Member"]

        two_seaters  = [a for a in aircraft if a.seats == 2]
        four_seaters = [a for a in aircraft if a.seats == 4]

        instructor_busy = defaultdict(lambda: defaultdict(list))
        member_busy     = defaultdict(lambda: defaultdict(list))
        bookings = []

        for day_offset in range(-14, 43):
            day        = today + timedelta(days=day_offset)
            is_weekday = day.weekday() < 5
            period     = "past" if day_offset < 0 else ("busy" if day_offset <= 21 else "sparse")
            day_key    = day.isoformat()
            now_nz     = datetime.now(tz=NZ)

            for ac in aircraft:
                is_two = ac.seats == 2
                fill   = FILL[(is_two, is_weekday, period)]

                for slot_h, slot_m in SLOTS:
                    if random.random() > fill:
                        continue

                    slot_start = datetime(day.year, day.month, day.day,
                                         slot_h, slot_m, tzinfo=NZ)
                    slot_end   = slot_start + timedelta(minutes=SLOT_MINS)

                    dual_prob       = 0.75 if is_two else 0.45
                    instructor_user = None
                    if random.random() < dual_prob:
                        instructor_user = self._pick_instructor(
                            instructors, slot_start, slot_end,
                            day_key, instructor_busy)

                    flight_type = (
                        ft["DUAL"] if instructor_user
                        else (ft["SOLO"] if is_two
                              else random.choice([ft["SOLO"], ft["XC"]]))
                    )

                    member = self._pick_member(
                        flying_members, slot_start, slot_end,
                        day_key, member_busy)
                    if member is None:
                        continue

                    if slot_end < now_nz:
                        status = (BookingStatus.COMPLETED
                                  if random.random() > 0.06
                                  else BookingStatus.CANCELLED)
                    elif slot_start < now_nz:
                        status = BookingStatus.DEPARTED
                    else:
                        status = (BookingStatus.CONFIRMED
                                  if random.random() > 0.18
                                  else BookingStatus.PENDING)

                    confirmed_by = admin_user if status in (
                        BookingStatus.CONFIRMED, BookingStatus.COMPLETED,
                        BookingStatus.DEPARTED) else None
                    confirmed_at = (
                        slot_start - timedelta(hours=random.randint(1, 48))
                        if confirmed_by else None)

                    b = Booking(
                        club=club, aircraft=ac, member=member,
                        flight_type=flight_type, instructor=instructor_user,
                        scheduled_start=slot_start, scheduled_end=slot_end,
                        status=status, created_by=admin_user,
                        confirmed_by=confirmed_by, confirmed_at=confirmed_at,
                    )
                    if status == BookingStatus.COMPLETED:
                        b.departed_at = slot_start + timedelta(minutes=random.randint(2, 8))
                        b.arrived_at  = slot_end   - timedelta(minutes=random.randint(3, 12))
                    elif status == BookingStatus.DEPARTED:
                        b.departed_at = slot_start + timedelta(minutes=random.randint(1, 6))
                    bookings.append(b)

        created = Booking.objects.bulk_create(bookings)
        self.stdout.write(
            f"  Generated {len(created)} bookings "
            f"({date.today() - timedelta(days=14)} → {date.today() + timedelta(days=42)})"
        )

        # ── Guaranteed tomorrow bookings for demo screenshots ────────────────
        tomorrow = today + timedelta(days=1)
        instr_jane  = next((m for m in instructors if m.user.username == 'jane'), None)
        member_mike = next((m for m in flying_members if m.user.username == 'mike'), None)
        member_rita = next((m for m in flying_members if m.user.username == 'rita'), None)
        demo_2seat  = two_seaters[0] if two_seaters else None
        demo_4seat  = four_seaters[0] if four_seaters else None

        if instr_jane and member_mike and demo_2seat:
            t = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 9, 30, tzinfo=NZ)
            if not Booking.objects.filter(club=club, aircraft=demo_2seat,
                                           scheduled_start=t).exists():
                Booking.objects.create(
                    club=club, aircraft=demo_2seat, member=member_mike,
                    flight_type=ft.get("DUAL"), instructor=instr_jane.user,
                    scheduled_start=t, scheduled_end=t + timedelta(minutes=90),
                    status=BookingStatus.CONFIRMED, created_by=admin_user,
                    confirmed_by=admin_user,
                    confirmed_at=t - timedelta(hours=18),
                )
        if member_rita and demo_4seat:
            t = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 11, 0, tzinfo=NZ)
            if not Booking.objects.filter(club=club, aircraft=demo_4seat,
                                           scheduled_start=t).exists():
                Booking.objects.create(
                    club=club, aircraft=demo_4seat, member=member_rita,
                    flight_type=ft.get("XC"), instructor=None,
                    scheduled_start=t, scheduled_end=t + timedelta(minutes=90),
                    status=BookingStatus.CONFIRMED, created_by=admin_user,
                    confirmed_by=admin_user,
                    confirmed_at=t - timedelta(hours=10),
                )

    def _pick_instructor(self, instructors, slot_start, slot_end, day_key, busy):
        available = [
            i for i in instructors
            if not any(s < slot_end and e > slot_start
                       for s, e in busy[day_key][i.user.pk])
        ]
        if not available:
            return None
        chosen = random.choice(available)
        busy[day_key][chosen.user.pk].append((slot_start, slot_end))
        return chosen.user

    def _pick_member(self, members, slot_start, slot_end, day_key, busy):
        available = [
            m for m in members
            if not any(s < slot_end and e > slot_start
                       for s, e in busy[day_key][m.pk])
        ]
        if not available:
            return None
        chosen = random.choice(available)
        busy[day_key][chosen.pk].append((slot_start, slot_end))
        return chosen

    # ── Accounts ──────────────────────────────────────────────────────────────

    def _setup_accounts(self, club, members, admin_user):
        people_map = {row[0]: row for row in PEOPLE}
        accounts_created = 0
        txn_created = 0

        for member in members:
            row = people_map.get(member.user.username)
            if not row:
                continue
            _, _, _, role_name, _, standing, _, _, pay_method, top_up = row

            # Create account
            is_instructor = role_name == "Instructor"
            account, created = Account.objects.get_or_create(
                club_member=member,
                defaults={
                    "preferred_payment_method": pay_method,
                    "credit_limit": None if is_instructor else Decimal("500"),
                },
            )
            if created:
                accounts_created += 1

            # Add initial top-up credit for credit-paying members
            if top_up > 0 and account.balance == 0:
                # 2-3 top-up transactions spread over past months
                tops = self._split_topup(top_up)
                for i, amt in enumerate(tops):
                    months_ago = len(tops) - i
                    txn_date = datetime.now(tz=NZ) - timedelta(days=months_ago * 35)
                    AccountTransaction.objects.create(
                        account=account,
                        transaction_type="top_up",
                        direction="credit",
                        amount=amt,
                        description=f"Account top-up — bank transfer",
                        payment_method="bank_transfer",
                        created_by=admin_user,
                        created_at=txn_date,
                    )
                    account.apply_transaction(amt, "credit")
                txn_created += len(tops)

        self.stdout.write(f"  Accounts: {accounts_created} created, {txn_created} top-up txns")

    def _split_topup(self, total):
        """Split a top-up into 2-3 realistic amounts."""
        if total <= Decimal("800"):
            return [total]
        parts = random.randint(2, 3)
        chunk = (total / parts).quantize(Decimal("0.01"))
        result = [chunk] * (parts - 1)
        result.append(total - chunk * (parts - 1))
        return result

    # ── Flight completions ────────────────────────────────────────────────────

    def _setup_completions(self, club, aircraft, members, admin_user):
        people_map   = {row[0]: row[8] for row in PEOPLE}  # username → pay_method
        member_map   = {m.user.username: m for m in members}
        account_map  = {
            m.pk: Account.objects.filter(club_member=m).first()
            for m in members
        }

        # Running meter readings per aircraft
        meter = {}
        for ac in aircraft:
            init = Decimal(str(ac.hobbs_initial or "1000"))
            meter[ac.pk] = init

        completed_bookings = (
            Booking.objects.filter(club=club, status=BookingStatus.COMPLETED)
            .select_related("aircraft", "member", "member__user", "flight_type", "instructor")
            .order_by("scheduled_start")
        )

        completions_to_create = []
        charges_to_create     = []
        payments_to_create    = []
        txns_to_create        = []

        for booking in completed_bookings:
            # Actual hobbs hours ≈ 0.9–1.4 hrs (typically less than scheduled 1.5)
            actual_hrs = Decimal(str(round(random.uniform(0.9, 1.4), 1)))
            hire_rate  = HIRE_RATE.get(booking.aircraft.registration, Decimal("185"))

            hire_amt = (hire_rate * actual_hrs).quantize(Decimal("0.01"))
            instr_amt = Decimal("0")
            if booking.instructor:
                instr_amt = (INSTRUCTOR_RATE * actual_hrs).quantize(Decimal("0.01"))
            total = hire_amt + instr_amt

            # Meter readings
            start_reading = meter[booking.aircraft.pk]
            end_reading   = start_reading + actual_hrs
            meter[booking.aircraft.pk] = end_reading

            pay_method = people_map.get(booking.member.user.username, "credit")
            is_invoice = pay_method == "invoice"

            # Some partial payments for invoice payers (20% chance)
            partial = is_invoice and random.random() < 0.20

            if is_invoice:
                completion_pay_method = "invoice"
                amount_paid = (total * Decimal("0.5")).quantize(Decimal("0.01")) if partial else Decimal("0")
                paid_at     = booking.arrived_at if partial else None
            else:
                completion_pay_method = "credit"
                amount_paid = total
                paid_at     = booking.arrived_at

            fc = FlightCompletion(
                booking=booking,
                outcome="completed",
                actual_flight_hours=actual_hrs,
                hobbs_start=start_reading,
                hobbs_end=end_reading,
                total_charge=total,
                payment_method=completion_pay_method,
                amount_paid=amount_paid,
                paid_at=paid_at,
                logged_by=admin_user,
            )
            completions_to_create.append((fc, booking, hire_amt, instr_amt,
                                           total, amount_paid, pay_method, partial))

        # Bulk create completions first so we have PKs for charge items
        fc_objs = FlightCompletion.objects.bulk_create(
            [x[0] for x in completions_to_create])

        charge_items = []
        payment_rows = []
        account_debits = []  # (account, amount)

        for fc_obj, (_, booking, hire_amt, instr_amt, total, amount_paid,
                     pay_method, partial) in zip(fc_objs, completions_to_create):
            # Charge items
            charge_items.append(FlightChargeItem(
                flight_completion=fc_obj,
                item_type="hire",
                description=f"{booking.aircraft.registration} hire",
                amount=hire_amt,
            ))
            if instr_amt:
                charge_items.append(FlightChargeItem(
                    flight_completion=fc_obj,
                    item_type="instructor",
                    description="Instructor fee",
                    amount=instr_amt,
                ))

            # Payment record
            pay_method_str = "credit" if pay_method == "credit" else "invoice"
            payment_rows.append(FlightPayment(
                completion=fc_obj,
                member=booking.member,
                amount=amount_paid if amount_paid > 0 else total,
                method=pay_method_str,
                paid_at=booking.arrived_at if pay_method == "credit" else (
                    booking.arrived_at if partial else None),
                recorded_by=admin_user,
            ))

            # Debit account transaction for credit payers
            if pay_method == "credit":
                account = account_map.get(booking.member.pk)
                if account:
                    account_debits.append((account, total, booking))

        FlightChargeItem.objects.bulk_create(charge_items)
        FlightPayment.objects.bulk_create(payment_rows)

        # Apply account debits
        for account, amount, booking in account_debits:
            AccountTransaction.objects.create(
                account=account,
                transaction_type="flight",
                direction="debit",
                amount=amount,
                description=(
                    f"Flight — {booking.aircraft.registration} "
                    f"{booking.scheduled_start.strftime('%d %b %Y')}"
                ),
                flight_completion=None,
                created_by=admin_user,
                created_at=booking.arrived_at or booking.scheduled_end,
            )
            account.apply_transaction(amount, "debit")

        self.stdout.write(
            f"  FlightCompletions: {len(fc_objs)} | "
            f"ChargeItems: {len(charge_items)} | "
            f"Payments: {len(payment_rows)}"
        )

    # ── Invoices ──────────────────────────────────────────────────────────────

    def _setup_invoices(self, club, members, admin_user):
        invoice_members = [
            m for m in members
            if m.user.username in INVOICE_PAYERS
        ]
        member_map = {m.user.username: m for m in invoice_members}
        today = date.today()
        inv_num = Invoice.objects.filter(club=club).count() + 1
        invoices_created = 0

        # Monthly invoices for each invoice-paying member: 3 months history
        # March → paid, April → paid, May → overdue, June → current/draft
        months = [
            # (label, issue_offset_days, due_offset_days, status, amount_paid_pct)
            ("March 2026 flights", -105, -75, "paid",  1.0),
            ("April 2026 flights", -75,  -45, "paid",  1.0),
            ("May 2026 flights",   -45,  -15, "sent",  0.0),  # overdue
            ("June 2026 flights",  -14,  +16, "sent",  0.0),  # current
        ]

        for username, member in member_map.items():
            for label, issue_off, due_off, status, paid_pct in months:
                issue = today + timedelta(days=issue_off)
                due   = today + timedelta(days=due_off)

                # Get 3-6 flight completions for this member in that month window
                fc_qs = FlightCompletion.objects.filter(
                    booking__member=member,
                    booking__scheduled_start__date__gte=issue,
                    booking__scheduled_start__date__lt=issue + timedelta(days=32),
                ).select_related("booking__aircraft")[:5]

                line_total = sum(fc.total_charge for fc in fc_qs)
                if not line_total:
                    line_total = Decimal(str(random.randint(300, 900)))

                amount_paid = (line_total * Decimal(str(paid_pct))).quantize(
                    Decimal("0.01"))

                inv = Invoice.objects.create(
                    club=club,
                    member=member,
                    invoice_number=inv_num,
                    issue_date=issue,
                    due_date=due,
                    description=f"{label} — {member.user.get_full_name()}",
                    status=status,
                    gst_rate=Decimal("15"),
                    amount_paid=amount_paid,
                    sent_at=datetime(issue.year, issue.month, issue.day,
                                     9, 0, tzinfo=NZ) if status != "draft" else None,
                    paid_at=(datetime(due.year, due.month, due.day,
                                      14, 0, tzinfo=NZ)
                             if status == "paid" else None),
                    created_by=admin_user,
                )
                inv_num += 1
                invoices_created += 1

                # Line items: one per flight or a summary line
                if fc_qs:
                    for i, fc in enumerate(fc_qs):
                        InvoiceLineItem.objects.create(
                            invoice=inv,
                            description=(
                                f"{fc.booking.aircraft.registration} — "
                                f"{fc.booking.scheduled_start.strftime('%d %b')}"
                            ),
                            quantity=fc.actual_flight_hours,
                            unit="hrs",
                            rate=HIRE_RATE.get(fc.booking.aircraft.registration,
                                              Decimal("185")),
                            amount=fc.total_charge,
                            sort_order=i,
                        )
                else:
                    InvoiceLineItem.objects.create(
                        invoice=inv,
                        description="Flight hire (see flight records)",
                        quantity=Decimal("1"),
                        unit="",
                        rate=line_total,
                        amount=line_total,
                        sort_order=0,
                    )

        # Add one subscription invoice for a member renewal (for UI demo)
        renewal_member = next(
            (m for m in members if m.user.username == "sarah"), None)
        if renewal_member:
            inv = Invoice.objects.create(
                club=club,
                member=renewal_member,
                invoice_number=inv_num,
                issue_date=today - timedelta(days=5),
                due_date=today + timedelta(days=25),
                description="Annual subscription 2026-27",
                status="sent",
                gst_rate=Decimal("15"),
                amount_paid=Decimal("0"),
                sent_at=datetime.now(tz=NZ) - timedelta(days=5),
                created_by=admin_user,
                subscription_expiry_date=date(2027, 3, 31),
            )
            inv_num += 1
            invoices_created += 1
            InvoiceLineItem.objects.create(
                invoice=inv,
                description="Annual membership subscription — Full Member",
                quantity=Decimal("1"),
                unit="",
                rate=Decimal("650"),
                amount=Decimal("650"),
                sort_order=0,
            )

        self.stdout.write(f"  Invoices: {invoices_created} created")

    # ── Block-outs ────────────────────────────────────────────────────────────

    def _setup_blockouts(self, club, aircraft, members, admin_user):
        today = date.today()
        ac_map   = {a.registration: a for a in aircraft}
        inst_map = {
            m.user.username: m.user
            for m in members
            if m.role and m.role.name == "Instructor"
        }

        # Create block-out types
        bt = {}
        for spec in BLOCKOUT_TYPES:
            obj, _ = BlockOutType.objects.get_or_create(
                club=club, name=spec["name"],
                defaults={k: v for k, v in spec.items() if k != "name"},
            )
            bt[spec["name"]] = obj

        blocks_created = 0

        def make_block(**kwargs):
            nonlocal blocks_created
            b = BlockOut.objects.create(club=club, created_by=admin_user, **kwargs)
            blocks_created += 1
            return b

        # 1. ZK-WAC 100-hour check — next Monday & Tuesday, all day
        next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
        for d in [next_monday, next_monday + timedelta(days=1)]:
            b = make_block(
                blockout_type=bt["100-hour Check"],
                label="ZK-WAC 100-hour check",
                scope="aircraft",
                recurrence="one_off",
                date=d,
                all_day=True,
            )
            b.aircraft.set([ac_map["ZK-WAC"]])

        # 2. ZK-BCX annual inspection — 3 weeks out, 3 days
        ann_start = today + timedelta(days=21)
        for i in range(3):
            b = make_block(
                blockout_type=bt["Annual Inspection"],
                label="ZK-BCX annual inspection",
                scope="aircraft",
                recurrence="one_off",
                date=ann_start + timedelta(days=i),
                all_day=True,
            )
            b.aircraft.set([ac_map["ZK-BCX"]])

        # 3. Daily lunch break 12:30–13:30 (all instructors, soft, recurring)
        b = make_block(
            blockout_type=bt["Lunch Break"],
            label="Lunch",
            scope="instructors",
            recurrence="daily",
            all_day=False,
            start_time=time(12, 30),
            end_time=time(13, 30),
            active_from=today,
            active_until=today + timedelta(weeks=10),
        )
        b.instructors.set(list(inst_map.values()))

        # 4. Kate Wilson away — training course Thursday & Friday next week
        next_thu = today + timedelta(days=(3 - today.weekday()) % 7 + 7)
        for d in [next_thu, next_thu + timedelta(days=1)]:
            b = make_block(
                blockout_type=bt["Instructor Leave"],
                label="Kate away — CFI refresher course",
                scope="instructors",
                recurrence="one_off",
                date=d,
                all_day=True,
            )
            b.instructors.set([inst_map["kate"]])

        # 5. Club air day next Saturday — all resources
        next_sat = today + timedelta(days=(5 - today.weekday()) % 7 + 7)
        make_block(
            blockout_type=bt["Club Event / Air Day"],
            label="Club open day & fly-in",
            scope="all",
            recurrence="one_off",
            date=next_sat,
            all_day=True,
        )

        # 6. Saturday morning maintenance check (weekly, 08:00–09:30, all aircraft)
        b = make_block(
            blockout_type=bt["Maintenance Morning"],
            label="Weekly maintenance inspection",
            scope="aircraft",
            recurrence="weekly",
            weekday=5,  # Saturday
            all_day=False,
            start_time=time(8, 0),
            end_time=time(9, 30),
            active_from=today,
            active_until=today + timedelta(weeks=12),
        )
        b.aircraft.set(aircraft)

        self.stdout.write(f"  Block-outs: {blocks_created} created")
