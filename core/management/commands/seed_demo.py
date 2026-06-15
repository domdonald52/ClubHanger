from datetime import time, date

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

from core.models import (
    Club, ClubConfig, Role, MembershipCategory, ClubMember,
    Aircraft, AircraftType, FlightType,
)

User = get_user_model()

CLUB = {
    "name": "Wellington Aero Club",
    "slug": "wellington-aero-club",
    "phone": "04 388 8000",
    "email": "office@wellingtonaero.example",
    "timezone": "Pacific/Auckland",
    "currency": "NZD",
}

ROLES = ["Admin", "Instructor", "Member"]

MEMBER_CATEGORIES = [
    ("Instructor", True),
    ("Private Pilot", True),
    ("Commercial Pilot", True),
    ("Student Pilot", True),
    ("Life Member (Flying)", True),
    ("Life Member (Non-Flying)", True),
    ("Gateway Project", False),
    ("Young Eagles", False),
    ("Trial Flight", False),
]

AIRCRAFT = [
    {"registration": "ZK-WAC", "aircraft_type": "PA38", "seats": 2,
     "total_time_method": "tacho", "records_tacho": True},
    {"registration": "ZK-TAW", "aircraft_type": "C152", "seats": 2,
     "total_time_method": "hobbs", "records_hobbs": True},
]

FLIGHT_TYPES = [
    {"name": "Student Dual", "code": "DUAL", "is_training": True},
    {"name": "Solo Hire", "code": "SOLO", "is_training": False},
    {"name": "Trial Flight", "code": "TRIAL", "is_training": False},
]

# username, first, last, role_name, category_name, standing, subscription_expires, resigned_at
PEOPLE = [
    # Admins — no subscription expiry needed
    ("dominic", "Dominic", "Hales",   "Admin",      "Private Pilot",       "active",    None,         None),
    ("admin2",  "Alex",    "Reed",    "Admin",      "Commercial Pilot",    "active",    "2027-03-31", None),
    # Instructors
    ("sean",    "Sean",    "Kemp",    "Instructor", "Instructor",          "active",    "2027-03-31", None),
    ("jane",    "Jane",    "Park",    "Instructor", "Instructor",          "active",    "2026-12-31", None),
    # Standard members — current
    ("mike",    "Mike",    "Lowe",    "Member",     "Student Pilot",       "active",    "2027-03-31", None),
    ("rita",    "Rita",    "Singh",   "Member",     "Private Pilot",       "active",    "2027-03-31", None),
    # Expiring soon (within 30 days of today)
    ("sarah",   "Sarah",   "Williams","Member",     "Commercial Pilot",    "active",    "2026-07-05", None),
    # In grace period (expired, but within 60-day grace window)
    ("paulo",   "Paulo",   "Ferreira","Member",     "Private Pilot",       "active",    "2026-05-10", None),
    # Past grace period (expired > 60 days ago — auto-lapse not yet run)
    ("tom",     "Tom",     "Chen",    "Member",     "Private Pilot",       "active",    "2026-03-01", None),
    # Non-standard standings
    ("james",   "James",   "Okafor",  "Member",     "Student Pilot",       "suspended", "2026-03-31", None),
    ("lucy",    "Lucy",    "Hart",    "Member",     "Private Pilot",       "pending",   None,         None),
    ("bob",     "Bob",     "Morris",  "Member",     "Life Member (Flying)","resigned",  "2025-12-31", "2025-12-15"),
]

DEFAULT_PASSWORD = "clubhangar2026"


class Command(BaseCommand):
    help = (
        "Seed a full demo dataset: Wellington Aero Club, config, roles, "
        "categories, aircraft, flight types, instructors and members. "
        "Idempotent — safe to re-run."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        # Club
        club, created = Club.objects.get_or_create(
            slug=CLUB["slug"], defaults=CLUB
        )
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Club: {club.name} ({'created' if created else 'exists'})"
        ))

        # Config
        config, created = ClubConfig.objects.get_or_create(
            club=club,
            defaults={
                "default_booking_duration": 90,
                "time_slot_interval": 30,
                "operating_hours_start": time(7, 0),
                "operating_hours_end": time(21, 0),
            },
        )
        self.stdout.write(f"  Config: {'created' if created else 'exists'}")

        # Roles
        roles = {}
        for name in ROLES:
            role, created = Role.objects.get_or_create(club=club, name=name)
            roles[name] = role
            self.stdout.write(f"  Role '{name}': {'created' if created else 'exists'}")

        # Categories
        categories = {}
        for name, is_member in MEMBER_CATEGORIES:
            cat, created = MembershipCategory.objects.get_or_create(
                club=club, name=name, defaults={"is_member": is_member}
            )
            categories[name] = cat
            self.stdout.write(
                f"  Category '{name}': {'created' if created else 'exists'}"
            )

        # Aircraft types (ensure they exist before aircraft)
        for spec in AIRCRAFT:
            type_name = spec["aircraft_type"]
            AircraftType.objects.get_or_create(club=club, name=type_name)

        # Aircraft
        for spec in AIRCRAFT:
            type_name = spec.pop("aircraft_type", None)
            ac_type = AircraftType.objects.filter(club=club, name=type_name).first() if type_name else None
            ac, created = Aircraft.objects.get_or_create(
                club=club, registration=spec["registration"],
                defaults={**spec, 'aircraft_type': ac_type}
            )
            self.stdout.write(
                f"  Aircraft {spec['registration']}: {'created' if created else 'exists'}"
            )

        # Flight types
        for spec in FLIGHT_TYPES:
            ft, created = FlightType.objects.get_or_create(
                club=club, code=spec["code"], defaults=spec
            )
            self.stdout.write(
                f"  Flight type {spec['code']}: {'created' if created else 'exists'}"
            )

        # People (users + memberships)
        for username, first, last, role_name, cat_name, standing, exp_str, resigned_str in PEOPLE:
            user, u_created = User.objects.get_or_create(
                username=username,
                defaults={"first_name": first, "last_name": last},
            )
            if u_created:
                user.set_password(DEFAULT_PASSWORD)
                if role_name == "Admin":
                    user.is_staff = True
                    user.is_superuser = True
                user.save()

            exp_date     = date.fromisoformat(exp_str)     if exp_str     else None
            resigned_date = date.fromisoformat(resigned_str) if resigned_str else None

            member, m_created = ClubMember.objects.get_or_create(
                user=user, club=club,
                defaults={
                    "role": roles[role_name],
                    "membership_category": categories.get(cat_name),
                    "standing": standing,
                    "subscription_expires": exp_date,
                    "resigned_at": resigned_date,
                },
            )
            if not m_created:
                member.role = roles[role_name]
                member.membership_category = categories.get(cat_name)
                member.standing = standing
                member.subscription_expires = exp_date
                member.resigned_at = resigned_date
                member.save(update_fields=['role', 'membership_category', 'standing',
                                           'subscription_expires', 'resigned_at'])

            self.stdout.write(
                f"  {username} ({role_name}, {standing}): "
                f"{'created' if (u_created or m_created) else 'updated'}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Demo users password: '{DEFAULT_PASSWORD}'. "
            "Edit anything in the Django admin."
        ))
