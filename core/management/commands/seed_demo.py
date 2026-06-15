"""
seed_demo — populate a fresh database with a realistic Wellington Aero Club demo.

Creates:
  • Club + config
  • Roles, membership categories, flight types
  • 6 aircraft (4 × 2-seat, 2 × 4-seat)
  • 4 instructors + 18 members in various states
  • ~800-900 bookings: near-fully-booked for the next 3 weeks, sporadic after

Usage:
  python manage.py seed_demo           # idempotent, skips if bookings exist
  python manage.py seed_demo --reset   # wipe and regenerate bookings
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import random

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from core.models import (
    Club, ClubConfig, Role, MembershipCategory, ClubMember,
    Aircraft, AircraftType, FlightType, Booking, BookingStatus,
)

User = get_user_model()
NZ = ZoneInfo('Pacific/Auckland')

# ── Club ────────────────────────────────────────────────────────────────────

CLUB = {
    "name": "Wellington Aero Club",
    "slug": "wellington-aero-club",
    "phone": "04 388 8000",
    "email": "office@wellingtonaero.example",
    "timezone": "Pacific/Auckland",
    "currency": "NZD",
}

# ── Aircraft fleet ───────────────────────────────────────────────────────────

AIRCRAFT_FLEET = [
    # Two-seaters
    dict(registration="ZK-WAC", type_name="PA38 Tomahawk", seats=2,
         total_time_method="tacho", records_tacho=True,  records_hobbs=False,
         fuel_consumption_per_hour="22.0", hobbs_initial="4210.3"),
    dict(registration="ZK-TAW", type_name="Cessna 152",   seats=2,
         total_time_method="hobbs", records_tacho=False, records_hobbs=True,
         fuel_consumption_per_hour="19.0", hobbs_initial="6831.7"),
    dict(registration="ZK-EAC", type_name="Cessna 152",   seats=2,
         total_time_method="hobbs", records_tacho=False, records_hobbs=True,
         fuel_consumption_per_hour="19.0", hobbs_initial="5102.4"),
    dict(registration="ZK-LAN", type_name="Cessna 152",   seats=2,
         total_time_method="hobbs", records_tacho=False, records_hobbs=True,
         fuel_consumption_per_hour="19.0", hobbs_initial="3984.1"),
    # Four-seaters
    dict(registration="ZK-BCX", type_name="Cessna 172S",  seats=4,
         total_time_method="hobbs", records_tacho=False, records_hobbs=True,
         fuel_consumption_per_hour="34.0", hobbs_initial="2941.6"),
    dict(registration="ZK-FTW", type_name="Piper Warrior", seats=4,
         total_time_method="hobbs", records_tacho=False, records_hobbs=True,
         fuel_consumption_per_hour="30.0", hobbs_initial="3510.2"),
]

# ── Membership categories ────────────────────────────────────────────────────

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

# ── People ───────────────────────────────────────────────────────────────────
# (username, first, last, role, category, standing, sub_expires, resigned_at)

PEOPLE = [
    # ── Admins ──────────────────────────────────────────────────────────────
    ("dominic", "Dominic", "Hales",      "Admin",      "Full Member",    "active", "2027-03-31", None),
    ("alex",    "Alex",    "Reed",       "Admin",      "Commercial Pilot","active","2027-03-31", None),

    # ── Instructors (4) ─────────────────────────────────────────────────────
    ("sean",    "Sean",    "Kemp",       "Instructor", "Instructor",     "active", "2027-03-31", None),
    ("jane",    "Jane",    "Park",       "Instructor", "Instructor",     "active", "2026-12-31", None),
    ("mark",    "Mark",    "Thomson",    "Instructor", "Instructor",     "active", "2027-03-31", None),
    ("kate",    "Kate",    "Wilson",     "Instructor", "Instructor",     "active", "2027-03-31", None),

    # ── Active flying members ────────────────────────────────────────────────
    ("mike",    "Mike",    "Lowe",       "Member",     "Student Pilot",  "active", "2027-03-31", None),
    ("rita",    "Rita",    "Singh",      "Member",     "Full Member",    "active", "2027-03-31", None),
    ("hamish",  "Hamish",  "McKenzie",   "Member",     "Commercial Pilot","active","2027-03-31", None),
    ("emma",    "Emma",    "Bradley",    "Member",     "Student Pilot",  "active", "2027-03-31", None),
    ("chris",   "Chris",   "Park",       "Member",     "Full Member",    "active", "2027-03-31", None),
    ("sophie",  "Sophie",  "Nguyen",     "Member",     "Student Pilot",  "active", "2027-03-31", None),
    ("raj",     "Raj",     "Patel",      "Member",     "Student Pilot",  "active", "2027-03-31", None),
    ("anna",    "Anna",    "Fischer",    "Member",     "Student Pilot",  "active", "2027-03-31", None),
    ("ben",     "Ben",     "Walker",     "Member",     "Full Member",    "active", "2027-03-31", None),
    ("james",   "James",   "Tahi",       "Member",     "Student Pilot",  "active", "2027-03-31", None),
    ("aroha",   "Aroha",   "Williams",   "Member",     "Full Member",    "active", "2027-03-31", None),
    ("david",   "David",   "Morrison",   "Member",     "Full Member",    "active", "2027-03-31", None),
    ("lisa",    "Lisa",    "Chen",       "Member",     "Student Pilot",  "active", "2027-03-31", None),
    ("paulo",   "Paulo",   "Ferreira",   "Member",     "Full Member",    "active", "2027-03-31", None),
    ("tom",     "Tom",     "Hargreaves", "Member",     "Student Pilot",  "active", "2027-03-31", None),

    # ── Edge-case members (for UI demos) ────────────────────────────────────
    ("sarah",   "Sarah",   "Williams",   "Member",     "Commercial Pilot","active","2026-07-05", None),   # expiring soon
    ("grace",   "Grace",   "Okafor",     "Member",     "Student Pilot",  "pending",None,         None),   # pending ratification
    ("bob",     "Bob",     "Morris",     "Member",     "Life Member (Flying)","resigned","2025-12-31","2025-12-15"),
]

DEFAULT_PASSWORD = "clubhangar2026"

# ── Booking generation config ────────────────────────────────────────────────

# Slot start times (HH, MM) — 90-minute blocks, 08:00 – 17:00
SLOTS = [(8,0),(9,30),(11,0),(12,30),(14,0),(15,30)]
SLOT_MINS = 90

# Fill rates per context
FILL = {
    # (is_two_seat, is_weekday, period)
    (True,  True,  "busy"):     0.90,
    (True,  False, "busy"):     0.78,
    (False, True,  "busy"):     0.68,
    (False, False, "busy"):     0.58,
    (True,  True,  "past"):     0.88,
    (True,  False, "past"):     0.75,
    (False, True,  "past"):     0.65,
    (False, False, "past"):     0.55,
    (True,  True,  "sparse"):   0.28,
    (True,  False, "sparse"):   0.20,
    (False, True,  "sparse"):   0.18,
    (False, False, "sparse"):   0.12,
}


class Command(BaseCommand):
    help = "Seed a full demo dataset for Wellington Aero Club."

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset', action='store_true',
            help='Delete existing bookings and regenerate (other data is always upserted)'
        )

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(42)  # deterministic output

        club        = self._setup_club()
        roles, cats = self._setup_taxonomy(club)
        aircraft    = self._setup_fleet(club)
        members     = self._setup_people(club, roles, cats)
        ft          = self._setup_flight_types(club)

        existing = Booking.objects.filter(club=club).count()
        if existing and not options['reset']:
            self.stdout.write(
                f"  {existing} bookings already exist — skipping generation "
                "(use --reset to regenerate)"
            )
        else:
            if options['reset']:
                deleted, _ = Booking.objects.filter(club=club).delete()
                self.stdout.write(f"  Deleted {deleted} existing bookings")
            self._generate_bookings(club, aircraft, members, ft)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Login: dominic / {DEFAULT_PASSWORD}\n"
            f"Management app: /manage/{club.slug}/\n"
            f"Mobile app:     /app/{club.slug}/"
        ))

    # ── Setup helpers ────────────────────────────────────────────────────────

    def _setup_club(self):
        club, created = Club.objects.get_or_create(slug=CLUB["slug"], defaults=CLUB)
        self.stdout.write(f"Club: {club.name} ({'created' if created else 'exists'})")

        from datetime import time as dtime
        ClubConfig.objects.get_or_create(
            club=club,
            defaults={
                "default_booking_duration": 90,
                "time_slot_interval": 30,
                "operating_hours_start": dtime(8, 0),
                "operating_hours_end": dtime(18, 0),
            },
        )
        return club

    def _setup_taxonomy(self, club):
        roles = {}
        for name in ROLES:
            r, _ = Role.objects.get_or_create(club=club, name=name)
            roles[name] = r

        cats = {}
        for name, is_member in MEMBER_CATEGORIES:
            c, _ = MembershipCategory.objects.get_or_create(
                club=club, name=name, defaults={"is_member": is_member}
            )
            cats[name] = c

        self.stdout.write(f"  Roles: {len(roles)}  Categories: {len(cats)}")
        return roles, cats

    def _setup_fleet(self, club):
        aircraft_objs = []
        for spec in AIRCRAFT_FLEET:
            type_name = spec.pop("type_name")
            ac_type, _ = AircraftType.objects.get_or_create(club=club, name=type_name)
            ac, created = Aircraft.objects.get_or_create(
                club=club, registration=spec["registration"],
                defaults={**spec, "aircraft_type": ac_type},
            )
            aircraft_objs.append(ac)
            self.stdout.write(
                f"  {'Created' if created else 'Exists':8} {ac.registration} "
                f"({type_name}, {ac.seats}-seat)"
            )
        return aircraft_objs

    def _setup_flight_types(self, club):
        ft_map = {}
        for spec in FLIGHT_TYPES:
            ft, _ = FlightType.objects.get_or_create(
                club=club, code=spec["code"], defaults=spec
            )
            ft_map[spec["code"]] = ft
        return ft_map

    def _setup_people(self, club, roles, cats):
        from datetime import date as ddate
        members = []
        for username, first, last, role_name, cat_name, standing, exp_str, resigned_str in PEOPLE:
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

            exp      = ddate.fromisoformat(exp_str)      if exp_str      else None
            resigned = ddate.fromisoformat(resigned_str) if resigned_str else None

            member, _ = ClubMember.objects.get_or_create(
                user=user, club=club,
                defaults={
                    "role": roles[role_name],
                    "membership_category": cats.get(cat_name),
                    "standing": standing,
                    "subscription_expires": exp,
                    "resigned_at": resigned,
                },
            )
            # always upsert standing/role in case we re-run
            ClubMember.objects.filter(pk=member.pk).update(
                role=roles[role_name],
                membership_category=cats.get(cat_name),
                standing=standing,
                subscription_expires=exp,
                resigned_at=resigned,
            )
            members.append(member)

        self.stdout.write(f"  People: {len(members)}")
        return members

    # ── Booking generation ───────────────────────────────────────────────────

    def _generate_bookings(self, club, aircraft, members, ft):
        today = datetime.now(tz=NZ).date()

        instructors = [
            m for m in members
            if m.role and m.role.name == "Instructor"
        ]
        flying_members = [
            m for m in members
            if m.standing == "active"
            and m.role and m.role.name == "Member"
        ]
        admin_user = User.objects.filter(is_superuser=True).first()

        two_seaters  = [a for a in aircraft if a.seats == 2]
        four_seaters = [a for a in aircraft if a.seats == 4]

        # instructor_busy[day][user_pk] = [(start_dt, end_dt), ...]
        instructor_busy = defaultdict(lambda: defaultdict(list))
        # member_busy[day][member_pk] = [(start_dt, end_dt), ...]
        member_busy = defaultdict(lambda: defaultdict(list))

        bookings = []

        # Past 2 weeks + today + 6 weeks ahead
        for day_offset in range(-14, 43):
            day = today + timedelta(days=day_offset)
            is_weekday = day.weekday() < 5

            if day_offset < 0:
                period = "past"
            elif day_offset <= 21:
                period = "busy"
            else:
                period = "sparse"

            day_key = day.isoformat()
            now_nz  = datetime.now(tz=NZ)

            for ac in aircraft:
                is_two = ac.seats == 2
                fill   = FILL[(is_two, is_weekday, period)]

                for slot_h, slot_m in SLOTS:
                    if random.random() > fill:
                        continue  # natural gap

                    slot_start = datetime(day.year, day.month, day.day,
                                         slot_h, slot_m, tzinfo=NZ)
                    slot_end   = slot_start + timedelta(minutes=SLOT_MINS)

                    # Decide dual vs solo (two-seaters heavily dual, 4-seaters mixed)
                    dual_prob = 0.75 if is_two else 0.45
                    want_dual = random.random() < dual_prob

                    # Assign instructor if dual
                    instructor_user = None
                    if want_dual:
                        instructor_user = self._pick_instructor(
                            instructors, slot_start, slot_end,
                            day_key, instructor_busy
                        )

                    flight_type = (
                        ft["DUAL"] if instructor_user
                        else (ft["SOLO"] if is_two else random.choice([ft["SOLO"], ft["XC"]]))
                    )

                    # Pick an available member
                    member = self._pick_member(
                        flying_members, slot_start, slot_end,
                        day_key, member_busy
                    )
                    if member is None:
                        continue

                    # Status
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

                    confirmed_by = (
                        admin_user
                        if status in (BookingStatus.CONFIRMED, BookingStatus.COMPLETED,
                                      BookingStatus.DEPARTED)
                        else None
                    )
                    confirmed_at = (
                        slot_start - timedelta(hours=random.randint(1, 48))
                        if confirmed_by else None
                    )

                    b = Booking(
                        club=club,
                        aircraft=ac,
                        member=member,
                        flight_type=flight_type,
                        instructor=instructor_user,
                        scheduled_start=slot_start,
                        scheduled_end=slot_end,
                        status=status,
                        created_by=admin_user,
                        confirmed_by=confirmed_by,
                        confirmed_at=confirmed_at,
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
            f"({date.today() - timedelta(days=14)} → {today + timedelta(days=42)})"
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
