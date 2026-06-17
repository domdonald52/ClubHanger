class NoCacheAppMiddleware:
    """Prevent browsers/iOS from caching the mobile app pages or the auth pages."""

    AUTH_PREFIXES = ('/login', '/logout', '/password-reset', '/reset/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        p = request.path
        # A stale login or password-reset page served from cache is confusing and
        # can look like an old (e.g. Django admin) screen — so never cache them.
        if '/app/' in p or p.startswith(self.AUTH_PREFIXES):
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
        return response
