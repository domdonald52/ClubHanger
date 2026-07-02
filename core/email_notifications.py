"""
Event-triggered email notifications.

Each function is called at the point the event occurs (view or service layer).
All functions are fire-and-forget — exceptions are caught and logged, never
raised to the caller.

SMTP is configured via environment variables (see settings.py).
Set EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend for dev.
Set EMAIL_OVERRIDE_TO to redirect all outgoing email to one address (testing).
"""

import logging
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings

log = logging.getLogger(__name__)

_LOCAL = {'localhost', '127.0.0.1', '0.0.0.0', '*'}


def _site_url():
    """Return the absolute site URL for use in emails, e.g. https://example.railway.app"""
    url = getattr(settings, 'SITE_URL', '').rstrip('/')
    if not url:
        hosts = getattr(settings, 'ALLOWED_HOSTS', [])
        host = next((h for h in hosts if h not in _LOCAL and not h.startswith('.')), '')
        if host:
            url = f'https://{host}'
    return url


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
    try:
        return club_member.notification_prefs
    except Exception:
        return None


def _wants(club_member, flag):
    p = _prefs(club_member)
    return getattr(p, flag, True) if p else True


def _email_context(club):
    """Common template context for HTML emails."""
    ctx = {
        'club_name': club.name,
        'banner_color': '#2b2b2b',
        'primary_color': '#c0481c',
        'logo_url': '',
    }
    try:
        from .models import ClubConfig
        cfg = ClubConfig.objects.filter(club=club).first()
        if cfg:
            ctx['banner_color'] = cfg.theme_banner
            ctx['primary_color'] = cfg.theme_primary
            if cfg.logo:
                site_url = _site_url()
                if site_url:
                    ctx['logo_url'] = site_url + cfg.logo.url
    except Exception:
        pass
    return ctx


def _send(subject, body_text, to_email, from_email, body_html=None):
    override = (getattr(settings, 'EMAIL_OVERRIDE_TO', '') or '').strip()
    recipient = override if override else to_email
    try:
        if body_html:
            msg = EmailMultiAlternatives(subject, body_text, from_email, [recipient])
            msg.attach_alternative(body_html, 'text/html')
            msg.send()
        else:
            send_mail(subject, body_text, from_email, [recipient], fail_silently=False)
        if recipient != to_email:
            log.info('Email sent (→ override %s): %s', recipient, subject)
        else:
            log.info('Email sent: %s → %s', subject, to_email)
    except Exception as e:
        log.warning('Email failed: %s → %s: %s', subject, to_email, e)
    except BaseException as e:
        log.warning('Email send aborted (worker signal?): %s → %s: %s', subject, to_email, e)
        raise


# ── Club invite ───────────────────────────────────────────────────────────────

def club_invite(invite):
    """Send a single-use invite link to a prospective member."""
    from django.urls import reverse
    site = _site_url()
    accept_path = reverse('core:accept_invite', kwargs={'token': str(invite.token)})
    accept_url = site + accept_path

    ctx = _email_context(invite.club)
    ctx.update({'invite': invite, 'accept_url': accept_url})

    subject = f"You've been invited to join {invite.club.name}"
    body_html = render_to_string('email/invite.html', ctx)
    body_text = (
        f"You've been invited to join {invite.club.name} on ClubHangar.\n\n"
        f"Accept your invitation:\n{accept_url}\n\n"
        f"This link expires in 7 days. If you didn't expect this, you can ignore it."
    )
    _send(subject, body_text, invite.email, _from_addr(invite.club), body_html)


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

    # Most recent lesson note for this member with a next-lesson plan
    from .models import LessonNote as _LN
    _note_qs = (_LN.objects
                .filter(booking__member=member, booking__club=booking.club,
                        booking__scheduled_start__lt=booking.scheduled_start)
                .exclude(next_lesson_plan='')
                .order_by('-booking__scheduled_start'))
    if booking.instructor:
        prev_note = _note_qs.filter(booking__instructor=booking.instructor).first()
        if not prev_note:
            prev_note = _note_qs.first()
    else:
        prev_note = _note_qs.first()

    from django.urls import reverse as _rev
    booking_url = _site_url() + _rev('core:app_booking_detail', args=[booking.club.slug, booking.id])

    body = (
        f'Hi {member.user.first_name},\n\n'
        f'Your booking has been confirmed.\n\n'
        f'  Aircraft:   {booking.aircraft.registration}\n'
        f'  Date/time:  {booking.scheduled_start.strftime("%A %-d %B %Y, %H:%M")}\n'
        f'  Duration:   until {booking.scheduled_end.strftime("%H:%M")}\n'
    )
    if booking.instructor:
        body += f'  Instructor: {booking.instructor.get_full_name()}\n'
    if prev_note:
        body += f'\nFrom your last lesson:\n{prev_note.next_lesson_plan}\n'
    body += (
        f'\nSee you there!\n\n'
        f'Please note that bookings are subject to weather and operational constraints '
        f'which can change at short notice. Your instructor will endeavour to make '
        f'contact if there are any issues.\n\n'
        f'View or cancel your booking:\n{booking_url}\n\n'
        f'{booking.club.name}\n'
    )
    ctx = {**_email_context(booking.club), 'booking': booking, 'member': member, 'prev_note': prev_note, 'booking_url': booking_url}
    body_html = render_to_string('email/booking_confirmed.html', ctx)
    _send(subject, body, email, from_email, body_html=body_html)


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
    ctx = {**_email_context(booking.club), 'booking': booking, 'member': member, 'reason': reason}
    body_html = render_to_string('email/booking_cancelled.html', ctx)
    _send(subject, body, email, from_email, body_html=body_html)


def booking_unconfirmed(booking):
    """Sent to member when their confirmed booking is reverted to pending."""
    member = booking.member
    if not member:
        return
    email = member.user.email
    if not email:
        return
    from_email = _from_addr(booking.club)
    subject = f'Booking pending — {booking.aircraft.registration} · {booking.scheduled_start.strftime("%a %-d %b %H:%M")}'
    body = (
        f'Hi {member.user.first_name},\n\n'
        f'Your booking has been moved back to pending.\n\n'
        f'  Aircraft:  {booking.aircraft.registration}\n'
        f'  Date/time: {booking.scheduled_start.strftime("%A %-d %B %Y, %H:%M")}\n'
        f'\nThe club will be in touch to re-confirm or let you know next steps.\n\n{booking.club.name}\n'
    )
    ctx = {**_email_context(booking.club), 'booking': booking, 'member': member}
    body_html = render_to_string('email/booking_unconfirmed.html', ctx)
    _send(subject, body, email, from_email, body_html=body_html)


def booking_amended(booking, changes=''):
    """Sent to member when booking details (aircraft, instructor) are changed by staff."""
    member = booking.member
    if not member:
        return
    email = member.user.email
    if not email:
        return
    from_email = _from_addr(booking.club)
    subject = f'Booking updated — {booking.aircraft.registration} · {booking.scheduled_start.strftime("%a %-d %b %H:%M")}'
    body = (
        f'Hi {member.user.first_name},\n\n'
        f'Your booking has been updated.\n\n'
        f'  Aircraft:  {booking.aircraft.registration}\n'
        f'  Date/time: {booking.scheduled_start.strftime("%A %-d %B %Y, %H:%M")}\n'
    )
    if booking.instructor:
        body += f'  Instructor: {booking.instructor.get_full_name()}\n'
    if changes:
        body += f'\nChanged: {changes}\n'
    body += f'\n{booking.club.name}\n'
    ctx = {**_email_context(booking.club), 'booking': booking, 'member': member, 'changes': changes}
    body_html = render_to_string('email/booking_amended.html', ctx)
    _send(subject, body, email, from_email, body_html=body_html)


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
    base_ctx = _email_context(club)
    for admin in admins:
        body = (
            f'A new occurrence report has been submitted.\n\n'
            f'  Type:        {report.occurrence_type.name}\n'
            f'  Date:        {report.date_of_occurrence}\n'
            f'  Reported by: {report.reported_by.user.get_full_name()}\n'
        )
        if report.aircraft:
            body += f'  Aircraft:    {report.aircraft.registration}\n'
        body += f'\n  {report.description[:300]}\n\nLog in to review this report.\n\n{club.name}\n'
        ctx = {**base_ctx, 'report': report, 'admin': admin}
        body_html = render_to_string('email/occurrence_submitted.html', ctx)
        _send(subject, body, admin.user.email, from_email, body_html=body_html)


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
    from django.urls import reverse as _rev
    booking_url = _site_url() + _rev('core:app_booking_detail', args=[booking.club.slug, booking.id])
    body += f'\nView or cancel your booking:\n{booking_url}\n\n{booking.club.name}\n'
    ctx = {**_email_context(booking.club), 'booking': booking, 'member': member, 'booking_url': booking_url}
    body_html = render_to_string('email/booking_reminder.html', ctx)
    _send(subject, body, email, from_email, body_html=body_html)


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
    ctx = {**_email_context(club_member.club),
           'club_member': club_member, 'credential': credential, 'days_until': days_until}
    body_html = render_to_string('email/credential_expiry.html', ctx)
    _send(subject, body, email, from_email, body_html=body_html)


def subscription_expiry_warning(club_member, days_until):
    """Warn member that their subscription expires soon. Called by cron command."""
    if not _wants(club_member, 'subscription_expiring'):
        return
    email = club_member.user.email
    if not email:
        return
    from_email = _from_addr(club_member.club)
    exp = club_member.subscription_expires
    subject = f'Subscription expiring in {days_until} days — {club_member.club.name}'
    body = (
        f'Hi {club_member.user.first_name},\n\n'
        f'Your membership subscription expires in {days_until} days'
        f' (on {exp.strftime("%-d %B %Y")}).\n\n'
        f'Please contact the club to arrange renewal.\n\n'
        f'{club_member.club.name}\n'
    )
    ctx = {**_email_context(club_member.club),
           'club_member': club_member, 'days_until': days_until,
           'expiry_date': exp}
    body_html = render_to_string('email/subscription_expiry.html', ctx)
    _send(subject, body, email, from_email, body_html=body_html)


def payment_reminder(club_member):
    """Nudge member with a negative account balance. Called by cron command."""
    if not _wants(club_member, 'payment_reminder'):
        return
    email = club_member.user.email
    if not email:
        return
    try:
        account = club_member.account
        balance = account.balance
    except Exception:
        return
    if balance >= 0:
        return
    from_email = _from_addr(club_member.club)
    subject = f'Account balance reminder — {club_member.club.name}'
    body = (
        f'Hi {club_member.user.first_name},\n\n'
        f'Your account balance is currently ${balance:,.2f}.\n\n'
        f'Please contact the club or make a payment to clear your outstanding balance.\n\n'
        f'{club_member.club.name}\n'
    )
    ctx = {**_email_context(club_member.club),
           'club_member': club_member, 'balance': balance}
    body_html = render_to_string('email/payment_reminder.html', ctx)
    _send(subject, body, email, from_email, body_html=body_html)


def invoice_sent(invoice):
    """Email member a copy of their invoice when it is marked sent. Called from view."""
    if not invoice.member:
        return
    club_member = invoice.member
    if not _wants(club_member, 'invoice_sent'):
        return
    email = club_member.user.email
    if not email:
        return
    from_email = _from_addr(invoice.club)
    status_label = 'PAID' if invoice.status == 'paid' else f'Due {invoice.due_date.strftime("%-d %b %Y")}'
    subject = f'Invoice {invoice.display_number} — {invoice.club.name} — {status_label}'
    body = (
        f'Hi {club_member.user.first_name},\n\n'
        f'Please find your invoice from {invoice.club.name}.\n\n'
        f'  Invoice:  {invoice.display_number}\n'
        f'  Amount:   ${invoice.total:,.2f}\n'
        f'  Due:      {invoice.due_date.strftime("%-d %B %Y")}\n'
    )
    if invoice.balance_due > 0:
        body += f'  Owing:    ${invoice.balance_due:,.2f}\n'
    body += f'\n{invoice.club.name}\n'
    line_items = list(invoice.line_items.all().order_by('sort_order'))
    ctx = {**_email_context(invoice.club),
           'invoice': invoice, 'club_member': club_member,
           'line_items': line_items, 'status_label': status_label}
    body_html = render_to_string('email/invoice_sent.html', ctx)
    _send(subject, body, email, from_email, body_html=body_html)


def invoice_overdue_reminder(invoice, days_overdue, reminder_text=''):
    """Remind member of an overdue invoice. Called by send_notifications at 30/60/90-day thresholds."""
    if not invoice.member:
        return
    club_member = invoice.member
    email = club_member.user.email
    if not email:
        return
    from_email = _from_addr(invoice.club)
    subject = f'Invoice {invoice.display_number} overdue — {invoice.club.name}'
    body = (
        f'Hi {club_member.user.first_name},\n\n'
        f'This is a reminder that invoice {invoice.display_number} is now {days_overdue} days overdue.\n\n'
        f'  Invoice:  {invoice.display_number}\n'
        f'  Amount:   ${invoice.balance_due:,.2f}\n'
        f'  Due:      {invoice.due_date.strftime("%-d %B %Y")}\n\n'
        f'Please pay at the club or by bank transfer, using {invoice.display_number} as your payment reference.\n\n'
        f'If you have already paid, please allow a few days for the payment to be recorded.\n\n'
        f'{invoice.club.name}\n'
    )
    from django.urls import reverse as _rev
    invoice_url = _site_url() + _rev('core:app_invoice_detail', args=[invoice.club.slug, invoice.id])
    if not reminder_text:
        try:
            from .models import ClubConfig as _CC
            _cfg = _CC.objects.filter(club=invoice.club).first()
            reminder_text = (_cfg.overdue_reminder_text or '') if _cfg else ''
        except Exception:
            pass
    ctx = {**_email_context(invoice.club),
           'invoice': invoice, 'club_member': club_member,
           'days_overdue': days_overdue, 'invoice_url': invoice_url,
           'reminder_text': reminder_text}
    body_html = render_to_string('email/invoice_overdue.html', ctx)
    _send(subject, body, email, from_email, body_html=body_html)


def lesson_note_emailed(note, club):
    """Email a lesson note to the member after the instructor shares it."""
    member = note.booking.member
    if not member:
        return
    email = member.user.email
    if not email:
        return
    from_email = _from_addr(club)
    date_str = note.booking.scheduled_start.strftime('%-d %B %Y') if note.booking.scheduled_start else '?'
    author_name = note.author.get_full_name() if note.author else 'Your instructor'
    subject = f'Training note — {date_str} — {club.name}'
    body = f'Hi {member.user.first_name},\n\n{author_name} has shared training notes from your flight on {date_str}.\n\n'
    if note.exercises_covered:
        body += f'Exercises covered:\n{note.exercises_covered}\n\n'
    if note.debrief_notes:
        body += f'Debrief:\n{note.debrief_notes}\n\n'
    if note.next_lesson_plan:
        body += f'Next lesson plan:\n{note.next_lesson_plan}\n\n'
    body += f'{club.name}'
    sections = []
    if note.exercises_covered:
        sections.append(('Exercises covered', note.exercises_covered))
    if note.debrief_notes:
        sections.append(('Debrief', note.debrief_notes))
    if note.next_lesson_plan:
        sections.append(('Next lesson plan', note.next_lesson_plan))
    fc = getattr(note.booking, 'flight_completion', None)
    body_html = render_to_string('email/lesson_note.html', {
        'club': club, 'note': note, 'fc': fc,
        'member': member, 'author_name': author_name,
        'date_str': date_str, 'sections': sections,
    })
    _send(subject, body, email, from_email, body_html=body_html)
