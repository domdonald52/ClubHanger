from datetime import date
from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import ClubMember


class Command(BaseCommand):
    help = (
        "Mark active members whose subscription has expired as lapsed. "
        "Run nightly via cron: 0 6 * * * python manage.py update_lapsed_members"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Show who would be lapsed without making changes."
        )
        parser.add_argument(
            '--grace-days', type=int, default=0,
            help="Number of days after subscription_expires before lapsing (default 0)."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        grace = options['grace_days']
        cutoff = date.today()

        candidates = ClubMember.objects.filter(
            standing='active',
            subscription_expires__lt=cutoff,
        ).select_related('user', 'club')

        if not candidates.exists():
            self.stdout.write(self.style.SUCCESS("No members to lapse."))
            return

        count = 0
        for m in candidates:
            name = m.user.get_full_name() if m.user else '—'
            self.stdout.write(
                f"  {'[DRY RUN] Would lapse' if dry_run else 'Lapsing'}: "
                f"{name} @ {m.club.name} "
                f"(subscription_expires={m.subscription_expires})"
            )
            if not dry_run:
                m.standing = 'lapsed'
                m.save(update_fields=['standing'])
            count += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(f"{count} member(s) would be lapsed."))
        else:
            self.stdout.write(self.style.SUCCESS(f"{count} member(s) lapsed."))
