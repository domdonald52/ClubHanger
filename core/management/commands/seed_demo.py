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
  python manage.py seed_demo --slug staging --name "Staging Club"  # named copy

The demo club defaults to "Wellington Aero Club (Demo)" / "wac-demo". The real
"wellington-aero-club" slug is reserved for production and the seed refuses to
touch it unless --force is given.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import random
import os

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_save
from django.utils import timezone

from core.models import (
    Club, ClubConfig, Role, MembershipCategory, ClubMember, Account,
    AccountTransaction, Aircraft, AircraftType, ChargeRate, FlightType,
    Booking, BookingStatus, FlightCompletion, FlightChargeItem, FlightPayment,
    Invoice, InvoiceLineItem, BlockOutType, BlockOut, OccurrenceReport,
    Aerodrome, AerodromeFeeType, MemberCredential, CredentialType,
    AircraftMaintenanceItem, LessonNote,
)

User = get_user_model()
NZ = ZoneInfo('Pacific/Auckland')

# ── Club ─────────────────────────────────────────────────────────────────────

# Demo identity. Kept deliberately distinct from the real club: "Wellington
# Aero Club" / "wellington-aero-club" is reserved for the first PRODUCTION
# instance, so the seed must never create or mutate it (get_or_create on the
# prod slug would otherwise dump demo data into the live club).
DEMO_NAME = "Wellington Aero Club (Demo)"
DEMO_SLUG = "wac-demo"
RESERVED_SLUGS = {"wellington-aero-club"}

# Seeded admin login. The username IS an email so it works with the
# email-enforcing login screen (the old 'dominic' username could not be entered
# there). Override with the SEED_ADMIN_EMAIL env var (e.g. your real address, so
# password-reset emails actually reach you); the default is a placeholder kept
# out of any personal inbox and safe to commit to a public repo.
ADMIN_EMAIL = os.environ.get("SEED_ADMIN_EMAIL", "admin@wac-demo.example")

CLUB = {
    "name": DEMO_NAME,
    "slug": DEMO_SLUG,
    "phone": "04 388 8000",
    "email": "office@wellingtonaero.example",
    "address": "Main Terminal, Wellington Airport\nRongotai, Wellington 6022",
    "timezone": "Pacific/Auckland",
    "currency": "NZD",
}

# Demo billing + theme. PLACEHOLDERS — clearly a demo. Real GST/bank details
# belong on the production club, entered via Settings (never committed here).
DEMO_BILLING = {
    "invoice_number_prefix": "WAC-",
    "payment_terms_days": 14,
    "payment_terms_text": ("Payment due within 14 days. Direct credit to the "
                           "account above, quoting your invoice number."),
    "gst_number": "123-456-789",
    "bank_name": "ANZ Bank",
    "bank_account": "01-0123-0123456-00",
}
DEMO_THEME = {
    "theme_banner": "#1d3a5f",   # navy — matches the app icon
    "theme_primary": "#2f7dd1",
    "theme_accent": "#4a90d9",
}

# ── Aircraft fleet ────────────────────────────────────────────────────────────

AIRCRAFT_FLEET = [
    dict(registration="ZK-WAC", type_name="PA38 Tomahawk", seats=2,
         total_time_method="tacho", records_tacho=True,  records_hobbs=False,
         fuel_consumption_per_hour="22.0", hobbs_initial="4210.3",
         maint_hours_initial="4210.3"),
    dict(registration="ZK-TAW", type_name="Cessna 152",    seats=2,
         total_time_method="hobbs", records_tacho=False, records_hobbs=True,
         fuel_consumption_per_hour="19.0", hobbs_initial="6831.7",
         maint_hours_initial="6831.7"),
    dict(registration="ZK-BCX", type_name="Cessna 172S",   seats=4,
         total_time_method="hobbs", records_tacho=False, records_hobbs=True,
         fuel_consumption_per_hour="34.0", hobbs_initial="2941.6",
         maint_hours_initial="2941.6"),
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
    (ADMIN_EMAIL,"Dominic","Donald",    "Admin",      "Full Member",         "active", "2027-03-31",None, "credit",  Decimal("1500")),
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
        parser.add_argument('--slug', default=DEMO_SLUG,
                            help=f'Club slug to seed (default: {DEMO_SLUG}). '
                                 f'The production slug is reserved.')
        parser.add_argument('--name', default=DEMO_NAME,
                            help=f'Club name, used only when first created '
                                 f'(default: "{DEMO_NAME}").')
        parser.add_argument('--force', action='store_true',
                            help='Allow seeding a reserved/production slug (dangerous).')

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(42)
        reset = options['reset']
        slug = options['slug']
        name = options['name']

        if slug in RESERVED_SLUGS and not options['force']:
            raise CommandError(
                f"Refusing to seed reserved production slug '{slug}'. "
                f"Use a demo slug (default '{DEMO_SLUG}'), or pass --force to override.")

        club = self._setup_club(slug, name)

        if reset:
            self.stdout.write("  Wiping existing demo data...")
            BlockOut.objects.filter(club=club).delete()
            BlockOutType.objects.filter(club=club).delete()
            FlightCompletion.objects.filter(booking__club=club).delete()
            Invoice.objects.filter(club=club).delete()
            Booking.objects.filter(club=club).delete()
            AircraftMaintenanceItem.objects.filter(aircraft__club=club).delete()
            ChargeRate.objects.filter(aircraft__club=club).delete()
            Aircraft.objects.filter(club=club).delete()
            AircraftType.objects.filter(club=club).delete()
            # Delete ALL club members (not just PEOPLE list) so stale users from
            # previous seed versions don't survive as ghost instructors.
            # Must delete PROTECT-referencing models first.
            stale_user_ids = list(
                ClubMember.objects.filter(club=club).values_list('user_id', flat=True)
            )
            OccurrenceReport.objects.filter(reported_by__club=club).delete()
            FlightPayment.objects.filter(member__club=club).delete()
            ClubMember.objects.filter(club=club).delete()
            # Only remove users who now belong to NO club at all — so seeding a
            # second demo club that shares this roster doesn't delete members
            # still in use by the other club.
            orphaned_ids = [
                uid for uid in stale_user_ids
                if not ClubMember.objects.filter(user_id=uid).exists()
            ]
            User.objects.filter(id__in=orphaned_ids, is_superuser=False).delete()
            AerodromeFeeType.objects.filter(aerodrome__club=club).delete()
            Aerodrome.objects.filter(club=club).delete()

        roles, cats = self._setup_taxonomy(club)
        aircraft    = self._setup_fleet(club)
        ft          = self._setup_flight_types(club)
        members     = self._setup_people(club, roles, cats)
        admin_user  = User.objects.filter(is_superuser=True).first()

        self._setup_aerodromes(club)
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

        # ── Lesson notes (idempotent — skips if any exist) ────────────────────
        if LessonNote.objects.filter(booking__club=club).exists():
            self.stdout.write("  Lesson notes exist — skipping")
        else:
            self._setup_lesson_notes(club, members)

        # ── Block-outs ─────────────────────────────────────────────────────────
        if BlockOut.objects.filter(club=club).exists():
            self.stdout.write("  Block-outs exist — skipping")
        else:
            self._setup_blockouts(club, aircraft, members, admin_user)

        # ── Maintenance items ──────────────────────────────────────────────────
        self._setup_maintenance(club, aircraft)

        # ── Instructor credentials ─────────────────────────────────────────────
        self._setup_instructor_credentials(club, members)

        # ── Instructor availability (roster windows) ───────────────────────────
        # Without these, the off-roster check treats every instructor as
        # unavailable and flags all their bookings on the Attention page.
        self._setup_instructor_availability(club, members)

        # ── Dominic's personal demo data (always idempotent) ──────────────────
        self._setup_dominic(club, aircraft, members, ft, admin_user)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Login: {ADMIN_EMAIL} / {DEFAULT_PASSWORD}\n"
            f"Management app: /manage/{club.slug}/\n"
            f"Mobile app:     /app/{club.slug}/"
        ))

    # ── Setup helpers ─────────────────────────────────────────────────────────

    def _setup_club(self, slug=DEMO_SLUG, name=DEMO_NAME):
        defaults = {**CLUB, "slug": slug, "name": name}
        club, created = Club.objects.get_or_create(slug=slug, defaults=defaults)
        # Keep demo contact details fresh on re-seed (defaults only apply on create).
        club.phone = CLUB["phone"]
        club.email = CLUB["email"]
        club.address = CLUB["address"]
        club.save(update_fields=["phone", "email", "address"])
        self.stdout.write(f"Club: {club.name} ({'created' if created else 'exists'})")

        config, _ = ClubConfig.objects.get_or_create(
            club=club,
            defaults={
                "default_booking_duration": 90,
                "time_slot_interval": 30,
                "operating_hours_start": time(8, 0),
                "operating_hours_end": time(18, 0),
            },
        )
        # Sync demo billing + theme every run (so --reset refreshes presentation).
        # Logo is left untouched — supply a real PNG and set it in Settings.
        for field, value in {**DEMO_BILLING, **DEMO_THEME}.items():
            setattr(config, field, value)
        config.save()
        return club

    def _setup_taxonomy(self, club):
        roles = {n: Role.objects.get_or_create(club=club, name=n)[0] for n in ROLES}
        # Ensure system_role_type and permission flags are correct
        Role.objects.filter(club=club, name="Admin").update(
            system_role_type='admin', is_superadmin=True,
            can_access_manage=True, can_access_fleet=True, can_access_safety=True,
            can_access_settings=True, can_access_reports=True,
            bookings_access='manage_all',
        )
        Role.objects.filter(club=club, name="Instructor").update(
            system_role_type='instructor', is_superadmin=False,
            can_access_manage=True, can_access_fleet=False, can_access_safety=True,
            can_access_settings=False, can_access_reports=False,
            bookings_access='manage_all',
        )
        Role.objects.filter(club=club, name="Member").update(
            system_role_type='member', is_superadmin=False,
            can_access_manage=False, can_access_fleet=False, can_access_safety=False,
            can_access_settings=False, can_access_reports=False,
            bookings_access='manage_own',
        )
        roles = {n: Role.objects.get(club=club, name=n) for n in ROLES}
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

    def _setup_aerodromes(self, club):
        AERODROMES = [
            dict(icao_code="NZWN", name="Wellington International", is_home=True,
                 notes="Home base. No landing fees for club aircraft."),
            dict(icao_code="NZPP", name="Paraparaumu", is_home=False,
                 notes="Common training area. Full stop $15, T&G $8."),
            dict(icao_code="NZMS", name="Masterton (Hood)", is_home=False,
                 notes="Cross-country destination. Full stop $20."),
            dict(icao_code="NZOH", name="Ohakea", is_home=False,
                 notes="Military — prior permission required. PPR: 04 498 2000."),
        ]
        created = 0
        for spec in AERODROMES:
            ad, c = Aerodrome.objects.get_or_create(
                club=club, icao_code=spec["icao_code"], defaults=spec)
            if c:
                created += 1
                if spec["icao_code"] == "NZPP":
                    AerodromeFeeType.objects.get_or_create(
                        aerodrome=ad, name="Full stop",
                        defaults={"default_amount": "15.00"})
                    AerodromeFeeType.objects.get_or_create(
                        aerodrome=ad, name="Touch & go",
                        defaults={"default_amount": "8.00"})
                elif spec["icao_code"] == "NZMS":
                    AerodromeFeeType.objects.get_or_create(
                        aerodrome=ad, name="Full stop",
                        defaults={"default_amount": "20.00"})
        self.stdout.write(f"  Aerodromes: {created} created")

    def _setup_people(self, club, roles, cats):
        members = []
        for row in PEOPLE:
            username, first, last, role_name, cat_name, standing, exp_str, res_str, pay_m, _ = row
            user, u_created = User.objects.get_or_create(
                username=username,
                defaults={"first_name": first, "last_name": last},
            )
            user.first_name, user.last_name = first, last
            if u_created:
                user.set_password(DEFAULT_PASSWORD)
            if not user.email:
                # Username is already an email for the admin; synthesize one for
                # everyone else so no account is left without an address (which
                # would silently break password reset).
                user.email = username if "@" in username else f"{username}@wac-demo.example"
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
                is_on_instructor_roster=(role_name == "Instructor"),
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

            # Payment record — only if something was actually paid
            if amount_paid > 0:
                pay_method_str = "credit" if pay_method == "credit" else "invoice"
                payment_rows.append(FlightPayment(
                    completion=fc_obj,
                    member=booking.member,
                    amount=amount_paid,
                    method=pay_method_str,
                    paid_at=booking.arrived_at if pay_method == "credit" else (
                        booking.arrived_at if partial else None),
                    recorded_by=admin_user,
                ))

            # Debit account transaction for credit payers
            if pay_method == "credit":
                account = account_map.get(booking.member.pk)
                if account:
                    account_debits.append((account, total, booking, fc_obj))

        FlightChargeItem.objects.bulk_create(charge_items)
        FlightPayment.objects.bulk_create(payment_rows)

        # Apply account debits
        for account, amount, booking, fc_obj in account_debits:
            AccountTransaction.objects.create(
                account=account,
                transaction_type="flight",
                direction="debit",
                amount=amount,
                description=(
                    f"Flight — {booking.aircraft.registration} "
                    f"{booking.scheduled_start.strftime('%d %b %Y')}"
                ),
                flight_completion=fc_obj,
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
        config = club.config
        terms  = config.payment_terms_days or 14
        today  = date.today()
        inv_num = Invoice.objects.filter(club=club).count() + 1
        invoices_created = 0

        # Real-world model: one invoice per flight, raised at completion when the
        # pilot couldn't pay on the spot (no credit is extended). Issued on the
        # flight date; due payment_terms_days later — so older unpaid flights fall
        # overdue on their own. Driven off the actual invoice-method completions
        # so every invoice ties back to a real flight.
        already = set(
            Invoice.objects.filter(club=club, flight_completion__isnull=False)
            .values_list("flight_completion_id", flat=True))

        inv_completions = (
            FlightCompletion.objects
            .filter(booking__club=club, payment_method="invoice", outcome="completed")
            .select_related("booking__aircraft", "booking__member__user",
                            "booking__flight_type", "booking__instructor")
            .prefetch_related("charge_items")
            .order_by("booking__scheduled_start")
        )

        for fc in inv_completions:
            if fc.pk in already:
                continue
            b = fc.booking
            flight_date = b.scheduled_start.astimezone(NZ).date()
            issue = flight_date
            due   = flight_date + timedelta(days=terms)
            reg   = b.aircraft.registration

            # Has the bank transfer come through? Most past-due flights have been
            # settled; a few stay outstanding (the ones the office chases).
            if (today - flight_date).days > terms and random.random() < 0.65:
                status = "paid"
                amount_paid = fc.total_charge
                paid_at = (datetime(flight_date.year, flight_date.month, flight_date.day,
                                    10, 0, tzinfo=NZ) + timedelta(days=min(terms, 7)))
            else:
                status = "sent"
                amount_paid = fc.amount_paid   # 0, or a partial payment
                paid_at = None

            inv = Invoice.objects.create(
                club=club, member=b.member, flight_completion=fc,
                invoice_number=inv_num,
                issue_date=issue, due_date=due,
                description=f"Flight hire — {reg} {flight_date.strftime('%d %b %Y')}",
                status=status, gst_rate=Decimal("15"),
                amount_paid=amount_paid,
                sent_at=datetime(issue.year, issue.month, issue.day, 17, 0, tzinfo=NZ),
                paid_at=paid_at,
                created_by=admin_user,
            )
            inv_num += 1
            invoices_created += 1

            # Mirror the flight's charge breakdown as invoice line items.
            for i, ci in enumerate(fc.charge_items.all()):
                if ci.item_type in ("hire", "instructor"):
                    qty  = fc.actual_flight_hours
                    unit = "hrs"
                    rate = (HIRE_RATE.get(reg, Decimal("185"))
                            if ci.item_type == "hire" else INSTRUCTOR_RATE)
                else:
                    qty, unit, rate = Decimal("1"), "", ci.amount
                InvoiceLineItem.objects.create(
                    invoice=inv, description=ci.description,
                    quantity=qty, unit=unit, rate=rate, amount=ci.amount,
                    charge_item=ci, sort_order=i,
                )

            # Sync invoice state back onto the FC so display_status_key is correct.
            _fc_upd = ['invoice_issued']
            fc.invoice_issued = True
            if status == "paid":
                fc.amount_paid = fc.total_charge
                fc.paid_at = paid_at
                _fc_upd += ['amount_paid', 'paid_at']
                # Also advance the booking to completed
                Booking.objects.filter(pk=b.pk).update(status='completed')
            fc.save(update_fields=_fc_upd)

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

        # Ensure all invoice-method FCs are marked invoice_issued so they don't
        # appear as "Charges outstanding" in the bookings list.
        FlightCompletion.objects.filter(
            booking__club=club, payment_method="invoice", invoice_issued=False
        ).update(invoice_issued=True)

    # ── Maintenance items ─────────────────────────────────────────────────────

    def _setup_maintenance(self, club, aircraft):
        today = date.today()
        AircraftMaintenanceItem.objects.filter(aircraft__club=club).delete()
        ac = {a.registration: a for a in aircraft}

        items = [
            # ── ZK-WAC: annual GREEN, 100-hr GREEN, oil AMBER ──────────────────
            AircraftMaintenanceItem(
                aircraft=ac["ZK-WAC"], name="Annual inspection",
                due_date=today + timedelta(days=240), interval_days=365,
                last_completed_date=today - timedelta(days=125),
                warn_days=14, alert_days=7, urgency="green",
            ),
            AircraftMaintenanceItem(
                aircraft=ac["ZK-WAC"], name="100-hour check",
                due_hours=Decimal("4300"), interval_hours=Decimal("100"),
                last_completed_hours=Decimal("4200"),
                warn_hours=Decimal("20"), alert_hours=Decimal("5"),
                urgency="green",   # ≈90h remaining
            ),
            AircraftMaintenanceItem(
                aircraft=ac["ZK-WAC"], name="Oil & filter change",
                due_hours=Decimal("4238"), interval_hours=Decimal("50"),
                last_completed_hours=Decimal("4188"),
                warn_hours=Decimal("20"), alert_hours=Decimal("5"),
                urgency="amber",   # ≈28h remaining
            ),
            # ── ZK-TAW: annual RED, 100-hr RED, oil GREEN ─────────────────────
            AircraftMaintenanceItem(
                aircraft=ac["ZK-TAW"], name="Annual inspection",
                due_date=today + timedelta(days=4), interval_days=365,
                last_completed_date=today - timedelta(days=361),
                warn_days=14, alert_days=7, urgency="red",  # 4 days → RED
            ),
            AircraftMaintenanceItem(
                aircraft=ac["ZK-TAW"], name="100-hour check",
                due_hours=Decimal("6838"), interval_hours=Decimal("100"),
                last_completed_hours=Decimal("6738"),
                warn_hours=Decimal("20"), alert_hours=Decimal("5"),
                urgency="red",   # ≈6h remaining
            ),
            AircraftMaintenanceItem(
                aircraft=ac["ZK-TAW"], name="Oil & filter change",
                due_hours=Decimal("6880"), interval_hours=Decimal("50"),
                last_completed_hours=Decimal("6830"),
                warn_hours=Decimal("20"), alert_hours=Decimal("5"),
                urgency="green",  # ≈48h remaining
            ),
            # ── ZK-BCX: annual GREEN, 100-hr GREEN, oil AMBER, ELT AMBER ──────
            AircraftMaintenanceItem(
                aircraft=ac["ZK-BCX"], name="Annual inspection",
                due_date=today + timedelta(days=120), interval_days=365,
                last_completed_date=today - timedelta(days=245),
                warn_days=14, alert_days=7, urgency="green",
            ),
            AircraftMaintenanceItem(
                aircraft=ac["ZK-BCX"], name="100-hour check",
                due_hours=Decimal("2980"), interval_hours=Decimal("100"),
                last_completed_hours=Decimal("2880"),
                warn_hours=Decimal("20"), alert_hours=Decimal("5"),
                urgency="green",  # ≈38h remaining
            ),
            AircraftMaintenanceItem(
                aircraft=ac["ZK-BCX"], name="Oil & filter change",
                due_hours=Decimal("2958"), interval_hours=Decimal("50"),
                last_completed_hours=Decimal("2908"),
                warn_hours=Decimal("20"), alert_hours=Decimal("5"),
                urgency="amber",  # ≈16h remaining
            ),
            AircraftMaintenanceItem(
                aircraft=ac["ZK-BCX"], name="ELT battery replacement",
                due_date=today + timedelta(days=11), interval_days=365,
                last_completed_date=today - timedelta(days=354),
                warn_days=14, alert_days=7, urgency="amber",  # 11 days → AMBER
            ),
        ]
        AircraftMaintenanceItem.objects.bulk_create(items)
        self.stdout.write(f"  Maintenance items: {len(items)} across {len(aircraft)} aircraft")

    # ── Block-outs ────────────────────────────────────────────────────────────

    def _setup_lesson_notes(self, club, members):
        instructors = {m.user.username: m.user for m in members if m.role and m.role.name == "Instructor"}

        # Per-student note progressions — exercises, debrief, next plan
        PROGRESSIONS = [
            # Early student — first few lessons
            [
                (
                    "Pre-flight inspection walkthrough\nEffects of controls — straight and level\nClimbing and descending turns",
                    "Showed good awareness during pre-flight. Tendency to over-control in roll during turns — common at this stage. Altitude holding needs work but is improving. Confident communication with me in the cockpit.",
                    "Consolidate straight and level, climbing/descending turns. Introduce traffic pattern entry and circuit awareness. Review HASELL checks before next lesson.",
                ),
                (
                    "HASELL checks\nTraffic pattern — upwind, crosswind, downwind\nBase and final approach — power-off glide",
                    "Much better control feel this lesson. Circuit work is coming together — overshooting finals on the first two approaches, then corrected well. HASELL check read from notes — aim to have these memory items by next flight.",
                    "Continue circuit consolidation. Introduce solo standard — aim for three consistently stable approaches. Pre-solo checks briefing on the ground before we go up.",
                ),
                (
                    "First supervised solo — 3 circuits\nPost-solo debrief",
                    "Great first solo. Approaches were stable and consistent. Slight float on landing 3 — discussed ground effect and how to manage it. Big milestone achieved — student was understandably nervous beforehand but performed to a high standard once airborne.",
                    "Continue solo circuit consolidation — aim for 5 solo circuits next lesson before moving on to area solo. Revise emergency procedures (engine failure on take-off) before next dual.",
                ),
            ],
            # Intermediate student — working toward licence
            [
                (
                    "Stall recognition and recovery — power-on and power-off\nIncipient spin entry and recovery\nSteep turns 45°",
                    "Confident with power-off stalls. Hesitant on power-on stall recovery — discussed the importance of immediate rudder application. Steep turns were accurate within ±100 ft. Good situational awareness throughout.",
                    "Consolidate steep turns and stall recovery. Introduce forced landing procedure from altitude. Brief on MAYDAY calls.",
                ),
                (
                    "Precautionary landing — field selection and approach\nForced landing from 3000 ft\nPanavia checks",
                    "Field selection was methodical — good use of wind shadow and slope assessment. Forced landing height judgment was slightly high on first attempt; second was very good. Panavia checks completed without prompting.",
                    "Navigation exercise — local area 30 nm triangle. Introduce VOR tracking and NDB holds if time permits. File a student flight plan before the lesson.",
                ),
                (
                    "Cross-country navigation — NZWN–NZPM–NZWN\nDiversion exercise en route\nVOR tracking",
                    "Excellent flight — held headings and altitudes well throughout. Handled the diversion calmly; chose an appropriate alternate and recalculated ETA accurately. VOR tracking solid. Ready to progress to cross-country solo endorsement check.",
                    "Pre-licence check ride preparation. Review all memory items, emergency procedures, and licence skill test standards. Book the next lesson as a mock test.",
                ),
            ],
            # More advanced — instrument/night work
            [
                (
                    "IMC appreciation — unusual attitudes\nPartial panel — standby instruments only\nRecovery from spiral dive and incipient spin under the hood",
                    "Handled unusual attitudes well for a first instrument lesson. Partial panel showed tendency to fixate on altimeter — discussed the instrument scan. Good recovery technique once scan was re-established.",
                    "Continue partial panel flying. Introduce NDB tracking and procedure turns. Revise aerodrome met minima for IFR approaches.",
                ),
                (
                    "NDB non-precision approach — NZWN ILS/NDB Rwy 16\nMissed approach and holding pattern\nNight currency circuits — 4 landings",
                    "Approach was stabilised to MDA; decision-making on go/no-go was sound. Holding pattern entry was correct but timing drifted slightly on the outbound leg. Night circuits — good lighting awareness, maintained circuit altitude well.",
                    "Book a full night cross-country to maintain night rating currency. Continue instrument approaches — next lesson: ILS raw data (no flight director). Brief on SIGMET interpretation.",
                ),
            ],
        ]

        # Assign a progression to each student-like member (not instructors, not lapsed)
        student_usernames = [
            m.user.username for m in members
            if m.role and m.role.name not in ("Instructor",) and m.standing == "active"
        ]

        notes_created = 0
        total_students = len(student_usernames)
        self.stdout.write(f"  Lesson notes: 0/{total_students} students...")
        for i, username in enumerate(student_usernames):
            try:
                student_member = next(m for m in members if m.user.username == username)
            except StopIteration:
                continue

            progression = PROGRESSIONS[i % len(PROGRESSIONS)]

            # Find completed dual (instructed) bookings for this student, oldest first
            dual_bookings = list(
                Booking.objects.filter(
                    club=club,
                    member=student_member,
                    status=BookingStatus.COMPLETED,
                    instructor__isnull=False,
                )
                .select_related('instructor')
                .order_by('scheduled_start')[:len(progression)]
            )

            for booking, (exercises, debrief, next_plan) in zip(dual_bookings, progression):
                LessonNote.objects.create(
                    booking=booking,
                    author=booking.instructor,
                    exercises_covered=exercises,
                    debrief_notes=debrief,
                    next_lesson_plan=next_plan,
                )
                notes_created += 1

            self.stdout.write(f"  Lesson notes: {i + 1}/{total_students} students ({notes_created} notes)")

        self.stdout.write(f"  Lesson notes: {notes_created} created")

    def _setup_instructor_credentials(self, club, members):
        today = date.today()
        INSTR_CREDS = {
            # (username, cert_type, name, issue_date, expiry_date, cert_number)
            "sean":  [
                ('instr_b',   'B-Cat Instructor Certificate', date(2018, 4, 10), None,             'NZ-INSTR-B-2018-0342'),
                ('medical_c2','Class 2 Medical',               date(2024, 9, 1),  date(2026, 9, 1), 'NZ-MED2-2024-1187'),
                ('fr',        'Flight Review',                 date(2025, 3, 15), date(2027, 3, 15),''),
                ('type',      'Cessna 152',                    date(2015, 3, 20), None,             ''),
                ('type',      'Cessna 172S',                   date(2016, 6, 12), None,             ''),
                ('type',      'PA38 Tomahawk',                 date(2017, 11, 5), None,             ''),
            ],
            "jane":  [
                ('instr_c',   'C-Cat Instructor Certificate', date(2020, 7, 14), None,             'NZ-INSTR-C-2020-0891'),
                ('medical_c2','Class 2 Medical',               date(2025, 2, 1),  date(2027, 2, 1), 'NZ-MED2-2025-0334'),
                ('fr',        'Flight Review',                 date(2025, 8, 20), date(2027, 8, 20),''),
                ('type',      'Cessna 152',                    date(2019, 5, 8),  None,             ''),
                ('type',      'Cessna 172S',                   date(2020, 1, 22), None,             ''),
            ],
            "mark":  [
                ('instr_b',   'B-Cat Instructor Certificate', date(2016, 2, 28), None,             'NZ-INSTR-B-2016-0217'),
                ('medical_c2','Class 2 Medical',               date(2024, 6, 15), date(2026, 6, 15),'NZ-MED2-2024-0782'),
                ('fr',        'Flight Review',                 date(2024, 11, 5), date(2026, 11, 5),''),
                ('type',      'Cessna 152',                    date(2014, 8, 3),  None,             ''),
                ('type',      'Cessna 172S',                   date(2015, 10, 17),None,             ''),
                ('type',      'PA38 Tomahawk',                 date(2016, 3, 9),  None,             ''),
                ('tailwheel', 'Tailwheel Endorsement',         date(2016, 3, 9),  None,             ''),
            ],
            "kate":  [
                ('instr_c',   'C-Cat Instructor Certificate', date(2022, 11, 3), None,             'NZ-INSTR-C-2022-1204'),
                ('medical_c2','Class 2 Medical',               date(2025, 5, 1),  date(2027, 5, 1), 'NZ-MED2-2025-0891'),
                ('fr',        'Flight Review',                 date(2025, 6, 1),  date(2027, 6, 1), ''),
                ('type',      'Cessna 152',                    date(2021, 4, 19), None,             ''),
                ('type',      'PA38 Tomahawk',                 date(2022, 8, 30), None,             ''),
            ],
        }
        ac_type_map = {}
        for m in members:
            pass  # built below
        from core.models import AircraftType
        ac_types = {t.name: t for t in AircraftType.objects.filter(club=club)}

        ct_map = {ct.code: ct for ct in CredentialType.objects.filter(region='NZ-CAA')}
        created = 0
        for username, creds in INSTR_CREDS.items():
            member = next((m for m in members if m.user.username == username), None)
            if not member:
                continue
            for cred_code, name, issue, expiry, cert_num in creds:
                ct = ct_map.get(cred_code)
                if not ct:
                    continue
                ac_type = ac_types.get(name) if cred_code == 'type' else None
                lookup = dict(member=member.user, credential_type=ct, name=name)
                defaults = dict(issue_date=issue, expiry_date=expiry, certificate_number=cert_num)
                if ac_type:
                    defaults['aircraft_type'] = ac_type
                _, c = MemberCredential.objects.get_or_create(**lookup, defaults=defaults)
                if c:
                    created += 1
        self.stdout.write(f"  Instructor credentials: {created} created")

    def _setup_instructor_availability(self, club, members):
        """Give every rostered instructor full-week, all-day availability.
        Without availability windows the off-roster check treats an instructor
        as unavailable and falsely flags all their bookings on the Attention
        page. Idempotent."""
        from core.models import InstructorAvailability
        instructors = [m for m in members if m.role and m.role.name == "Instructor"]
        created = 0
        for m in instructors:
            for weekday in range(7):  # Mon (0) .. Sun (6)
                _, c = InstructorAvailability.objects.get_or_create(
                    club_member=m, recurrence='weekly', weekday=weekday,
                    defaults={'all_day': True},
                )
                if c:
                    created += 1
        self.stdout.write(f"  Instructor availability: {created} windows created")

    def _setup_dominic(self, club, aircraft, members, ft, admin_user):
        today = date.today()
        dom = next((m for m in members if m.user.username == ADMIN_EMAIL), None)
        if not dom:
            return

        ac_map = {ac.registration: ac for ac in aircraft}

        # ── Credentials ──────────────────────────────────────────────────────
        ct_map = {ct.code: ct for ct in CredentialType.objects.filter(region='NZ-CAA')}
        ct_type = ct_map.get('type')
        ct_dlr9 = ct_map.get('dlr9')
        ct_tw   = ct_map.get('tailwheel')
        # Type ratings for fleet aircraft
        if ct_type:
            for reg, label in [("ZK-WAC", "PA38 Tomahawk"), ("ZK-TAW", "Cessna 152"),
                                ("ZK-BCX", "Cessna 172S")]:
                ac = ac_map.get(reg)
                if ac:
                    MemberCredential.objects.get_or_create(
                        member=dom.user, credential_type=ct_type, aircraft_type=ac.aircraft_type,
                        defaults={'name': label, 'issue_date': date(2021, 3, 15)},
                    )
            # PA28 — not in current fleet, name-only
            MemberCredential.objects.get_or_create(
                member=dom.user, credential_type=ct_type, name='Piper PA28 Warrior',
                defaults={'issue_date': date(2019, 8, 20)},
            )
        # DLR9 medical — expires 11 months 15 days from today
        if ct_dlr9:
            med_m = today.month + 11
            med_expiry = date(today.year + (med_m - 1) // 12, (med_m - 1) % 12 + 1, today.day) \
                         + timedelta(days=15)
            MemberCredential.objects.get_or_create(
                member=dom.user, credential_type=ct_dlr9,
                defaults={
                    'issue_date': date(today.year - 2, today.month, today.day),
                    'expiry_date': med_expiry,
                    'certificate_number': 'NZ-DLR9-2024-04521',
                },
            )
        # Tailwheel endorsement
        if ct_tw:
            MemberCredential.objects.get_or_create(
                member=dom.user, credential_type=ct_tw,
                defaults={'issue_date': date(2018, 5, 10)},
            )

        # ── Bookings ─────────────────────────────────────────────────────────
        wac = ac_map.get("ZK-WAC")
        bcx = ac_map.get("ZK-BCX")
        for d_offset, ac, ftype_code, hour in [(3, wac, "SOLO", 14), (7, bcx, "XC", 9)]:
            if not ac:
                continue
            d = today + timedelta(days=d_offset)
            t = datetime(d.year, d.month, d.day, hour, 30, tzinfo=NZ)
            if not Booking.objects.filter(club=club, aircraft=ac,
                                          member=dom, scheduled_start=t).exists():
                Booking.objects.create(
                    club=club, aircraft=ac, member=dom,
                    flight_type=ft.get(ftype_code), instructor=None,
                    scheduled_start=t, scheduled_end=t + timedelta(minutes=90),
                    status=BookingStatus.CONFIRMED, created_by=admin_user,
                )

        # ── Unpaid per-flight invoices (mobile-app demo) ──────────────────────
        # Dominic usually pays by credit, but a couple of flights went to invoice
        # (no card on the day → bank transfer): one current, one overdue. Each is
        # a real completed flight so the mobile invoice sheet shows full detail.
        if not Invoice.objects.filter(club=club, member=dom).exists():
            terms = club.config.payment_terms_days or 14
            inv_num = Invoice.objects.filter(club=club).count() + 1

            # (reg, days_ago, flight_type, hours, [(label, item_type, amount)])
            demo_flights = [
                ("ZK-BCX", 6, "XC", Decimal("1.4"), [
                    ("Four-seat surcharge (C172)",                "surcharge", Decimal("75.00")),
                    ("Landing fee — NZMS Masterton (full stop)",  "landing",   Decimal("20.00")),
                ]),
                ("ZK-WAC", 20, "SOLO", Decimal("1.2"), [
                    ("Landing fee — NZMS Masterton (full stop)",  "landing",   Decimal("20.00")),
                ]),
            ]
            for reg, days_ago, ftype_code, hours, extras in demo_flights:
                ac = ac_map.get(reg)
                if not ac:
                    continue
                d = today - timedelta(days=days_ago)
                start = datetime(d.year, d.month, d.day, 10, 0, tzinfo=NZ)
                booking = Booking.objects.create(
                    club=club, aircraft=ac, member=dom,
                    flight_type=ft.get(ftype_code), instructor=None,
                    scheduled_start=start, scheduled_end=start + timedelta(minutes=90),
                    status=BookingStatus.COMPLETED, created_by=admin_user,
                    departed_at=start + timedelta(minutes=5),
                    arrived_at=start + timedelta(minutes=int(hours * 60) + 5),
                )
                hire_rate = HIRE_RATE.get(reg, Decimal("185"))
                hire_amt  = (hire_rate * hours).quantize(Decimal("0.01"))
                total = hire_amt + sum((amt for _, _, amt in extras), Decimal("0"))

                start_reading = Decimal(str(ac.hobbs_initial or "1000"))
                fc = FlightCompletion.objects.create(
                    booking=booking, outcome="completed",
                    actual_flight_hours=hours,
                    hobbs_start=start_reading, hobbs_end=start_reading + hours,
                    total_charge=total, payment_method="invoice",
                    amount_paid=Decimal("0"), paid_at=None, logged_by=admin_user,
                )
                FlightChargeItem.objects.create(
                    flight_completion=fc, item_type="hire",
                    description=f"{reg} hire", amount=hire_amt)
                for label, itype, amt in extras:
                    FlightChargeItem.objects.create(
                        flight_completion=fc, item_type=itype,
                        description=label, amount=amt)

                inv = Invoice.objects.create(
                    club=club, member=dom, flight_completion=fc,
                    invoice_number=inv_num,
                    issue_date=d, due_date=d + timedelta(days=terms),
                    description=f"Flight hire — {reg} {d.strftime('%d %b %Y')}",
                    status="sent", gst_rate=Decimal("15"), amount_paid=Decimal("0"),
                    sent_at=datetime(d.year, d.month, d.day, 17, 0, tzinfo=NZ),
                    created_by=admin_user,
                )
                inv_num += 1
                InvoiceLineItem.objects.create(
                    invoice=inv, description=f"{reg} hire",
                    quantity=hours, unit="hrs", rate=hire_rate, amount=hire_amt,
                    sort_order=0)
                for i, (label, _itype, amt) in enumerate(extras, start=1):
                    InvoiceLineItem.objects.create(
                        invoice=inv, description=label,
                        quantity=Decimal("1"), unit="", rate=amt, amount=amt,
                        sort_order=i)

        self.stdout.write("  Dominic: credentials, bookings and invoice seeded")

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

        # Suppress all BlockOut signals during seeding — post_save and both
        # m2m_changed handlers all call rescan_bookings() which scans every
        # booking in the club. With hundreds of seeded bookings this hangs.
        from django.db.models.signals import m2m_changed
        from core.models import _blockout_saved, _blockout_aircraft_changed, _blockout_instructors_changed
        post_save.disconnect(_blockout_saved, sender=BlockOut)
        m2m_changed.disconnect(_blockout_aircraft_changed, sender=BlockOut.aircraft.through)
        m2m_changed.disconnect(_blockout_instructors_changed, sender=BlockOut.instructors.through)

        def make_block(label="", **kwargs):
            nonlocal blocks_created
            self.stdout.write(f"  Block-out: {label or kwargs.get('blockout_type', '?')}...")
            b = BlockOut.objects.create(club=club, created_by=admin_user, label=label, **kwargs)
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

        post_save.connect(_blockout_saved, sender=BlockOut)
        m2m_changed.connect(_blockout_aircraft_changed, sender=BlockOut.aircraft.through)
        m2m_changed.connect(_blockout_instructors_changed, sender=BlockOut.instructors.through)
        self.stdout.write(f"  Block-outs: {blocks_created} created")
