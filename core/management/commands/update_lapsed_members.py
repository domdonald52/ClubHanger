from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from core.models import Club, ClubMember, MembershipHistoryEntry


class Command(BaseCommand):
    help = (
        "Mark active members whose subscription has expired past the grace period as lapsed. "
        "Grace period is read from ClubConfig.lapse_grace_days (default 60). "
        "Run nightly via cron: 0 6 * * * python manage.py update_lapsed_members"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Show who would be lapsed without making changes."
        )
        parser.add_argument(
            '--grace-days', type=int, default=None,
            help="Override grace days from ClubConfig. Defaults to ClubConfig.lapse_grace_days per club."
        )
        parser.add_argument(
            '--club', type=str, default=None,
            help="Only process one club (by slug)."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run   = options['dry_run']
        grace_override = options['grace_days']
        club_slug = options['club']

        clubs = Club.objects.all()
        if club_slug:
            clubs = clubs.filter(slug=club_slug)

        total = 0
        today = timezone.localdate()

        for club in clubs:
            grace = grace_override
            if grace is None:
                try:
                    grace = club.config.lapse_grace_days
                except Exception:
                    grace = 60
            cutoff = today - timedelta(days=grace)

            candidates = ClubMember.objects.filter(
                club=club,
                standing=ClubMember.STANDING_ACTIVE,
                subscription_expires__lt=cutoff,
            ).select_related('user')

            for m in candidates:
                name = m.user.get_full_name() if m.user else '—'
                days_over = (today - m.subscription_expires).days
                self.stdout.write(
                    f"  {'[DRY RUN] Would lapse' if dry_run else 'Lapsing'}: "
                    f"{name} @ {club.name} "
                    f"(expired {m.subscription_expires}, {days_over}d ago)"
                )
                if not dry_run:
                    m.standing   = ClubMember.STANDING_LAPSED
                    m.resigned_at = m.resigned_at or today
                    m.save(update_fields=['standing', 'resigned_at'])
                    if m.user and m.user.is_active:
                        m.user.is_active = False
                        m.user.save(update_fields=['is_active'])
                    MembershipHistoryEntry.objects.create(
                        club_member=m,
                        event_type=MembershipHistoryEntry.EVENT_STANDING_CHANGE,
                        old_value=ClubMember.STANDING_ACTIVE,
                        new_value=ClubMember.STANDING_LAPSED,
                        note=f'Auto-lapsed — subscription expired {m.subscription_expires} '
                             f'({days_over} days ago, grace period {grace} days)',
                    )
                total += 1

        if total == 0:
            self.stdout.write(self.style.SUCCESS("No members to lapse."))
        elif dry_run:
            self.stdout.write(self.style.WARNING(f"{total} member(s) would be lapsed."))
        else:
            self.stdout.write(self.style.SUCCESS(f"{total} member(s) lapsed."))
