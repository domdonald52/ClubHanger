from django.conf import settings as _settings
from .models import Club, ClubConfig


def theme(request):
    """
    Expose the current club's theme colours to all templates.
    Resolves the club from the URL kwargs (club_slug) when available,
    else falls back to the first club. Always returns safe defaults.
    """
    club = None
    rm = getattr(request, 'resolver_match', None)
    if rm and rm.kwargs.get('club_slug'):
        club = Club.objects.filter(slug=rm.kwargs['club_slug']).first()
    if club is None:
        club = Club.objects.first()

    if club is None:
        return {'theme': {}}

    config, _ = ClubConfig.objects.get_or_create(club=club)
    ctx = {
        'vapid_public_key': _settings.VAPID_PUBLIC_KEY,
        'theme': {
            'banner': config.theme_banner,
            'primary': config.theme_primary,
            'accent': config.theme_accent,
            'confirmed': config.theme_confirmed,
            'pending': config.theme_pending,
            'departed': config.theme_departed,
            'returned': config.theme_returned,
            'completed_paid': config.theme_completed_paid,
            'weekend': config.theme_weekend,
            'atypical': config.theme_atypical,
            'logo': config.logo if config.logo else None,
            'chart_colors': config.get_chart_colors(),
            'font_family': config.get_font()[0],
            'font_url': config.get_font()[1],
            'compact_mode': config.compact_mode,
        }
    }
    # Make club_member available to the nav on every page
    if request.user.is_authenticated:
        from .models import ClubMember
        cm = ClubMember.objects.filter(user=request.user, club=club).select_related('role').first()
        if cm:
            ctx['club_member'] = cm
            ctx['other_clubs'] = list(
                ClubMember.objects.filter(user=request.user)
                .exclude(club=club)
                .select_related('club')
                .order_by('club__name')
            )
            try:
                unread_qs = cm.notifications.filter(is_read=False)
                ctx['unread_notifications_count'] = unread_qs.count()
                ctx['recent_notifications'] = list(unread_qs[:8])
            except Exception:
                ctx['unread_notifications_count'] = 0
                ctx['recent_notifications'] = []
            try:
                from .models import OccurrenceReport as _OR, OccurrenceAction as _OA
                ctx['open_occurrences_count'] = _OR.objects.filter(
                    club=club, status=_OR.STATUS_SUBMITTED).count() if cm.can_access_manage else 0
                ctx['open_actions_count'] = (
                    _OA.objects.filter(report__club=club, status='open').count()
                    if cm.can_access_manage else
                    _OA.objects.filter(assigned_to=cm, status='open').count()
                )
            except Exception:
                ctx['open_occurrences_count'] = 0
                ctx['open_actions_count'] = 0
            if cm.can_access_manage:
                try:
                    from .models import Booking, AircraftMaintenanceItem, MemberCredential, MaintenanceUrgency
                    from django.db.models import Q
                    from datetime import date
                    _today = date.today()
                    _n = 0
                    _n += Booking.objects.filter(
                        club=club, status='completed',
                        flight_completion__paid_at__isnull=True,
                        flight_completion__total_charge__gt=0,
                    ).count()
                    _n += Booking.objects.filter(
                        club=club, scheduled_start__date__gte=_today,
                    ).exclude(status__in=['cancelled', 'completed']).filter(
                        Q(blockout_conflict=True) |
                        Q(member__standing__in=['suspended', 'lapsed', 'resigned']) |
                        Q(aircraft__status='retired')
                    ).count()
                    _n += AircraftMaintenanceItem.objects.filter(
                        aircraft__club=club, aircraft__status='online',
                        urgency__in=[MaintenanceUrgency.AMBER, MaintenanceUrgency.RED],
                    ).count()
                    ctx['exceptions_count'] = _n
                except Exception:
                    ctx['exceptions_count'] = 0
                try:
                    from .models import Account as _Acct
                    from decimal import Decimal as _D
                    _drift = 0
                    for _acc in _Acct.objects.filter(club_member__club=club):
                        if abs(_acc.recompute_balance() - _acc.balance) > _D('0.01'):
                            _drift += 1
                    ctx['integrity_issues_count'] = _drift
                except Exception:
                    ctx['integrity_issues_count'] = 0
    return ctx
