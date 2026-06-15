"""
Event-triggered email notifications.

Each function is called at the point the event occurs (view or service layer).
All functions are fire-and-forget — exceptions are caught and logged, never
raised to the caller.

SMTP is configured via environment variables (see settings.py).
Set EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend for dev.
"""

import logging
from django.core.mail import send_mail
from django.conf import settings

log = logging.getLogger(__name__)


def _from_addr(club):
    """Use club billing email if set, else DEFAULT_FROM_EMAIL."""
    try:
        from .models import ClubConfig
        cfg = ClubConfig.objects.filter(club=club).first()
        if cfg and cfg.billing_email:
            name = cfg.billing_name or club.name
            return f'{name} <{cfg.billing_email}>'
    except Exception:
        pass
    return settings.DEFAULT_FROM_EMAIL


def _prefs(club_member):
    """Return NotificationPreference for this member, or None."""
    try:
        return club_member.notification_prefs
    except Exception:
        return None


def _wants(club_member, flag):
    p = _prefs(club_member)
    return getattr(p, flag, True) if p else True


def _send(subject, body, to_email, from_email):
    try:
        send_mail(subject, body, from_email, [to_email], fail_silently=False)
        log.info('Email sent: %s → %s', subject, to_email)
    except Exception as e:
        log.warning('Email failed: %s → %s: %s', subject, to_email, e)


# ── Booking events ────────────────────────────────────────────────────────────

def booking_confirmed(booking):
    """Sent to member when their booking is confirmed."""
    member = booking.member
    if not _wants(member, 'booking_confirmed'):
        return
    email = member.user.email
    if not email:
        return
    from_email = _from_addr(booking.club)
    subject = f'Booking confirmed — {booking.aircraft.registration} · {booking.scheduled_start.strftime("%a %-d %b %H:%M")}'
    body = (
        f'Hi {member.user.first_name},\n\n'
        f'Your booking has been confirmed.\n\n'
        f'  Aircraft:   {booking.aircraft.registration}\n'
        f'  Date/time:  {booking.scheduled_start.strftime("%A %-d %B %Y, %H:%M")}\n'
        f'  Duration:   until {booking.scheduled_end.strftime("%H:%M")}\n'
    )
    if booking.instructor:
        body += f'  Instructor: {booking.instructor.get_full_name()}\n'
    body += f'\n{booking.club.name}\n'
    _send(subject, body, email, from_email)


def booking_cancelled(booking, reason=''):
    """Sent to member when their booking is cancelled."""
    member = booking.member
    if not _wants(member, 'booking_cancelled'):
        return
    email = member.user.email
    if not email:
        return
    from_email = _from_addr(booking.club)
    subject = f'Booking cancelled — {booking.aircraft.registration} · {booking.scheduled_start.strftime("%a %-d %b %H:%M")}'
    body = (
        f'Hi {member.user.first_name},\n\n'
        f'Your booking has been cancelled.\n\n'
        f'  Aircraft:  {booking.aircraft.registration}\n'
        f'  Date/time: {booking.scheduled_start.strftime("%A %-d %B %Y, %H:%M")}\n'
    )
    if reason:
        body += f'  Reason:    {reason}\n'
    body += f'\nContact the club if you have questions.\n\n{booking.club.name}\n'
    _send(subject, body, email, from_email)


def occurrence_submitted(report):
    """Alert manage-access members when a new occurrence report is submitted."""
    from .models import ClubMember
    club = report.club
    from_email = _from_addr(club)
    admins = (ClubMember.objects
              .filter(club=club, has_admin_access=True)
              .select_related('user')
              .exclude(user__email=''))
    subject = f'New occurrence report — {report.occurrence_type.name} · {report.date_of_occurrence}'
    body = (
        f'A new occurrence report has been submitted.\n\n'
        f'  Type:        {report.occurrence_type.name}\n'
        f'  Date:        {report.date_of_occurrence}\n'
        f'  Reported by: {report.reported_by.user.get_full_name()}\n'
    )
    if report.aircraft:
        body += f'  Aircraft:    {report.aircraft.registration}\n'
    body += f'\n  {report.description[:300]}\n\nLog in to review this report.\n\n{club.name}\n'
    for admin in admins:
        _send(subject, body, admin.user.email, from_email)


# ── Cron-triggered (called from management command) ───────────────────────────

def booking_reminder(booking):
    """Day-before reminder. Called by cron command."""
    member = booking.member
    if not _wants(member, 'booking_reminder'):
        return
    email = member.user.email
    if not email:
        return
    from_email = _from_addr(booking.club)
    subject = f'Reminder: {booking.aircraft.registration} tomorrow at {booking.scheduled_start.strftime("%H:%M")}'
    body = (
        f'Hi {member.user.first_name},\n\n'
        f'This is a reminder for your flight tomorrow.\n\n'
        f'  Aircraft:   {booking.aircraft.registration}\n'
        f'  Date/time:  {booking.scheduled_start.strftime("%A %-d %B %Y, %H:%M")}\n'
        f'  Duration:   until {booking.scheduled_end.strftime("%H:%M")}\n'
    )
    if booking.instructor:
        body += f'  Instructor: {booking.instructor.get_full_name()}\n'
    body += f'\n{booking.club.name}\n'
    _send(subject, body, email, from_email)


def credential_expiry_warning(club_member, credential, days_until):
    """Warn member that a credential expires soon. Called by cron command."""
    if not _wants(club_member, 'credential_expiring'):
        return
    email = club_member.user.email
    if not email:
        return
    from_email = _from_addr(club_member.club)
    subject = f'Credential expiring in {days_until} days — {credential.get_credential_type_display()}'
    body = (
        f'Hi {club_member.user.first_name},\n\n'
        f'Your {credential.get_credential_type_display()} expires in {days_until} days'
        f' (on {credential.expiry_date.strftime("%-d %B %Y")}).\n\n'
        f'Please arrange renewal to maintain your flying currency.\n\n'
        f'{club_member.club.name}\n'
    )
    _send(subject, body, email, from_email)
