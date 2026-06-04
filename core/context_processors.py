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
        'theme': {
            'banner': config.theme_banner,
            'primary': config.theme_primary,
            'accent': config.theme_accent,
            'confirmed': config.theme_confirmed,
            'pending': config.theme_pending,
            'weekend': config.theme_weekend,
            'atypical': config.theme_atypical,
        }
    }
    # Make club_member available to the nav on every page
    if request.user.is_authenticated:
        from .models import ClubMember
        cm = ClubMember.objects.filter(user=request.user, club=club).select_related('role').first()
        if cm:
            ctx['club_member'] = cm
    return ctx
