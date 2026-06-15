"""
In-app notification service.
All public functions are safe to call from any context — they silently
no-op if the member has opted out or the notification can't be created.
Adds Web Push alongside in-app notifications where subscriptions exist.
"""
from datetime import date


def _push(club_member, title, body, url=None):
    """Fire-and-forget push to all of a member's subscribed devices."""
    try:
        from ..push import notify_member
        notify_member(club_member, title, body, url=url)
    except Exception:
        pass  # never let push failures break the calling flow


_PREF_FIELDS = {
    'booking_confirmed', 'booking_cancelled', 'booking_reminder',
    'credential_expiring', 'subscription_expiring',
    'instructor_booking_urgent', 'instructor_booking_upcoming',
    'maintenance_alert', 'lapsed_credentials', 'slot_released',
    'payment_reminder', 'invoice_sent',
}


def notify(club_member, notification_type, subject, body='', action_url=''):
    """Create an in-app notification unless the member has opted out."""
    from ..models import Notification, NotificationPreference
    if notification_type in _PREF_FIELDS:
        try:
            prefs = club_member.notification_prefs
            if not getattr(prefs, notification_type, True):
                return
        except NotificationPreference.DoesNotExist:
            pass  # no prefs record = all defaults (on)
    try:
        Notification.objects.create(
            club_member=club_member,
            notification_type=notification_type,
            subject=subject,
            body=body,
            action_url=action_url,
        )
    except Exception:
        pass  # never let notification failures break the calling flow


def notify_booking_confirmed(booking):
    if not booking.member:
        return
    from django.urls import reverse
    url = reverse('core:booking_detail',
                  kwargs={'club_slug': booking.club.slug, 'booking_id': booking.id})
    title = f'Booking confirmed — {booking.aircraft.registration}'
    body  = booking.scheduled_start.strftime('%a %-d %b, %H:%M')
    notify(booking.member, 'booking_confirmed', title, action_url=url)
    app_url = reverse('core:app_bookings', kwargs={'club_slug': booking.club.slug})
    _push(booking.member, title, body, url=app_url)


def notify_booking_cancelled(booking):
    if not (booking.member or booking.instructor):
        return
    from django.urls import reverse
    from ..models import ClubMember
    reason = booking.get_cancellation_reason_display() if booking.cancellation_reason else ''
    push_body = f'{booking.scheduled_start.strftime("%a %-d %b, %H:%M")}' + (f' — {reason}' if reason else '')
    subj   = (f'Booking cancelled — {booking.aircraft.registration} '
              f'{booking.scheduled_start.strftime("%a %-d %b")}')
    url    = reverse('core:booking_detail',
                     kwargs={'club_slug': booking.club.slug, 'booking_id': booking.id})
    app_url = reverse('core:app_bookings', kwargs={'club_slug': booking.club.slug})
    if booking.member:
        notify(booking.member, 'booking_cancelled', subj, body=f'Reason: {reason}' if reason else '', action_url=url)
        _push(booking.member, subj, push_body, url=app_url)
    if booking.instructor:
        im = ClubMember.objects.filter(user=booking.instructor, club=booking.club).first()
        if im and im != booking.member:
            notify(im, 'booking_cancelled', subj, body=f'Reason: {reason}' if reason else '', action_url=url)
            _push(im, subj, push_body, url=app_url)


def notify_instructor_new_booking(booking):
    """Notify instructor of new assignment. Urgent if ≤2 days, standard if 3–10 days."""
    if not booking.instructor:
        return
    from ..models import ClubMember
    days = (booking.scheduled_start.date() - date.today()).days
    if days > 10:
        return
    ntype = 'instructor_booking_urgent' if days <= 2 else 'instructor_booking_upcoming'
    im = ClubMember.objects.filter(user=booking.instructor, club=booking.club).first()
    if not im:
        return
    from django.urls import reverse
    url  = reverse('core:booking_detail',
                   kwargs={'club_slug': booking.club.slug, 'booking_id': booking.id})
    name = booking.member.user.get_full_name() if booking.member else '—'
    prefix = 'Urgent — ' if days <= 2 else ''
    notify(
        im, ntype,
        f'{prefix}New booking: {name} · {booking.aircraft.registration} '
        f'{booking.scheduled_start.strftime("%a %-d %b %H:%M")}',
        action_url=url,
    )


def notify_flight_charged(flight_completion):
    """Push notification when a flight's charges are finalised."""
    booking = flight_completion.booking
    if not booking.member:
        return
    from django.urls import reverse
    title = f'Flight charged — {booking.aircraft.registration}'
    body  = f'${flight_completion.total_charge:.2f} · {booking.scheduled_start.strftime("%-d %b")}'
    app_url = reverse('core:app_profile', kwargs={'club_slug': booking.club.slug}) + '#outstanding'
    _push(booking.member, title, body, url=app_url)


def notify_invoice_issued(invoice):
    """Push notification when an invoice is sent to a member."""
    if not invoice.member:
        return
    from django.urls import reverse
    title = f'Invoice {invoice.display_number} — ${invoice.total:.2f}'
    body  = f'Due {invoice.due_date.strftime("%-d %b %Y")} · check your profile for details'
    app_url = reverse('core:app_profile', kwargs={'club_slug': invoice.club.slug}) + '#outstanding'
    _push(invoice.member, title, body, url=app_url)
