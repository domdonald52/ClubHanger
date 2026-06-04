from datetime import time

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

from core.models import (
    Club, ClubConfig, Role, MembershipCategory, ClubMember,
    Aircraft, FlightType,
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
     "total_time_method": "tacho_less_5", "records_tacho": True},
    {"registration": "ZK-TAW", "aircraft_type": "C152", "seats": 2,
     "total_time_method": "hobbs", "records_hobbs": True},
]

FLIGHT_TYPES = [
    {"name": "Student Dual", "code": "DUAL", "is_training": True},
    {"name": "Solo Hire", "code": "SOLO", "is_training": False},
    {"name": "Trial Flight", "code": "TRIAL", "is_training": False},
]

# username, first, last, role_name, category_name
PEOPLE = [
    ("dominic", "Dominic", "Hales", "Admin", "Private Pilot"),
    ("admin2", "Alex", "Reed", "Admin", "Commercial Pilot"),
    ("sean", "Sean", "Kemp", "Instructor", "Instructor"),
    ("jane", "Jane", "Park", "Instructor", "Instructor"),
    ("mike", "Mike", "Lowe", "Member", "Student Pilot"),
    ("rita", "Rita", "Singh", "Member", "Private Pilot"),
]

DEFAULT_PASSWORD = "changeme123"


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

        # Aircraft
        for spec in AIRCRAFT:
            ac, created = Aircraft.objects.get_or_create(
                club=club, registration=spec["registration"], defaults=spec
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
        for username, first, last, role_name, cat_name in PEOPLE:
            user, u_created = User.objects.get_or_create(
                username=username,
                defaults={"first_name": first, "last_name": last},
            )
            if u_created:
                user.set_password(DEFAULT_PASSWORD)
                # Admins get Django staff/superuser so they can reach /admin/
                if role_name == "Admin":
                    user.is_staff = True
                    user.is_superuser = True
                user.save()

            member, m_created = ClubMember.objects.get_or_create(
                user=user, club=club,
                defaults={
                    "role": roles[role_name],
                    "membership_category": categories.get(cat_name),
                    "standing": "active",
                },
            )
            if not m_created:
                member.role = roles[role_name]
                member.membership_category = categories.get(cat_name)
                member.standing = "active"
                member.save()

            self.stdout.write(
                f"  {username} ({role_name}): "
                f"{'created' if (u_created or m_created) else 'updated'}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Demo users password: '{DEFAULT_PASSWORD}'. "
            "Edit anything in the Django admin."
        ))
