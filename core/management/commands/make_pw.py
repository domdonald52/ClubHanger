from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = 'Reset dominic password to clubhangar2026'

    def handle(self, *args, **options):
        User = get_user_model()
        try:
            u = User.objects.get(username='dominic')
            u.set_password('clubhangar2026')
            u.save()
            self.stdout.write(self.style.SUCCESS('Password reset to clubhangar2026'))
        except User.DoesNotExist:
            self.stderr.write('User dominic not found')
