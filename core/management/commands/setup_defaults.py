from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Club, ClubConfig, Role, MembershipCategory, ClubMember, FlightType


# Sensible starting points. These are SEED data only — everything here is
# editable in the admin afterwards. Nothing in the app should hardcode these.
DEFAULT_ROLES = ["Admin", "Instructor", "Member"]

# (name, code, is_billable, is_training, is_solo)
DEFAULT_FLIGHT_TYPES = [
    ("Student Dual",       "student_dual",          True,  True,  False),
    ("Student Solo",       "student_solo",           True,  True,  True),
    ("Staff Training Dual","staff_training_dual",    True,  True,  False),
    ("Staff Training Solo","staff_training_solo",    True,  True,  True),
    ("Private Hire",       "private_hire",           True,  False, False),
    ("Ferry Flight",       "ferry_flight",           False, False, False),
]

DEFAULT_MEMBER_CATEGORIES = [
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


class Command(BaseCommand):
    help = "Create default ClubConfig, Roles, and MembershipCategories for clubs that lack them. Idempotent — safe to run repeatedly."

    def add_arguments(self, parser):
        parser.add_argument(
            "--club",
            type=str,
            default=None,
            help="Slug of a specific club. Omit to apply to all clubs.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        slug = options.get("club")
        clubs = Club.objects.filter(slug=slug) if slug else Club.objects.all()

        if not clubs.exists():
            self.stdout.write(self.style.WARNING("No matching clubs found."))
            return

        for club in clubs:
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{club.name} ({club.slug})"))

            # Config
            config, created = ClubConfig.objects.get_or_create(club=club)
            self.stdout.write(
                f"  Config: {'created' if created else 'exists'} "
                f"(hours {config.operating_hours_start:%H:%M}-{config.operating_hours_end:%H:%M}, "
                f"slot {config.time_slot_interval}m, default {config.default_booking_duration}m)"
            )

            # Roles
            for name in DEFAULT_ROLES:
                role, created = Role.objects.get_or_create(club=club, name=name)
                self.stdout.write(f"  Role '{name}': {'created' if created else 'exists'}")

            # Flight types
            for name, code, is_billable, is_training, is_solo in DEFAULT_FLIGHT_TYPES:
                ft, created = FlightType.objects.get_or_create(
                    club=club, code=code,
                    defaults={"name": name, "is_billable": is_billable,
                              "is_training": is_training, "is_solo": is_solo}
                )
                self.stdout.write(f"  FlightType '{name}' ({'solo' if is_solo else 'dual/other'}): {'created' if created else 'exists'}")

            # Member categories
            for name, is_member in DEFAULT_MEMBER_CATEGORIES:
                cat, created = MembershipCategory.objects.get_or_create(
                    club=club, name=name, defaults={"is_member": is_member}
                )
                self.stdout.write(
                    f"  Category '{name}' ({'member' if is_member else 'non-member'}): "
                    f"{'created' if created else 'exists'}"
                )

            # Report members whose role is now unset (from the FK migration)
            unset = ClubMember.objects.filter(club=club, role__isnull=True)
            if unset.exists():
                self.stdout.write(self.style.WARNING("  Members with NO role — reassign in admin:"))
                for m in unset:
                    who = m.user.get_username() if m.user else "(non-member)"
                    self.stdout.write(f"    - {who}")

        self.stdout.write(self.style.SUCCESS("\nDone. Edit any of these in the Django admin."))
