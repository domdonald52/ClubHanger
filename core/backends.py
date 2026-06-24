from django.contrib.auth.backends import ModelBackend
from .models import User


class EmailBackend(ModelBackend):
    """Authenticate using email address instead of username."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None
        try:
            user = User.objects.get(email__iexact=username)
        except User.DoesNotExist:
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
