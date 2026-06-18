"""
Paper Aviator → ClubHangar data migration command.

Import order matters — run in this sequence:
  1. --members     (creates User + ClubMember records)
  2. --balances    (sets opening account balances)
  3. --flights     (creates historical Booking + FlightCompletion records)
  4. --comments    (attaches instructor notes to matched flights)

Always dry-run first:
  python manage.py import_pa --club wac --members Members.csv --dry-run

Then execute:
  python manage.py import_pa --club wac --members Members.csv

Flight type names in PA must match CH flight type names exactly (case-insensitive).
Use --flight-type-map to remap where they differ:
  --flight-type-map '{"Student Dual": "Training Dual", "Solo": "Solo PIC"}'
"""

import csv
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    Account, AccountTransaction, Aircraft, Booking,
    Club, ClubMember, FlightCompletion, FlightType,
)

User = get_user_model()
NZ = ZoneInfo('Pacific/Auckland')

PA_STATUS_MAP = {
    'member':     'active',
    'expired':    'lapsed',
    'resigned':   'resigned',
    'non member': None,
    'deceased':   None,
}


class Command(BaseCommand):
    help = 'Import Paper Aviator CSV exports into ClubHangar'

    def add_arguments(self, parser):
        parser.add_argument('--club', required=True, metavar='SLUG',
                            help='Club slug')
        parser.add_argument('--members', metavar='PATH',
                            help='Members.csv from PA')
        parser.add_argument('--balances', metavar='PATH',
                            help='MemberBalances.csv from PA')
        parser.add_argument('--flights', nargs='+', metavar='PATH',
                            help='FlyingSheet.csv (one or more 30-day chunks)')
        parser.add_argument('--comments', nargs='+', metavar='PATH',
                            help='[member]-InstructorComments.csv files')
        parser.add_argument('--flight-type-map', metavar='JSON', default='{}',
                            help='JSON mapping PA flight type names to CH names. '
                                 'E.g. \'{"Student Dual": "Training Dual"}\'')
        parser.add_argument('--created-by', metavar='EMAIL',
                            help='Admin user email for audit fields (default: first club admin)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Validate and report without writing any data')

    def handle(self, *args, **options):
        try:
            club = Club.objects.get(slug=options['club'])
        except Club.DoesNotExist:
            raise CommandError(f"Club '{options['club']}' not found")

        dry_run = options['dry_run']
        mode = self.style.WARNING('DRY RUN') if dry_run else self.style.ERROR('LIVE RUN')
        self.stdout.write(f'\n{mode} — club: {club.name}\n')

        admin_user = self._get_admin_user(club, options.get('created_by'))
        self.stdout.write(f'Admin user: {admin_user.email}\n')

        try:
            ft_map = json.loads(options['flight_type_map'])
        except json.JSONDecodeError as e:
            raise CommandError(f'Invalid --flight-type-map JSON: {e}')

        if options['members']:
            self._import_members(club, options['members'], dry_run, admin_user)

        if options['balances']:
            self._import_balances(club, options['balances'], dry_run, admin_user)

        if options['flights']:
            for path in options['flights']:
                self._import_flights(club, path, dry_run, admin_user, ft_map)

        if options['comments']:
            for path in options['comments']:
                self._import_comments(club, path, dry_run)

        if dry_run:
            self.stdout.write(
                self.style.WARNING('\nDry run complete — re-run without --dry-run to commit.\n')
            )

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _get_admin_user(self, club, email=None):
        if email:
            try:
                return User.objects.get(email=email)
            except User.DoesNotExist:
                raise CommandError(f'User {email!r} not found')
        member = (
            ClubMember.objects
            .filter(club=club, has_admin_access=True)
            .select_related('user')
            .first()
        )
        if not member or not member.user:
            raise CommandError(
                'No admin user found for this club — pass --created-by <email>'
            )
        return member.user

    def _parse_date(self, s):
        """Parse PA date strings in several formats."""
        if not s:
            return None
        s = s.strip()
        for fmt in ('%d/%m/%Y %I:%M:%S %p', '%d-%b-%Y', '%Y-%m-%d', '%d/%m/%Y'):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    def _decimal(self, s, default=None):
        try:
            return Decimal(str(s).strip())
        except (InvalidOperation, AttributeError):
            return default

    @staticmethod
    def _split_name(name):
        """
        Return (first, last) from PA name formats.
        Handles "Smith, John", "John Smith", "(Smith, John)", "(DONT DELETE OR USE)".
        """
        name = name.strip().strip('()')
        if ',' in name:
            parts = [p.strip() for p in name.split(',', 1)]
            return parts[1], parts[0]
        parts = name.split()
        if len(parts) >= 2:
            return ' '.join(parts[:-1]), parts[-1]
        return name, ''

    def _member_lookup(self, club):
        """Return {full_name_lower: ClubMember} for all club members with a user."""
        lookup = {}
        for cm in ClubMember.objects.filter(club=club).select_related('user'):
            if cm.user:
                key = cm.user.get_full_name().lower().strip()
                lookup[key] = cm
        return lookup

    # ── Member import ─────────────────────────────────────────────────────────

    def _import_members(self, club, path, dry_run, admin_user):
        self.stdout.write(f'\n── Members: {path}')
        created = skipped_corp = skipped_status = dupes = errors = 0

        rows = self._read_csv(path)

        with transaction.atomic():
            for row in rows:
                first = (row.get('FirstName') or '').strip()
                last  = (row.get('Surname') or '').strip()
                email = (row.get('Email') or '').strip().lower()
                pa_status = (row.get('MembershipStatus') or '').strip().lower()
                memno = (row.get('MembershipNumber') or '').strip()
                phone_mobile = (row.get('PhoneMobile') or '').strip()
                phone_home   = (row.get('PhoneHome') or '').strip()
                phone_work   = (row.get('PhoneWork') or '').strip()

                # Skip corporate entries (no individual name)
                if not first and not last:
                    skipped_corp += 1
                    continue

                ch_standing = PA_STATUS_MAP.get(pa_status)
                if ch_standing is None:
                    skipped_status += 1
                    self.stdout.write(f'  SKIP  {first} {last} — status="{pa_status}"')
                    continue

                # Duplicate check
                if email and ClubMember.objects.filter(club=club, user__email=email).exists():
                    dupes += 1
                    self.stdout.write(f'  DUP   {first} {last} <{email}>')
                    continue

                label = f'{first} {last} <{email or "(no email)"}> [{ch_standing}]'
                self.stdout.write(f'  {"(dry)" if dry_run else "CREATE"} {label}')

                if not dry_run:
                    try:
                        with transaction.atomic():
                            if email:
                                user, _ = User.objects.get_or_create(
                                    email=email,
                                    defaults={
                                        'username':   email,
                                        'first_name': first,
                                        'last_name':  last,
                                        'is_active':  False,
                                    },
                                )
                            else:
                                username = f'pa_{memno or (first + last).lower().replace(" ", "_")}'
                                user, _ = User.objects.get_or_create(
                                    username=username,
                                    defaults={
                                        'first_name': first,
                                        'last_name':  last,
                                        'email':      '',
                                        'is_active':  False,
                                    },
                                )

                            cm, _ = ClubMember.objects.get_or_create(
                                user=user, club=club,
                                defaults={
                                    'standing':          ch_standing,
                                    'membership_number': memno,
                                    'phone_mobile':      phone_mobile,
                                    'phone_home':        phone_home,
                                    'phone_work':        phone_work,
                                },
                            )
                            Account.objects.get_or_create(club_member=cm)
                            created += 1
                    except Exception as e:
                        errors += 1
                        self.stdout.write(self.style.ERROR(f'  ERROR {label}: {e}'))
                else:
                    created += 1

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            f'  → created={created}  skipped_corporate={skipped_corp}  '
            f'skipped_status={skipped_status}  duplicates={dupes}  errors={errors}'
        )

    # ── Balance import ────────────────────────────────────────────────────────

    def _import_balances(self, club, path, dry_run, admin_user):
        self.stdout.write(f'\n── Balances: {path}')
        applied = zero_skipped = not_found = errors = 0

        rows = self._read_csv(path)
        lookup = self._member_lookup(club)

        with transaction.atomic():
            for row in rows:
                full_name = (row.get('FullName') or '').strip()
                balance = self._decimal(row.get('Balance') or '0', Decimal('0'))

                if balance == 0:
                    zero_skipped += 1
                    continue

                first, last = self._split_name(full_name)
                key = f'{first} {last}'.lower().strip()
                cm = lookup.get(key)

                if not cm:
                    not_found += 1
                    self.stdout.write(f'  MISS  "{full_name}" (balance={balance})')
                    continue

                direction = 'credit' if balance > 0 else 'debit'
                amount = abs(balance)
                self.stdout.write(
                    f'  {"(dry)" if dry_run else "SET"} '
                    f'{full_name}: {direction} ${amount}'
                )

                if not dry_run:
                    try:
                        account = Account.objects.get(club_member=cm)
                        AccountTransaction.objects.create(
                            account=account,
                            transaction_type='adjustment',
                            direction=direction,
                            amount=amount,
                            description='Opening balance imported from Paper Aviator',
                            payment_method='other',
                            reference='PA migration',
                            created_by=admin_user,
                        )
                        account.balance = balance
                        account.save(update_fields=['balance', 'updated_at'])
                        applied += 1
                    except Exception as e:
                        errors += 1
                        self.stdout.write(self.style.ERROR(f'  ERROR {full_name}: {e}'))
                else:
                    applied += 1

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            f'  → applied={applied}  zero_skipped={zero_skipped}  '
            f'not_found={not_found}  errors={errors}'
        )

    # ── Flight import ─────────────────────────────────────────────────────────

    def _import_flights(self, club, path, dry_run, admin_user, ft_map):
        self.stdout.write(f'\n── Flights: {path}')
        created = dupes = not_found = errors = 0
        unmapped_types = set()

        rows = self._read_csv(path)

        member_lookup   = self._member_lookup(club)
        aircraft_lookup = {a.registration.upper(): a for a in Aircraft.objects.filter(club=club)}
        ft_lookup       = {ft.name.lower(): ft for ft in FlightType.objects.filter(club=club)}

        # Instructors — all staff members, not just roster
        instructor_lookup = {}
        for cm in ClubMember.objects.filter(club=club).select_related('user'):
            if cm.user and cm.is_staff:
                instructor_lookup[cm.user.get_full_name().lower()] = cm.user

        # Build set of already-imported PA flight IDs to prevent re-import
        existing_pa_ids = set()
        for desc in Booking.objects.filter(
            club=club, description__contains='[PA-'
        ).values_list('description', flat=True):
            existing_pa_ids.update(re.findall(r'\[PA-(\d+)\]', desc))

        with transaction.atomic():
            for row in rows:
                flight_id     = (row.get('FlightID') or '').strip()
                date_str      = (row.get('FlightDate') or '').strip()
                member_name   = (row.get('MemberName') or '').strip()
                staff_name    = (row.get('StaffName') or '').strip()
                pa_ft_name    = (row.get('FlightType') or '').strip()
                description   = (row.get('Description') or '').strip()
                registration  = (row.get('Registration') or '').strip().upper()
                charge_time   = self._decimal(row.get('ChargeTime') or '0', Decimal('0'))
                hobbs_start   = self._decimal(row.get('StartHobbs'))
                hobbs_end     = self._decimal(row.get('EndHobbs'))
                tacho_start   = self._decimal(row.get('StartTacho1'))
                tacho_end     = self._decimal(row.get('EndTacho1'))
                total_charge  = self._decimal(row.get('Total') or '0', Decimal('0'))

                # Skip already-imported
                if flight_id in existing_pa_ids:
                    dupes += 1
                    continue

                flight_date = self._parse_date(date_str)
                if not flight_date:
                    errors += 1
                    self.stdout.write(
                        self.style.ERROR(f'  ERROR  flight {flight_id}: bad date "{date_str}"')
                    )
                    continue

                # Resolve member
                member = member_lookup.get(member_name.lower())
                if not member:
                    not_found += 1
                    self.stdout.write(f'  MISS   member "{member_name}" (PA flight {flight_id})')
                    continue

                # Resolve aircraft
                aircraft = aircraft_lookup.get(registration)
                if not aircraft:
                    not_found += 1
                    self.stdout.write(f'  MISS   aircraft "{registration}" (PA flight {flight_id})')
                    continue

                # Resolve flight type (apply user mapping first)
                mapped_ft_name = ft_map.get(pa_ft_name, pa_ft_name)
                flight_type = ft_lookup.get(mapped_ft_name.lower())
                if not flight_type:
                    unmapped_types.add(pa_ft_name)
                    not_found += 1
                    continue

                # Resolve instructor (optional)
                instructor_user = instructor_lookup.get(staff_name.lower()) if staff_name else None

                # Scheduled times: noon NZT on flight date
                dt_start = datetime(
                    flight_date.year, flight_date.month, flight_date.day,
                    12, 0, tzinfo=NZ,
                )
                dt_end = dt_start + timedelta(hours=float(charge_time or 1))

                full_desc = f'{description} [PA-{flight_id}]'.strip()

                self.stdout.write(
                    f'  {"(dry)" if dry_run else "CREATE"} '
                    f'{flight_date} {member_name} {registration} '
                    f'{charge_time}h ${total_charge}'
                )

                if not dry_run:
                    try:
                        with transaction.atomic():
                            booking = Booking.objects.create(
                                club=club,
                                member=member,
                                aircraft=aircraft,
                                flight_type=flight_type,
                                instructor=instructor_user,
                                scheduled_start=dt_start,
                                scheduled_end=dt_end,
                                status='completed',
                                description=full_desc,
                                created_by=admin_user,
                                departed_at=dt_start,
                                arrived_at=dt_end,
                            )
                            FlightCompletion.objects.create(
                                booking=booking,
                                outcome='completed',
                                outcome_notes='Imported from Paper Aviator',
                                hobbs_start=hobbs_start,
                                hobbs_end=hobbs_end,
                                tacho_start=tacho_start,
                                tacho_end=tacho_end,
                                actual_flight_hours=charge_time,
                                total_charge=total_charge,
                                amount_paid=total_charge,
                                logged_by=admin_user,
                            )
                            created += 1
                    except Exception as e:
                        errors += 1
                        self.stdout.write(self.style.ERROR(f'  ERROR flight {flight_id}: {e}'))
                else:
                    created += 1

            if dry_run:
                transaction.set_rollback(True)

        if unmapped_types:
            self.stdout.write(self.style.WARNING(
                '\n  Unmapped PA flight types — add to --flight-type-map or create these in CH Settings:'
            ))
            for t in sorted(unmapped_types):
                self.stdout.write(f'    "{t}"')

        self.stdout.write(
            f'  → created={created}  duplicates={dupes}  '
            f'not_found={not_found}  errors={errors}'
        )

    # ── Instructor comment import ─────────────────────────────────────────────

    def _import_comments(self, club, path, dry_run):
        self.stdout.write(f'\n── Comments: {path}')
        attached = not_found = 0

        rows = self._read_csv(path)

        for row in rows:
            date_str    = (row.get('Date') or '').strip()
            registration = (row.get('Aircraft') or '').strip().upper()
            description = (row.get('Description') or '').strip()
            comment     = (row.get('Comment') or '').strip()

            if not comment:
                continue

            flight_date = self._parse_date(date_str)
            if not flight_date:
                continue

            qs = FlightCompletion.objects.filter(
                booking__club=club,
                booking__aircraft__registration=registration,
                booking__scheduled_start__date=flight_date,
            )
            if description:
                qs = qs.filter(booking__description__icontains=description[:30])

            fc = qs.first()
            if not fc:
                not_found += 1
                self.stdout.write(f'  MISS  {date_str} {registration} "{description[:30]}"')
                continue

            note = f'[Instructor note: {comment}]'
            self.stdout.write(
                f'  {"(dry)" if dry_run else "ATTACH"} '
                f'{date_str} {registration} — {comment[:50]}'
            )

            if not dry_run:
                existing = fc.outcome_notes or ''
                if note not in existing:
                    fc.outcome_notes = f'{existing}\n{note}'.strip()
                    fc.save(update_fields=['outcome_notes', 'updated_at'])
            attached += 1

        self.stdout.write(f'  → attached={attached}  not_found={not_found}')

    # ── CSV reader ────────────────────────────────────────────────────────────

    def _read_csv(self, path):
        try:
            with open(path, encoding='utf-8-sig') as f:
                return list(csv.DictReader(f))
        except FileNotFoundError:
            raise CommandError(f'File not found: {path}')
        except Exception as e:
            raise CommandError(f'Error reading {path}: {e}')
