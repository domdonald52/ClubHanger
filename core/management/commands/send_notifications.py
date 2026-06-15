"""
Management command: send_notifications

Run daily (e.g. via cron at 07:00):
    venv/bin/python manage.py send_notifications

Sends:
  - Booking reminders for flights tomorrow
  - Credential expiry warnings (30 days and 7 days out)
"""

from datetime import date, timedelta
from django.core.management.base import BaseCommand
from django.db.models import Q


class Command(BaseCommand):
    help = 'Send daily email notifications — booking reminders and credential expiry warnings'

    def handle(self, *args, **options):
        from core.models import Club, Booking, MemberCredential
        from core.email_notifications import booking_reminder, credential_expiry_warning

        tomorrow = date.today() + timedelta(days=1)
        sent = 0

        for club in Club.objects.all():
            # Booking reminders — confirmed flights tomorrow
            bookings = (Booking.objects
                        .filter(club=club, status='confirmed',
                                scheduled_start__date=tomorrow)
                        .select_related('member__user', 'member__club',
                                        'aircraft', 'instructor', 'flight_type'))
            for b in bookings:
                booking_reminder(b)
                sent += 1

            # Credential expiry warnings — 30 days and 7 days out
            for days in (30, 7):
                target = date.today() + timedelta(days=days)
                creds = (MemberCredential.objects
                         .filter(club_member__club=club, expiry_date=target)
                         .select_related('club_member__user', 'club_member__club'))
                for cred in creds:
                    credential_expiry_warning(cred.club_member, cred, days)
                    sent += 1

        self.stdout.write(self.style.SUCCESS(f'send_notifications: {sent} notifications sent'))
