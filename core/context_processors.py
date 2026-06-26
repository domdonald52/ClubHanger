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
            'dual_accent': config.dual_accent,
            'weekend': config.theme_weekend,
            'atypical': config.theme_atypical,
            'logo': config.logo if config.logo else None,
            'app_banner': config.app_banner if config.app_banner else None,
            'chart_colors': config.get_chart_colors(),
            'font_family': config.get_font()[0],
            'font_url': config.get_font()[1],
            'compact_mode': config.compact_mode,
            'dark_mode': config.dark_mode,
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
            _on_manage_page = rm and rm.url_name and (
                rm.url_name.startswith('manage_') or 'manage' in (rm.namespace or '')
                or (rm.kwargs.get('club_slug') and request.path.startswith(f"/manage/"))
            )
            if cm.can_access_manage and _on_manage_page:
                try:
                    import time as _cp_time, logging as _cp_log
                    _cp_logger = _cp_log.getLogger('perf.context_processor')
                    _cp_t0 = _cp_t_prev = _cp_time.perf_counter()
                    def _cp_tick(label):
                        nonlocal _cp_t_prev
                        _now = _cp_time.perf_counter()
                        _cp_logger.warning('PERF ctx_proc [%s] %.0fms (total %.0fms)', label,
                                           (_now - _cp_t_prev) * 1000, (_now - _cp_t0) * 1000)
                        _cp_t_prev = _now

                    from .models import Booking, AircraftMaintenanceItem, MemberCredential, MaintenanceUrgency, Invoice
                    from django.db.models import Q, Exists, OuterRef
                    from datetime import datetime, timedelta, time as _time
                    from django.utils import timezone as _tz
                    # Use datetime range (not __date__) so PostgreSQL can use the index
                    _today = _tz.localdate()
                    _today_start = _tz.make_aware(datetime.combine(_today, _time.min))
                    _n = 0
                    # Unpaid completed flights (no invoice)
                    _n += Booking.objects.filter(
                        club=club, status='completed',
                        flight_completions__paid_at__isnull=True,
                        flight_completions__total_charge__gt=0,
                        flight_completions__invoices__isnull=True,
                    ).distinct().count()
                    _cp_tick('unpaid_flights')
                    # Booking conflicts — build one deduped set of IDs (mirrors manage_exceptions)
                    _future = Booking.objects.filter(
                        club=club, status__in=['pending', 'confirmed'],
                        scheduled_start__gte=_today_start)
                    _ac_sub = Booking.objects.filter(
                        club=club, aircraft_id=OuterRef('aircraft_id'),
                        status__in=['pending', 'confirmed', 'departed'],
                        scheduled_start__lt=OuterRef('scheduled_end'),
                        scheduled_end__gt=OuterRef('scheduled_start'),
                    ).exclude(pk=OuterRef('pk'))
                    _in_sub = Booking.objects.filter(
                        club=club, instructor_id=OuterRef('instructor_id'),
                        status__in=['pending', 'confirmed', 'departed'],
                        scheduled_start__lt=OuterRef('scheduled_end'),
                        scheduled_end__gt=OuterRef('scheduled_start'),
                    ).exclude(pk=OuterRef('pk'))
                    _clash_ids = set(
                        _future.filter(aircraft__isnull=False)
                        .annotate(_cl=Exists(_ac_sub)).filter(_cl=True)
                        .values_list('id', flat=True)
                    ) | set(
                        _future.filter(instructor__isnull=False)
                        .annotate(_cl=Exists(_in_sub)).filter(_cl=True)
                        .values_list('id', flat=True)
                    )
                    _cp_tick('clash_ids')
                    _conf_ids = set(
                        Booking.objects.filter(
                            club=club, scheduled_start__gte=_today_start,
                        ).exclude(status__in=['cancelled', 'completed']).filter(
                            Q(blockout_conflict=True) |
                            Q(member__standing__in=['suspended', 'lapsed', 'resigned']) |
                            Q(member__standing='active',
                              member__subscription_expires__isnull=False,
                              member__subscription_expires__lt=_today) |
                            Q(aircraft__status='retired') |
                            Q(id__in=_clash_ids)
                        ).values_list('id', flat=True)
                    )
                    _n += len(_conf_ids)
                    _cp_tick('conf_ids')
                    # Instructor off-roster — only bookings not already counted above
                    from .models import InstructorAvailability as _IA
                    _roster_uids = set(
                        ClubMember.objects.filter(club=club, is_on_instructor_roster=True)
                        .values_list('user_id', flat=True)
                    )
                    _av_by_user = {}
                    for _av in _IA.objects.filter(club_member__club=club).select_related('club_member'):
                        _av_by_user.setdefault(_av.club_member.user_id, []).append(_av)
                    _instr_bks = list(
                        Booking.objects.filter(
                            club=club, instructor__isnull=False,
                            scheduled_start__gte=_today_start,
                        ).exclude(status__in=['cancelled', 'completed'])
                        .exclude(id__in=_conf_ids)
                        .values_list('id', 'instructor_id', 'scheduled_start')
                    )
                    _n += sum(
                        1 for _, _uid, _start in _instr_bks
                        if _uid in _roster_uids
                        and _av_by_user.get(_uid)
                        and not any(w.applies_on(_tz.localtime(_start).date()) for w in _av_by_user[_uid])
                    )
                    _cp_tick('instructor_off_roster')
                    # Maintenance items (amber/red)
                    _n += AircraftMaintenanceItem.objects.filter(
                        aircraft__club=club, aircraft__status='online',
                        urgency__in=[MaintenanceUrgency.AMBER, MaintenanceUrgency.RED],
                    ).count()
                    # Unpaid invoices (draft or sent)
                    _n += Invoice.objects.filter(
                        club=club, status__in=(Invoice.STATUS_DRAFT, Invoice.STATUS_SENT),
                    ).count()
                    # Overdue returns (departed >24h)
                    _n += Booking.objects.filter(
                        club=club, status='departed',
                        departed_at__lt=_tz.now() - timedelta(hours=24),
                    ).count()
                    _cp_tick('maint+invoices+overdue')
                    # Lapsed credentials for members with upcoming bookings
                    _fut_user_ids = (
                        Booking.objects
                        .filter(club=club, scheduled_start__gte=_today_start)
                        .exclude(status__in=['cancelled', 'completed'])
                        .values_list('member__user_id', flat=True).distinct()
                    )
                    _n += (
                        MemberCredential.objects
                        .filter(member_id__in=_fut_user_ids, expiry_date__lt=_today)
                        .values('member_id').distinct().count()
                    )
                    _cp_tick('lapsed_credentials')
                    ctx['exceptions_count'] = _n
                except Exception:
                    ctx['exceptions_count'] = 0
                ctx['integrity_issues_count'] = 0
    return ctx
