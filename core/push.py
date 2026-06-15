"""
Web Push notification helpers.
Requires pywebpush and VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY in settings.
"""
import json
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def send_push(subscription, title, body, url=None, icon=None):
    """
    Send a Web Push notification to a single PushSubscription instance.
    Returns True on success, False if the subscription is gone (caller should delete it).
    Raises on unexpected errors.
    """
    from pywebpush import webpush, WebPushException

    payload = json.dumps({
        'title': title,
        'body':  body,
        'url':   url or '/',
        'icon':  icon or '/static/core/img/icon-192.png',
    })
    try:
        webpush(
            subscription_info={
                'endpoint': subscription.endpoint,
                'keys': {
                    'p256dh': subscription.p256dh,
                    'auth':   subscription.auth,
                },
            },
            data=payload,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={
                'sub': f'mailto:{settings.VAPID_CLAIMS_EMAIL}',
            },
        )
        return True
    except WebPushException as exc:
        if exc.response is not None and exc.response.status_code in (404, 410):
            # Subscription expired/unregistered
            return False
        logger.error('WebPush error: %s', exc)
        raise


def notify_member(club_member, title, body, url=None):
    """
    Send a push to all active subscriptions for a club member.
    Stale subscriptions (404/410) are cleaned up automatically.
    """
    from .models import PushSubscription
    dead = []
    for sub in PushSubscription.objects.filter(club_member=club_member):
        try:
            ok = send_push(sub, title, body, url=url)
            if not ok:
                dead.append(sub.pk)
        except Exception:
            logger.exception('Failed to push to %s', sub.endpoint[:60])
    if dead:
        PushSubscription.objects.filter(pk__in=dead).delete()
