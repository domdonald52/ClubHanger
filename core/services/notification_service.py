"""
In-app notification service.
All public functions are safe to call from any context — they silently
no-op if the member has opted out or the notification can't be created.
"""
from datetime import date


_PREF_FIELDS = {
    'booking_confirmed', 'booking_cancelled', 'booking_reminder',
    'credential_expiring', 'subscription_expiring',
    'instructor_booking_urgent', 'instructor_booking_upcoming',
    'maintenance_alert', 'lapsed_credentials', 'slot_released',
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
    notify(
        booking.member, 'booking_confirmed',
        f'Booking confirmed — {booking.aircraft.registration} '
        f'{booking.scheduled_start.strftime("%a %-d %b")}',
        action_url=url,
    )


def notify_booking_cancelled(booking):
    if not (booking.member or booking.instructor):
        return
    from django.urls import reverse
    from ..models import ClubMember
    reason = booking.get_cancellation_reason_display() if booking.cancellation_reason else ''
    body   = f'Reason: {reason}' if reason else ''
    subj   = (f'Booking cancelled — {booking.aircraft.registration} '
              f'{booking.scheduled_start.strftime("%a %-d %b")}')
    if booking.member:
        notify(booking.member, 'booking_cancelled', subj, body=body)
    if booking.instructor:
        im = ClubMember.objects.filter(user=booking.instructor, club=booking.club).first()
        if im and im != booking.member:
            notify(im, 'booking_cancelled', subj, body=body)


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
