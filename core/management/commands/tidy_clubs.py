"""
tidy_clubs — staging cleanup utility.

Two operations, combinable in one run:
  --delete SLUG   Delete a club entirely (all data + the club record). Repeatable.
  --empty  SLUG   Empty a club back to a clean shell — keeps its slug, name and
                  settings (theme/billing/config) and re-adds a chosen admin, but
                  removes every member, aircraft, booking, invoice, etc.

DRY-RUN BY DEFAULT: nothing is committed unless you pass --execute. The dry-run
still performs the work inside a rolled-back transaction, so it also proves the
delete will succeed (no protected-reference surprises).

  # preview
  python manage.py tidy_clubs --delete wac-demo --empty wellington-aero-club --admin dom.donald@gmail.com
  # apply
  python manage.py tidy_clubs --delete wac-demo --empty wellington-aero-club --admin dom.donald@gmail.com --execute

Note: orphaned demo *users* (with no remaining club membership) are left in place
— they're invisible in the app and harmless.
"""
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.db import transaction
from django.contrib.auth import get_user_model

from core.models import (
    Club, ClubConfig, Role, ClubMember,
    Booking, FlightCompletion, FlightSegment, FlightPayment,
    OccurrenceReport, Aircraft,
)

User = get_user_model()


def _clear_protect_blockers(club):
    """Delete the handful of models that PROTECT-reference club-scoped objects,
    so a subsequent club.delete() can cascade everything else cleanly."""
    FlightSegment.objects.filter(member__club=club).delete()
    FlightPayment.objects.filter(member__club=club).delete()
    OccurrenceReport.objects.filter(club=club).delete()
    FlightCompletion.objects.filter(booking__club=club).delete()
    Booking.objects.filter(club=club).delete()
    Aircraft.objects.filter(club=club).delete()


class Command(BaseCommand):
    help = ("Delete clubs entirely and/or empty a club to a clean shell. "
            "Dry-run unless --execute is given.")

    def add_arguments(self, parser):
        parser.add_argument('--delete', action='append', default=[], metavar='SLUG',
                            help='Club slug to delete entirely (repeatable).')
        parser.add_argument('--empty', default=None, metavar='SLUG',
                            help='Club slug to empty (keeps slug/name/settings).')
        parser.add_argument('--admin', default=None, metavar='EMAIL',
                            help='Username/email of the admin to keep on the emptied club.')
        parser.add_argument('--execute', action='store_true',
                            help='Apply the changes (otherwise dry-run + rollback).')

    def handle(self, *args, **opts):
        delete_slugs = opts['delete']
        empty_slug   = opts['empty']
        admin_id     = opts['admin']
        execute      = opts['execute']

        if not delete_slugs and not empty_slug:
            raise CommandError("Nothing to do — pass --delete SLUG and/or --empty SLUG.")

        for slug in delete_slugs:
            if not Club.objects.filter(slug=slug).exists():
                raise CommandError(f"--delete: no club with slug '{slug}'.")
        if empty_slug and not Club.objects.filter(slug=empty_slug).exists():
            raise CommandError(f"--empty: no club with slug '{empty_slug}'.")

        admin_user = None
        if empty_slug:
            if not admin_id:
                raise CommandError("--empty requires --admin EMAIL (who keeps access).")
            admin_user = (User.objects.filter(username=admin_id).first()
                          or User.objects.filter(email=admin_id).first())
            if not admin_user:
                raise CommandError(f"--admin: no user with username/email '{admin_id}'.")

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\ntidy_clubs — {'EXECUTE' if execute else 'DRY-RUN (rolls back, no changes)'}"))

        try:
            with transaction.atomic():
                for slug in delete_slugs:
                    club = Club.objects.get(slug=slug)
                    self.stdout.write(
                        f"\nDELETE  {club.name} ({slug}) — "
                        f"{ClubMember.objects.filter(club=club).count()} members, "
                        f"{Aircraft.objects.filter(club=club).count()} aircraft, "
                        f"{Booking.objects.filter(club=club).count()} bookings")
                    _clear_protect_blockers(club)
                    club.delete()
                    self.stdout.write(self.style.SUCCESS("  deleted."))

                if empty_slug:
                    club = Club.objects.get(slug=empty_slug)
                    self.stdout.write(
                        f"\nEMPTY   {club.name} ({empty_slug}) — keeping slug/name/settings; "
                        f"admin = {admin_user.username}")
                    club_vals = Club.objects.filter(slug=empty_slug).values().first()
                    club_vals.pop('id', None)
                    cfg_vals = ClubConfig.objects.filter(club=club).values().first() or {}
                    cfg_vals = {k: v for k, v in cfg_vals.items() if k not in ('id', 'club_id')}

                    _clear_protect_blockers(club)
                    club.delete()

                    new_club = Club.objects.create(**club_vals)
                    call_command('setup_defaults', club=empty_slug, verbosity=0)
                    if cfg_vals:
                        ClubConfig.objects.update_or_create(club=new_club, defaults=cfg_vals)
                    admin_role = Role.objects.filter(club=new_club, name='Admin').first()
                    ClubMember.objects.create(
                        user=admin_user, club=new_club, role=admin_role,
                        standing='active', has_admin_access=True)
                    self.stdout.write(self.style.SUCCESS(
                        f"  emptied; {admin_user.username} re-added as admin."))

                if not execute:
                    self.stdout.write(self.style.WARNING(
                        "\nDRY-RUN complete (validated, rolling back). "
                        "Re-run with --execute to apply."))
                    transaction.set_rollback(True)
        except Exception as e:
            raise CommandError(f"Aborted (nothing changed): {e}")

        if execute:
            self.stdout.write(self.style.SUCCESS("\nDone."))
