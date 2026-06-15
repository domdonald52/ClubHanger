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
    "name": "Kapiti Aero Club",
    "slug": "kapiti-aero-club",
    "phone": "04 296 9000",
    "email": "office@kapitiaero.example",
    "timezone": "Pacific/Auckland",
    "currency": "NZD",
}

ROLES = ["Admin", "Instructor", "Member"]

MEMBER_CATEGORIES = [
    ("Instructor", True),
    ("Private Pilot", True),
    ("Student Pilot", True),
    ("Life Member", True),
    ("Trial Flight", False),
    ("Young Eagles", False),
]

AIRCRAFT = [
    {"registration": "ZK-KAC", "aircraft_type": "C172", "seats": 4,
     "total_time_method": "hobbs", "records_hobbs": True},
    {"registration": "ZK-KAP", "aircraft_type": "PA28", "seats": 4,
     "total_time_method": "tacho", "records_tacho": True},
]

FLIGHT_TYPES = [
    {"name": "Student Dual", "code": "DUAL", "is_training": True},
    {"name": "Solo Hire", "code": "SOLO", "is_training": False},
    {"name": "Trial Flight", "code": "TRIAL", "is_training": False},
]

# username, first, last, role_name, category_name, standing, subscription_expires
# dominic is a member of both clubs for testing the multi-club switch
PEOPLE = [
    ("dominic",  "Dominic", "Hales",   "Admin",      "Private Pilot", "active", None),
    ("kapiti_a", "Karen",   "Taupo",   "Admin",      "Private Pilot", "active", "2027-03-31"),
    ("kapiti_i", "Rob",     "Parata",  "Instructor", "Instructor",    "active", "2027-03-31"),
    ("kapiti_m", "Wiremu",  "Ngata",   "Member",     "Student Pilot", "active", "2027-03-31"),
    ("kapiti_m2","Aroha",   "Tane",    "Member",     "Private Pilot", "active", "2027-06-30"),
]

DEFAULT_PASSWORD = "clubhangar2026"


class Command(BaseCommand):
    help = (
        "Seed Kapiti Aero Club demo data. Idempotent — safe to re-run. "
        "Upload the club logo via Admin > Clubs after running."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        club, created = Club.objects.get_or_create(
            slug=CLUB["slug"], defaults=CLUB
        )
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Club: {club.name} ({'created' if created else 'exists'})"
        ))

        config, created = ClubConfig.objects.get_or_create(
            club=club,
            defaults={
                "default_booking_duration": 90,
                "time_slot_interval": 30,
                "operating_hours_start": time(7, 0),
                "operating_hours_end": time(20, 0),
            },
        )
        self.stdout.write(f"  Config: {'created' if created else 'exists'}")

        roles = {}
        for name in ROLES:
            role, created = Role.objects.get_or_create(club=club, name=name)
            roles[name] = role
            self.stdout.write(f"  Role '{name}': {'created' if created else 'exists'}")

        categories = {}
        for name, is_member in MEMBER_CATEGORIES:
            cat, created = MembershipCategory.objects.get_or_create(
                club=club, name=name, defaults={"is_member": is_member}
            )
            categories[name] = cat
            self.stdout.write(f"  Category '{name}': {'created' if created else 'exists'}")

        for spec in AIRCRAFT:
            AircraftType.objects.get_or_create(club=club, name=spec["aircraft_type"])

        for spec in AIRCRAFT:
            spec = dict(spec)
            type_name = spec.pop("aircraft_type")
            ac_type = AircraftType.objects.filter(club=club, name=type_name).first()
            ac, created = Aircraft.objects.get_or_create(
                club=club, registration=spec["registration"],
                defaults={**spec, 'aircraft_type': ac_type}
            )
            self.stdout.write(f"  Aircraft {spec['registration']}: {'created' if created else 'exists'}")

        for spec in FLIGHT_TYPES:
            ft, created = FlightType.objects.get_or_create(
                club=club, code=spec["code"], defaults=spec
            )
            self.stdout.write(f"  Flight type {spec['code']}: {'created' if created else 'exists'}")

        for username, first, last, role_name, cat_name, standing, exp_str in PEOPLE:
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

            exp_date = date.fromisoformat(exp_str) if exp_str else None

            member, m_created = ClubMember.objects.get_or_create(
                user=user, club=club,
                defaults={
                    "role": roles[role_name],
                    "membership_category": categories.get(cat_name),
                    "standing": standing,
                    "subscription_expires": exp_date,
                },
            )
            if not m_created:
                member.role = roles[role_name]
                member.membership_category = categories.get(cat_name)
                member.standing = standing
                member.subscription_expires = exp_date
                member.save(update_fields=['role', 'membership_category', 'standing', 'subscription_expires'])

            self.stdout.write(
                f"  {username} ({role_name}, {standing}): "
                f"{'created' if (u_created or m_created) else 'updated'}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Upload the club logo at Admin > Clubs > {club.name}. "
            f"User password: '{DEFAULT_PASSWORD}'."
        ))
