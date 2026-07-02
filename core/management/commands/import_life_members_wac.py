"""
One-off: import Wellington Aero Club life members from Paper Aviator.

    venv/bin/python manage.py import_life_members_wac --dry-run
    venv/bin/python manage.py import_life_members_wac

Idempotent — safe to run twice. Upserts on email where present;
falls back to first+last name match for the one member with no email.
No welcome or invite emails are sent.
"""
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Account, Club, ClubMember, Role

User = get_user_model()

MEMBERS = [
    {'first_name': 'Alistair',  'last_name': 'Gillespie',   'phone_mobile': '027 291 7665',                                    'email': 'directors@aaml.kiwi'},
    {'first_name': 'Amy',       'last_name': 'Dreverman',   'phone_mobile': '027 372 9842',                                    'email': 'amy.dreverman@gmail.com'},
    {'first_name': 'Andrew',    'last_name': 'Braddick',    'phone_home':   '479 5685',    'phone_mobile': '021 909 030',      'email': 'andrewb@xtra.co.nz'},
    {'first_name': 'Basil',     'last_name': 'Wakelin',     'phone_home':   '04 479 5286', 'phone_mobile': '027 453 0493',     'email': 'basilwakelin@gmail.com'},
    {'first_name': 'Bernard',   'last_name': 'Weinstein',   'phone_home':   '04 476 0588',                                    'email': 'bernardwe2@gmail.com'},
    {'first_name': 'Brian',     'last_name': 'Souter',      'phone_home':   '04 476 7910', 'phone_mobile': '+64 021 476 791',  'email': 'souterb@xtra.co.nz'},
    {'first_name': 'Bruce',     'last_name': 'Cunningham',  'phone_home':   '04 388 8792', 'phone_work':   '388 5886',         'email': 'soaringnz@xtra.co.nz'},
    {'first_name': 'Charles',   'last_name': 'Davis',       'phone_home':   '04 562 7178', 'phone_mobile': '027 675 7822',     'email': 'info@craniofacialsurgery.co.nz'},
    {'first_name': 'David',     'last_name': 'Jupp',        'phone_home':   '04 934 7470', 'phone_mobile': '+64 (21) 476 676', 'email': 'davidwjupp@gmail.com'},
    {'first_name': 'John',      'last_name': 'Spry',        'phone_home':   '04 971 5953', 'phone_mobile': '+64 (21) 474808',  'email': 'johnspry2@gmail.com'},
    {'first_name': 'John',      'last_name': 'Cook',        'phone_home':   '04 293 5383', 'phone_mobile': '027 2315009',      'email': 'jjcook@xtra.co.nz'},
    {'first_name': 'William',   'last_name': 'Coulter',     'phone_home':   '06 367 0989',                                    'email': ''},
]


class Command(BaseCommand):
    help = 'Import Wellington Aero Club life members (one-off)'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Show what would happen without writing anything')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — nothing will be saved'))

        try:
            club = Club.objects.get(slug='wellington-aero-club')
        except Club.DoesNotExist:
            raise CommandError("Club with slug 'wellington' not found")

        try:
            life_role = Role.objects.get(club=club, name__iexact='life member')
        except Role.DoesNotExist:
            raise CommandError(
                "No role named 'Life Member' found for Wellington Aero Club — "
                "create it in Settings → Roles first"
            )
        except Role.MultipleObjectsReturned:
            life_role = Role.objects.filter(club=club, name__iexact='life member').first()

        self.stdout.write(f'Club:      {club.name}')
        self.stdout.write(f'Role:      {life_role.name} (id={life_role.id})')
        self.stdout.write('')

        created_count = updated_count = 0

        with transaction.atomic():
            for row in MEMBERS:
                first = row['first_name']
                last  = row['last_name']
                email = row.get('email', '').strip().lower()

                # Synthesise placeholder email for members without one
                if not email:
                    email = f'{first.lower()}.{last.lower()}@migrated.invalid'
                    placeholder = True
                else:
                    placeholder = False

                # Find or create Django User
                user = User.objects.filter(email__iexact=email).first()
                if user:
                    # Update name in case it differs
                    changed = False
                    if user.first_name != first:
                        user.first_name = first; changed = True
                    if user.last_name != last:
                        user.last_name = last; changed = True
                    if changed and not dry_run:
                        user.save(update_fields=['first_name', 'last_name'])
                    action = 'existing user'
                else:
                    # Build a unique username
                    base_username = f'{first.lower()}.{last.lower()}'
                    username = base_username
                    suffix = 1
                    while User.objects.filter(username=username).exists():
                        username = f'{base_username}{suffix}'
                        suffix += 1
                    if not dry_run:
                        user = User.objects.create(
                            username=username,
                            first_name=first,
                            last_name=last,
                            email=email,
                            password=secrets.token_hex(32),  # unusable random password
                            is_active=True,
                        )
                    action = 'new user'

                # Find or create ClubMember
                member_qs = ClubMember.objects.filter(club=club, user=user) if user else ClubMember.objects.none()
                existing_member = member_qs.first() if user else None

                phone_fields = {k: v for k, v in row.items()
                                if k.startswith('phone_') and v}

                if existing_member:
                    existing_member.role    = life_role
                    existing_member.standing = ClubMember.STANDING_ACTIVE
                    for field, value in phone_fields.items():
                        setattr(existing_member, field, value)
                    if not dry_run:
                        existing_member.save()
                    updated_count += 1
                    verb = 'updated'
                else:
                    if not dry_run:
                        member = ClubMember.objects.create(
                            club=club,
                            user=user,
                            role=life_role,
                            standing=ClubMember.STANDING_ACTIVE,
                            **phone_fields,
                        )
                        Account.objects.get_or_create(member=member)
                    created_count += 1
                    verb = 'created'

                note = ' ⚠ placeholder email' if placeholder else ''
                self.stdout.write(
                    f'  {verb:7s}  {first} {last:<16} [{action}]  {email}{note}'
                )

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write('')
        prefix = 'Would create/update' if dry_run else 'Done —'
        self.stdout.write(self.style.SUCCESS(
            f'{prefix} {created_count} created, {updated_count} updated'
        ))
