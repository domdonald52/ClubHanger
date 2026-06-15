"""
Management command: send_notifications

Run daily (e.g. via Railway cron at 07:00 NZST):
    venv/bin/python manage.py send_notifications [--dry-run]

Sends:
  - Booking reminders for flights tomorrow
  - Credential expiry warnings (30 days and 7 days out)
  - Subscription expiry warnings (30 days and 7 days out)
  - Payment reminders for members with negative balances

--dry-run prints what would be sent without actually sending anything.
"""

from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Send daily email notifications — reminders, expiry warnings, payment nudges'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print what would be sent without actually sending emails',
        )

    def handle(self, *args, **options):
        from core.models import Club, Booking, MemberCredential, ClubMember
        from core.email_notifications import (
            booking_reminder,
            credential_expiry_warning,
            subscription_expiry_warning,
            payment_reminder,
        )

        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no emails will be sent'))

        tomorrow = timezone.localdate() + timedelta(days=1)
        sent = 0

        for club in Club.objects.all():
            self.stdout.write(f'\nClub: {club.name}')

            # Booking reminders — confirmed flights tomorrow
            bookings = (Booking.objects
                        .filter(club=club, status='confirmed',
                                scheduled_start__date=tomorrow)
                        .select_related('member__user', 'member__notification_prefs',
                                        'aircraft', 'instructor', 'flight_type'))
            for b in bookings:
                label = f'  Booking reminder → {b.member.user.get_full_name()} ({b.aircraft.registration} {b.scheduled_start:%H:%M})'
                if dry_run:
                    self.stdout.write(label)
                else:
                    booking_reminder(b)
                    self.stdout.write(label)
                sent += 1

            # Credential expiry warnings — 30 days and 7 days out
            for days in (30, 7):
                target = timezone.localdate() + timedelta(days=days)
                creds = (MemberCredential.objects
                         .filter(club_member__club=club, expiry_date=target)
                         .select_related('club_member__user', 'club_member__notification_prefs',
                                         'club_member__club'))
                for cred in creds:
                    label = (f'  Credential expiry ({days}d) → '
                             f'{cred.club_member.user.get_full_name()} '
                             f'({cred.get_credential_type_display()})')
                    if dry_run:
                        self.stdout.write(label)
                    else:
                        credential_expiry_warning(cred.club_member, cred, days)
                        self.stdout.write(label)
                    sent += 1

            # Subscription expiry warnings — 30 days and 7 days out
            for days in (30, 7):
                target = timezone.localdate() + timedelta(days=days)
                members = (ClubMember.objects
                           .filter(club=club, standing='active',
                                   role__renewal_required=True,
                                   subscription_expires=target)
                           .select_related('user', 'notification_prefs'))
                for m in members:
                    label = f'  Subscription expiry ({days}d) → {m.user.get_full_name()}'
                    if dry_run:
                        self.stdout.write(label)
                    else:
                        subscription_expiry_warning(m, days)
                        self.stdout.write(label)
                    sent += 1

            # Payment reminders — active members with negative account balance
            neg_members = (ClubMember.objects
                           .filter(club=club, standing='active',
                                   account__balance__lt=0)
                           .select_related('user', 'account', 'notification_prefs'))
            for m in neg_members:
                label = f'  Payment reminder → {m.user.get_full_name()} (balance: ${m.account.balance:,.2f})'
                if dry_run:
                    self.stdout.write(label)
                else:
                    payment_reminder(m)
                    self.stdout.write(label)
                sent += 1

        prefix = 'Would send' if dry_run else 'Sent'
        self.stdout.write(self.style.SUCCESS(f'\nsend_notifications: {prefix} {sent} notifications'))
