"""
seed_demos — seed TWO clearly-fictional demo clubs so the multi-club login
picker can be tested on staging.

Both clubs are seeded via the existing `seed_demo` command, so they get the
full realistic dataset. The seeded admin (SEED_ADMIN_EMAIL, default
admin@wac-demo.example) is a member of BOTH clubs, so signing in shows the
club picker on web and mobile.

  python manage.py seed_demos          # seed/refresh both demo clubs
  python manage.py seed_demos --reset  # wipe & regenerate both (fresh DB)

The names are obviously made-up so they're never mistaken for a real club.
"""
from django.core.management.base import BaseCommand
from django.core.management import call_command

# (name, slug) — both fictional.
DEMO_CLUBS = [
    ("Skyhaven Aero Club",     "skyhaven"),
    ("Brightwater Flying Club", "brightwater"),
]


class Command(BaseCommand):
    help = ("Seed two clearly-named demo clubs (Skyhaven, Brightwater) for testing "
            "the multi-club login picker. The seeded admin is a member of both.")

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset', action='store_true',
            help='Wipe & regenerate each demo club. Safe across the two clubs '
                 '(shared members are only removed once orphaned from both).')

    def handle(self, *args, **options):
        for name, slug in DEMO_CLUBS:
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== {name} ({slug}) ==="))
            call_command('seed_demo', slug=slug, name=name, reset=options['reset'])
        self.stdout.write(self.style.SUCCESS(
            "\nBoth demo clubs seeded. The admin is a member of both — sign in to "
            "see the club picker (web and mobile)."))
