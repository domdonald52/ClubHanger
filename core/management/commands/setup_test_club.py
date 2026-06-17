"""
Create (or reset) an isolated club for testing the Paper Aviator data
migration, so import runs never touch the demo/seed data in real clubs.

    python manage.py setup_test_club                  # create + ensure defaults
    python manage.py setup_test_club --reset          # also wipe imported members

Idempotent. Reuses `setup_defaults` for config/roles/types, then stamps the
three built-in roles with their system_role_type so role lookups elsewhere
resolve correctly.
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Club, ClubMember, Role


SYSTEM_ROLE_BY_NAME = {
    'member':     Role.SYSTEM_MEMBER,
    'instructor': Role.SYSTEM_INSTRUCTOR,
    'admin':      Role.SYSTEM_ADMIN,
}


class Command(BaseCommand):
    help = ("Create or reset an isolated club for migration-import testing. "
            "Idempotent — safe to run repeatedly.")

    def add_arguments(self, parser):
        parser.add_argument('--slug', default='migration-test',
                            help="Slug for the test club (default: migration-test).")
        parser.add_argument('--name', default='Migration Test Club',
                            help="Display name used only when the club is first created.")
        parser.add_argument('--reset', action='store_true',
                            help="Delete this club's members (and any users left "
                                 "with no other membership) so imports start clean.")

    @transaction.atomic
    def handle(self, *args, **options):
        slug = options['slug']
        name = options['name']

        club, created = Club.objects.get_or_create(slug=slug, defaults={'name': name})
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nTest club '{club.name}' ({club.slug}): {'created' if created else 'exists'}"))

        # Config, default roles, flight types, member categories.
        call_command('setup_defaults', club=slug)

        # Stamp the built-in roles so system_role_type lookups resolve.
        for role in Role.objects.filter(club=club):
            srt = SYSTEM_ROLE_BY_NAME.get(role.name.strip().lower())
            if srt and role.system_role_type != srt:
                role.system_role_type = srt
                role.save(update_fields=['system_role_type'])
                self.stdout.write(f"  Stamped role '{role.name}' -> system_role_type={srt}")

        if options['reset']:
            self._reset(club)

        self.stdout.write(self.style.SUCCESS(
            f"\nReady. Import into this club with: "
            f"manage.py import_members <file> --club {slug}"))

    def _reset(self, club):
        from django.contrib.auth import get_user_model
        User = get_user_model()

        members = list(ClubMember.objects.filter(club=club).select_related('user'))
        user_ids = [m.user_id for m in members if m.user_id]
        n_members = len(members)
        ClubMember.objects.filter(club=club).delete()

        # Delete users now orphaned by the wipe (no other membership, not staff).
        orphaned = (User.objects.filter(id__in=user_ids)
                    .filter(club_memberships__isnull=True)
                    .exclude(is_staff=True).exclude(is_superuser=True))
        n_users = orphaned.count()
        orphaned.delete()

        self.stdout.write(self.style.WARNING(
            f"  Reset: deleted {n_members} member(s) and {n_users} orphaned user(s)."))
