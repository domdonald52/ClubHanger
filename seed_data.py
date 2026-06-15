#!/usr/bin/env python
"""
Seed data for ClubHangar — Wellington Aero Club.

Creates a realistic club dataset:
  • 50 members (mix of students, private hire, club members, suspended)
  • 5 instructors (2 permanent roster, 3 part-time with availability windows)
  • 10 aircraft (trainers, hire, varying ages)
  • Charge rates, instructor grades, aerodromes, landing fees
  • Maintenance items + log entries with correct cumulative hours
  • ~25 past flights with realistic Hobbs readings and account transactions
  • ~15 upcoming confirmed bookings
  • 1 currently-departed flight

Usage:
    venv/bin/python seed_data.py            # add/update data, preserve existing flights
    venv/bin/python seed_data.py --reset    # clear all flights/transactions first
    venv/bin/python seed_data.py --dry-run  # show counts without writing
"""

import argparse, os, sys
from datetime import date, time, datetime, timedelta
from decimal import Decimal as D

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aero_club.settings')
import django
django.setup()

from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db import transaction as dj_transaction
from core.models import (
    Club, ClubMember, Role, Aircraft, AircraftType, FlightType,
    ChargeRate, InstructorGrade, Aerodrome, AerodromeFeeType,
    Booking, FlightCompletion, FlightChargeItem, FlightLandingEntry,
    AircraftMaintenanceItem, MaintenanceLogEntry,
    Account, AccountTransaction, FlightPayment, ClubConfig,
    InstructorAvailability, AircraftSurchargeType,
    Contact, ContactType,
    FlyingBudget,
    MemberCredential, CredentialType,
    create_maint_log_entry,
)
from core.services.booking_service import ServiceResult

User = get_user_model()

# ── Args ──────────────────────────────────────────────────────────────────────
p = argparse.ArgumentParser()
p.add_argument('--reset',   action='store_true', help='Clear flights/transactions before seeding')
p.add_argument('--dry-run', action='store_true', help='Show what would be created, write nothing')
args = p.parse_args()

DRY = args.dry_run
NOW = timezone.localtime(timezone.now())  # local (Auckland) time for hour arithmetic
TODAY = NOW.date()

def log(msg): print(f'  {msg}')
def head(msg): print(f'\n── {msg}')

if DRY:
    print('\n[DRY RUN — no data will be written]\n')

# ── Club ──────────────────────────────────────────────────────────────────────
head('Club')
club = Club.objects.first()
if not club:
    print('No club found — create one first via the app.'); sys.exit(1)
log(f'Club: {club.name} ({club.slug})')

# ── Early reset — must run before FlightType deduplication ───────────────────
if args.reset and not DRY:
    head('RESET — clearing flights, transactions, maintenance logs')
    FlightPayment.objects.filter(completion__booking__club=club).delete()
    FlightChargeItem.objects.filter(flight_completion__booking__club=club).delete()
    FlightLandingEntry.objects.filter(flight_completion__booking__club=club).delete()
    FlightCompletion.objects.filter(booking__club=club).delete()
    Booking.objects.filter(club=club).delete()
    MaintenanceLogEntry.objects.filter(aircraft__club=club).delete()
    AccountTransaction.objects.filter(account__club_member__club=club).delete()
    MemberCredential.objects.filter(club_member__club=club).delete()
    Account.objects.filter(club_member__club=club).update(balance=D('0'))
    log('Cleared all flight, transaction and maintenance data')

# Update club config
if not DRY:
    cfg, _ = ClubConfig.objects.get_or_create(club=club)
    cfg.operating_hours_start = time(8, 0)
    cfg.operating_hours_end   = time(19, 0)
    cfg.typical_hours_start   = time(8, 30)
    cfg.typical_hours_end     = time(17, 0)
    cfg.time_slot_interval    = 30
    cfg.save()
    log('Club config updated (08:30–17:00 typical, 08:00–19:00 operating, 90min slots)')

# ── Roles ─────────────────────────────────────────────────────────────────────
head('Roles')
def get_or_create_role(name, bookings_access, manage, settings, reports, superadmin, renewal, annual_fee):
    if DRY:
        log(f'Role: {name}')
        return None
    r, created = Role.objects.get_or_create(
        club=club, name=name,
        defaults=dict(bookings_access=bookings_access, can_access_manage=manage,
                      can_access_settings=settings, can_access_reports=reports,
                      is_superadmin=superadmin, renewal_required=renewal,
                      annual_renewal_fee=annual_fee or None)
    )
    log(f'{"+" if created else "="} Role: {name}')
    return r

r_student  = get_or_create_role('Student',      'manage_own', False, False, False, False, True,  D('120'))
r_member   = get_or_create_role('Member',        'manage_own', False, False, False, False, True,  D('180'))
r_hire     = get_or_create_role('Private Hire',  'manage_own', False, False, False, False, True,  D('200'))
r_instr    = get_or_create_role('Instructor',    'manage_all', True, False, True, False, True,  D('0'))
r_admin    = get_or_create_role('Administrator', 'manage_all', True, True,  True, True,  False, D('0'))

# ── Instructor grades ─────────────────────────────────────────────────────────
head('Instructor grades')
def ig(name, rate):
    if DRY: log(f'Grade: {name} ${rate}/hr'); return None
    g, c = InstructorGrade.objects.get_or_create(club=club, name=name, defaults={'hourly_rate': D(str(rate))})
    log(f'{"+" if c else "="} {name} ${rate}/hr'); return g

grade_bcat = ig('B-Cat',  95)
grade_ccat = ig('C-Cat',  80)
grade_acat = ig('A-Cat', 110)

# ── Aircraft types ────────────────────────────────────────────────────────────
head('Aircraft types')
def at(name):
    if DRY: log(f'Type: {name}'); return None
    t, c = AircraftType.objects.get_or_create(club=club, name=name)
    log(f'{"+" if c else "="} {name}'); return t

type_c152 = at('Cessna 152')
type_c172 = at('Cessna 172')
type_pa28 = at('Piper PA-28 Warrior')
type_pa38 = at('PA38 Tomahawk')
type_da40 = at('Diamond DA40')
type_gr2  = at('Grumman AA-5B Tiger')

# ── Aircraft (10 total) ───────────────────────────────────────────────────────
head('Aircraft')
AIRCRAFT_DATA = [
    # reg, type, method, seats, status, hobbs_init, tacho_init, year_note
    ('ZK-TAW', type_c152, 'hobbs', 2, 'online',  4820.0, None),
    ('ZK-EKE', type_c172, 'hobbs', 4, 'online',  3210.0, None),
    ('ZK-TWR', type_pa38, 'hobbs', 2, 'online',  5640.0, None),
    ('ZK-WAC', type_pa28, 'tacho', 4, 'online', None, 3980.0),
    ('ZK-MGA', type_c172, 'hobbs', 4, 'online',  6100.0, None),
    ('ZK-GHX', type_c152, 'hobbs', 2, 'online',  7230.0, None),
    ('ZK-NEP', type_da40, 'hobbs', 4, 'online',  1840.0, None),
    ('ZK-BFR', type_pa28, 'tacho', 4, 'online', None, 4510.0),
    ('ZK-JEZ', type_gr2,  'hobbs', 4, 'online',  9120.0, None),
    ('ZK-OLD', type_c152, 'hobbs', 2, 'retired', 12400.0, None),
]

aircraft = {}
for reg, atype, method, seats, status, hobbs_init, tacho_init in AIRCRAFT_DATA:
    if DRY:
        log(f'Aircraft: {reg} ({atype})')
        aircraft[reg] = None
        continue
    ac, created = Aircraft.objects.get_or_create(
        club=club, registration=reg,
        defaults=dict(
            aircraft_type=atype, total_time_method=method,
            maint_time_source=method,
            maint_time_fraction=D('0.95') if 'tacho' in method else D('1.0'),
            seats=seats, status=status,
            hobbs_initial=D(str(hobbs_init)) if hobbs_init else None,
            tacho_initial=D(str(tacho_init)) if tacho_init else None,
            records_hobbs=(method == 'hobbs'),
            records_tacho=('tacho' in method),
        )
    )
    if not created:
        # Update key fields on existing aircraft
        ac.aircraft_type = atype
        ac.total_time_method = method
        if hobbs_init and not ac.hobbs_initial:
            ac.hobbs_initial = D(str(hobbs_init))
        if tacho_init and not ac.tacho_initial:
            ac.tacho_initial = D(str(tacho_init))
        ac.status = status
        ac.save()
    aircraft[reg] = ac
    log(f'{"+" if created else "="} {reg} {status}')

# ── Flight types ──────────────────────────────────────────────────────────────
head('Flight types')
FT_DATA = [
    ('Student Dual',     False, False, True),
    ('Student Solo',     True,  False, True),
    ('Private Hire',     True,  True,  True),
    ('Trial Flight',     False, False, True),
    ('Solo Hire',        True,  False, True),
    ('Staff Training Dual', False, False, True),
    ('Club Check',       False, False, False),
    ('Ferry Flight',     True,  False, False),
]
ft = {}
for name, solo, decl, bill in FT_DATA:
    if DRY:
        ft[name] = None; log(f'FT: {name}'); continue
    existing = FlightType.objects.filter(club=club, name=name)
    if existing.count() > 1:
        # Deduplicate — keep first, delete rest
        keep = existing.first()
        existing.exclude(pk=keep.pk).delete()
        obj, c = keep, False
    else:
        obj, c = FlightType.objects.get_or_create(
            club=club, name=name,
            defaults=dict(is_solo=solo, requires_declaration=decl, is_billable=bill, is_training=(not solo and bill))
        )
    ft[name] = obj
    log(f'{"+" if c else "="} {name}')

# ── Charge rates ──────────────────────────────────────────────────────────────
head('Charge rates')
# (aircraft_reg, flight_type_name, rate, method, includes_fuel)
RATES = [
    # Cessna 152s — trainer
    ('ZK-TAW', 'Student Dual',  165, 'hobbs', False),
    ('ZK-TAW', 'Student Solo',  145, 'hobbs', False),
    ('ZK-TAW', 'Trial Flight',  165, 'hobbs', False),
    ('ZK-TAW', 'Club Check',    145, 'hobbs', False),
    ('ZK-GHX', 'Student Dual',  160, 'hobbs', False),
    ('ZK-GHX', 'Student Solo',  140, 'hobbs', False),
    # Cessna 172s — trainer/hire
    ('ZK-EKE', 'Student Dual',  195, 'hobbs', False),
    ('ZK-EKE', 'Student Solo',  175, 'hobbs', False),
    ('ZK-EKE', 'Private Hire',  185, 'hobbs', False),
    ('ZK-EKE', 'Solo Hire',     175, 'hobbs', False),
    ('ZK-EKE', 'Trial Flight',  195, 'hobbs', False),
    ('ZK-MGA', 'Student Dual',  195, 'hobbs', False),
    ('ZK-MGA', 'Student Solo',  175, 'hobbs', False),
    ('ZK-MGA', 'Private Hire',  185, 'hobbs', False),
    # PA-28 Warriors — hire
    ('ZK-WAC', 'Private Hire',  210, 'tacho', False),
    ('ZK-WAC', 'Solo Hire',     200, 'tacho', False),
    ('ZK-BFR', 'Private Hire',  215, 'tacho', False),
    ('ZK-BFR', 'Solo Hire',     205, 'tacho', False),
    # PA-38 Tomahawk
    ('ZK-TWR', 'Student Dual',  170, 'hobbs', False),
    ('ZK-TWR', 'Student Solo',  150, 'hobbs', False),
    # Diamond DA40
    ('ZK-NEP', 'Private Hire',  260, 'hobbs', False),
    ('ZK-NEP', 'Solo Hire',     245, 'hobbs', False),
    # Tiger
    ('ZK-JEZ', 'Private Hire',  230, 'hobbs', False),
    ('ZK-JEZ', 'Solo Hire',     215, 'hobbs', False),
]
for reg, ft_name, rate, method, fuel in RATES:
    if DRY or not aircraft.get(reg) or not ft.get(ft_name): continue
    obj, c = ChargeRate.objects.get_or_create(
        aircraft=aircraft[reg], flight_type=ft[ft_name], time_method=method,
        defaults=dict(club=club, amount=D(str(rate)), includes_fuel=fuel)
    )
    if not c:
        obj.amount = D(str(rate)); obj.save()
    log(f'{"+" if c else "="} {reg} {ft_name} ${rate}/hr')

# ── Aerodromes & landing fees ─────────────────────────────────────────────────
head('Aerodromes')
AERODROME_DATA = [
    ('NZWN', 'Wellington International', [('Landing',    35, 1), ('T&G',       25, 1)]),
    ('NZMS', 'Masterton',                [('Landing',    15, 1), ('T&G',        10, 1)]),
    ('NZPP', 'Paraparaumu',              [('Landing',    20, 1), ('T&G',        15, 1)]),
    ('NZNH', 'Nelson',                   [('Landing',    25, 1), ('T&G',        18, 1)]),
    ('NZPM', 'Palmerston North',         [('Landing',    20, 1), ('T&G',        14, 1)]),
]
aerodromes = {}
for icao, name, fees in AERODROME_DATA:
    if DRY:
        log(f'Aerodrome: {icao} {name}'); aerodromes[icao] = None; continue
    ae, c = Aerodrome.objects.get_or_create(club=club, icao_code=icao, defaults={'name': name, 'is_active': True})
    aerodromes[icao] = ae
    for fee_name, amount, qty in fees:
        AerodromeFeeType.objects.get_or_create(
            aerodrome=ae, name=fee_name,
            defaults={'default_amount': D(str(amount))}
        )
    log(f'{"+" if c else "="} {icao} {name}')

# ── Members (50 total) ────────────────────────────────────────────────────────
head('Members (50)')

MEMBER_DATA = [
    # (username, first, last, role_key, standing, is_instructor, grade_key, is_admin)
    # Existing preserved — only add new ones
    # Instructors (5)
    ('jane',      'Jane',     'Park',      'Instructor', 'active', True,  'B-Cat', False),
    ('richard',   'Richard',  'Thomas',    'Instructor', 'active', True,  'C-Cat', False),
    ('helen',     'Helen',    'Watts',     'Instructor', 'active', True,  'B-Cat', False),
    ('peter_i',   'Peter',    'Inglis',    'Instructor', 'active', True,  'C-Cat', False),
    ('sarah_i',   'Sarah',    'Bright',    'Instructor', 'active', True,  'A-Cat', False),
    # Students (20)
    ('sean',      'Sean',     'Kemp',      'Student',    'active', False, None, False),
    ('mike',      'Mike',     'Lowe',      'Student',    'suspended', False, None, False),
    ('alex',      'Alex',     'Reed',      'Student',    'active', False, None, False),
    ('rita',      'Rita',     'Singh',     'Student',    'active', False, None, False),
    ('tom_b',     'Tom',      'Barrett',   'Student',    'active', False, None, False),
    ('lucy_w',    'Lucy',     'Wallace',   'Student',    'active', False, None, False),
    ('james_c',   'James',    'Carter',    'Student',    'active', False, None, False),
    ('emma_h',    'Emma',     'Hughes',    'Student',    'lapsed', False, None, False),
    ('david_f',   'David',    'Forde',     'Student',    'active', False, None, False),
    ('kate_m',    'Kate',     'Morrison',  'Student',    'active', False, None, False),
    ('ben_r',     'Ben',      'Rogers',    'Student',    'active', False, None, False),
    ('sophie_a',  'Sophie',   'Andrews',   'Student',    'active', False, None, False),
    ('liam_g',    'Liam',     'Grant',     'Student',    'active', False, None, False),
    ('chloe_d',   'Chloe',    'Dawson',    'Student',    'active', False, None, False),
    ('jack_s',    'Jack',     'Stewart',   'Student',    'active', False, None, False),
    ('olivia_t',  'Olivia',   'Taylor',    'Student',    'active', False, None, False),
    ('noah_p',    'Noah',     'Phillips',  'Student',    'active', False, None, False),
    ('ava_w',     'Ava',      'Wilson',    'Student',    'active', False, None, False),
    ('ethan_c',   'Ethan',    'Collins',   'Student',    'active', False, None, False),
    ('mia_b',     'Mia',      'Brown',     'Student',    'active', False, None, False),
    # Private hire / solo members (20)
    ('tom_hire',  'Tom',      'Henderson', 'Private Hire', 'active', False, None, False),
    ('fiona_h',   'Fiona',    'Harrison',  'Private Hire', 'active', False, None, False),
    ('raj_k',     'Raj',      'Kumar',     'Private Hire', 'active', False, None, False),
    ('lisa_m',    'Lisa',     'Mitchell',  'Private Hire', 'active', False, None, False),
    ('chris_n',   'Chris',    'Nelson',    'Private Hire', 'active', False, None, False),
    ('amy_o',     'Amy',      'Owen',      'Private Hire', 'active', False, None, False),
    ('dan_p',     'Dan',      'Parker',    'Private Hire', 'active', False, None, False),
    ('mel_q',     'Melanie',  'Quinn',     'Private Hire', 'active', False, None, False),
    ('rob_r',     'Rob',      'Roberts',   'Private Hire', 'active', False, None, False),
    ('sue_s',     'Sue',      'Sanders',   'Private Hire', 'active', False, None, False),
    ('paul_t',    'Paul',     'Turner',    'Private Hire', 'active', False, None, False),
    ('ann_u',     'Ann',      'Underwood', 'Private Hire', 'active', False, None, False),
    ('carl_v',    'Carl',     'Vickers',   'Private Hire', 'active', False, None, False),
    ('donna_w',   'Donna',    'Watson',    'Private Hire', 'active', False, None, False),
    ('ian_x',     'Ian',      'Xavier',    'Private Hire', 'active', False, None, False),
    ('jean_y',    'Jean',     'Young',     'Private Hire', 'active', False, None, False),
    ('ken_z',     'Ken',      'Zhang',     'Member',       'active', False, None, False),
    ('laura_a',   'Laura',    'Adams',     'Member',       'active', False, None, False),
    ('mark_b',    'Mark',     'Bailey',    'Member',       'active', False, None, False),
    ('nina_c',    'Nina',     'Clark',     'Member',       'lapsed', False, None, False),
    ('oscar_e',   'Oscar',    'Evans',     'Member',       'active', False, None, False),
    # Admin (keeping dominic)
    ('dominic',   'Dominic',  'Donald',    'Administrator', 'active', False, None, True),
]

grades = {}
if not DRY:
    grades = {g.name: g for g in InstructorGrade.objects.filter(club=club)}
roles_map = {}
if not DRY:
    roles_map = {r.name: r for r in Role.objects.filter(club=club)}

members = {}
for username, first, last, role_key, standing, is_instr, grade_key, is_admin in MEMBER_DATA:
    if DRY:
        log(f'Member: {first} {last} ({username}) {role_key}')
        members[username] = None
        continue
    user, uc = User.objects.get_or_create(username=username, defaults={
        'first_name': first, 'last_name': last,
        'email': f'{username}@example.co.nz', 'is_staff': is_admin,
    })
    if not uc:
        user.first_name = first; user.last_name = last; user.save()
    user.set_password('clubhangar2026')
    user.save()

    role_obj = roles_map.get(role_key)
    grade_obj = grades.get(grade_key) if grade_key else None

    cm, mc = ClubMember.objects.get_or_create(user=user, club=club, defaults={
        'role': role_obj, 'standing': standing,
        'is_on_instructor_roster': is_instr,
        'instructor_grade': grade_obj,
        'has_admin_access': is_admin,
    })
    if not mc:
        cm.standing = standing
        cm.is_on_instructor_roster = is_instr
        cm.instructor_grade = grade_obj
        cm.has_admin_access = is_admin
        if role_obj: cm.role = role_obj
        cm.save()

    members[username] = cm
    log(f'{"+" if mc else "="} {first} {last} ({role_key}, {standing})')

# ── Instructor credentials ────────────────────────────────────────────────────
head('Instructor credentials')
# Each rostered instructor gets: instructor cert, medical, and current flight review.
# Expiry dates are staggered so the demo shows a mix of near-expiry and current.
# username → (instr_grade_type, medical_type, instr_expiry_offset_days, medical_expiry_offset_days, fr_expiry_offset_days)
INSTR_CRED_DATA = {
    'jane':    (CredentialType.INSTRUCTOR_B, CredentialType.MEDICAL_C1,  730,  365, 500),
    'richard': (CredentialType.INSTRUCTOR_C, CredentialType.MEDICAL_C2,  400,  180, 300),
    'helen':   (CredentialType.INSTRUCTOR_A, CredentialType.MEDICAL_C1,  900,  270, 600),
    'sarah_i': (CredentialType.INSTRUCTOR_C, CredentialType.MEDICAL_C2,  200,   90, 100),  # near-expiry for demo
    'peter_i': (CredentialType.INSTRUCTOR_B, CredentialType.MEDICAL_C1,  600,  365, 400),
}
if not DRY:
    _cred_creator = User.objects.filter(is_superuser=True).first()
    for username, (instr_type, med_type, instr_days, med_days, fr_days) in INSTR_CRED_DATA.items():
        cm = members.get(username)
        if not cm:
            continue
        today_d = NOW.date()
        for ctype, days in [
            (instr_type, instr_days),
            (med_type,   med_days),
            (CredentialType.FLIGHT_REVIEW, fr_days),
        ]:
            MemberCredential.objects.get_or_create(
                club_member=cm, credential_type=ctype,
                defaults=dict(
                    issue_date=today_d - timedelta(days=730),
                    expiry_date=today_d + timedelta(days=days),
                    created_by=_cred_creator,
                ),
            )
        log(f'  {cm.user.get_full_name()} — {instr_type} / {med_type} / FR')

# ── Instructor availability windows ───────────────────────────────────────────
head('Instructor availability')
if not DRY:
    # Full-time instructors: Jane, Richard, Helen — available Mon-Sun
    for username in ('jane', 'richard', 'helen'):
        cm = members.get(username)
        if cm:
            for day in range(7):
                InstructorAvailability.objects.get_or_create(
                    club_member=cm, recurrence='weekly', weekday=day,
                    defaults={'all_day': False, 'start_time': time(7, 0), 'end_time': time(18, 0)}
                )
            log(f'{cm.user.first_name}: Mon-Sun')
    # Part-time instructors: Peter Inglis (weekends only), Sarah Bright (Tue-Thu + weekends)
    peter = members.get('peter_i')
    sarah = members.get('sarah_i')
    if peter:
        for day in [5, 6]:  # Sat, Sun
            InstructorAvailability.objects.get_or_create(
                club_member=peter, recurrence='weekly', weekday=day,
                defaults={'all_day': False, 'start_time': time(8,30), 'end_time': time(17,0)}
            )
        log('Peter Inglis: weekends only')
    if sarah:
        for day in [1, 2, 3, 5, 6]:  # Tue-Thu + Sat-Sun
            InstructorAvailability.objects.get_or_create(
                club_member=sarah, recurrence='weekly', weekday=day,
                defaults={'all_day': False, 'start_time': time(8,30), 'end_time': time(17,0)}
            )
        log('Sarah Bright: Tue-Thu + weekends')


# ── Maintenance items ─────────────────────────────────────────────────────────
head('Maintenance items')
# One set of items for each online aircraft
MAINT_ITEMS = [
    # (name, interval_hrs, warn_hrs, interval_days, warn_days)
    ('100-hour inspection', 100,  5, None, None),
    ('Oil & filter change',  50,  5, None, None),
    ('Annual CofA',         None, None, 365, 30),
    ('Prop overhaul',       500, 20, None, None),
]
if not DRY:
    for reg, ac_obj in aircraft.items():
        if ac_obj and ac_obj.status == 'online':
            base_hrs = float(ac_obj.hobbs_initial or ac_obj.tacho_initial or D('1000'))
            for name, int_hrs, warn_hrs, int_days, warn_days in MAINT_ITEMS:
                due_hrs = D(str(round(base_hrs + int_hrs, 1))) if int_hrs else None
                due_date_val = (TODAY + timedelta(days=int_days)) if int_days else None
                AircraftMaintenanceItem.objects.get_or_create(
                    aircraft=ac_obj, name=name,
                    defaults=dict(
                        interval_hours=D(str(int_hrs)) if int_hrs else None,
                        due_hours=due_hrs,
                        interval_days=int_days,
                        due_date=due_date_val,
                        warn_hours=D(str(warn_hrs)) if warn_hrs else None,
                        warn_days=warn_days,
                    )
                )
            log(f'{reg}: 4 maintenance items')

# ── Past flights with realistic Hobbs sequences ───────────────────────────────
head('Past flights')

# Hobbs cursor per aircraft (start from initial values)
if not DRY:
    hobbs = {reg: float(ac.hobbs_initial or D('1000')) for reg, ac in aircraft.items() if ac and ac.status == 'online'}
    tacho = {'ZK-WAC': 3980.0, 'ZK-BFR': 4510.0}

    # Helper: lookup member by username
    def m(username): return members.get(username)
    def ac(reg): return aircraft.get(reg)

    admin_user = members['dominic'].user

    def make_flight(reg, member_un, ft_name, instr_un, hours, days_ago,
                    outcome='completed', paid_method=None, extra_charge=None,
                    landing_aerodrome=None):
        """Create a complete past flight with charges, maintenance log, and optional payment."""
        if not aircraft.get(reg) or not members.get(member_un):
            return None
        ac_obj  = aircraft[reg]
        mem_obj = members[member_un]
        ft_obj  = ft.get(ft_name)
        instr   = members[instr_un].user if instr_un and members.get(instr_un) else None

        # Pin start to 10:00 AM local on that date — always within operating hours
        start_dt = NOW.replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
        end_dt   = start_dt + timedelta(hours=hours + 0.5)

        b = Booking.objects.create(
            club=club, member=mem_obj, aircraft=ac_obj, flight_type=ft_obj,
            instructor=instr, status='completed',
            scheduled_start=start_dt, scheduled_end=end_dt,
            departed_at=start_dt, arrived_at=start_dt + timedelta(hours=hours + 0.2),
            created_by=admin_user,
        )

        # Meter readings
        uses_hobbs = ac_obj.total_time_method == 'hobbs'
        uses_tacho = 'tacho' in ac_obj.total_time_method

        h_start = hobbs.get(reg, 1000.0) if uses_hobbs else None
        h_end   = round(h_start + hours, 1) if uses_hobbs else None
        t_start = tacho.get(reg, 1000.0) if uses_tacho else None
        t_end   = round(t_start + hours, 2) if uses_tacho else None

        fc = FlightCompletion.objects.create(
            booking=b, outcome=outcome, logged_by=admin_user,
            hobbs_start=D(str(h_start)) if h_start else None,
            hobbs_end=D(str(h_end)) if h_end else None,
            tacho_start=D(str(t_start)) if t_start else None,
            tacho_end=D(str(t_end)) if t_end else None,
            actual_flight_hours=D(str(hours)),
            departed_with_aircraft=ac_obj,
            departed_with_instructor=instr,
        )

        # Update cursors
        if uses_hobbs and h_end: hobbs[reg] = h_end
        if uses_tacho and t_end: tacho[reg] = t_end

        # Charges
        rate_obj = ChargeRate.objects.filter(
            aircraft=ac_obj, flight_type=ft_obj,
            time_method=ac_obj.total_time_method
        ).first()
        total = D('0')
        if rate_obj and hours > 0:
            hire_amt = round(float(rate_obj.amount) * hours, 2)
            FlightChargeItem.objects.create(
                flight_completion=fc, item_type='hire',
                description=f'Aircraft hire — {reg}',
                amount=D(str(hire_amt))
            )
            total += D(str(hire_amt))

        # Instructor fee
        if instr and members.get(instr_un):
            ig_obj = members[instr_un].instructor_grade
            if ig_obj and hours > 0:
                fee = round(float(ig_obj.hourly_rate) * hours, 2)
                FlightChargeItem.objects.create(
                    flight_completion=fc, item_type='instructor',
                    description=f'Instructor fee — {members[instr_un].user.get_full_name()}',
                    amount=D(str(fee))
                )
                total += D(str(fee))

        # Landing fee
        if landing_aerodrome and aerodromes.get(landing_aerodrome):
            ae_obj = aerodromes[landing_aerodrome]
            lf_tier = AerodromeFeeType.objects.filter(aerodrome=ae_obj, name='Landing').first()
            if lf_tier:
                FlightChargeItem.objects.create(
                    flight_completion=fc, item_type='landing',
                    description=f'Landing fee — {landing_aerodrome}',
                    amount=lf_tier.default_amount
                )
                total += lf_tier.default_amount

        # Extra one-off charge
        if extra_charge:
            FlightChargeItem.objects.create(
                flight_completion=fc, item_type='one_off',
                description=extra_charge[0], amount=D(str(extra_charge[1]))
            )
            total += D(str(extra_charge[1]))

        fc.total_charge = total
        fc.save(update_fields=['total_charge'])

        # Maintenance log
        create_maint_log_entry(fc)

        # Payment
        if paid_method and total > 0:
            acct, _ = Account.objects.get_or_create(club_member=mem_obj, defaults={'balance': D('0')})
            fp = FlightPayment.objects.create(
                completion=fc, member=mem_obj, amount=total,
                method=paid_method, paid_at=NOW - timedelta(days=days_ago - 1),
                recorded_by=admin_user,
            )
            if paid_method == 'credit':
                AccountTransaction.objects.create(
                    account=acct, transaction_type='flight', direction='debit',
                    amount=total, flight_completion=fc, payment_method='credit',
                    description=f'Flight {reg} {b.scheduled_start.date()}',
                    created_by=admin_user,
                )
                acct.apply_transaction(total, 'debit')
            fc._sync_payment_cache()

        log(f'Flight: {reg} {ft_name} {hours}h d-{days_ago} {"paid" if paid_method else "unpaid"} total=${total}')
        return b

    # ── Seed some account credit for members ──────────────────────────────────
    def top_up(username, amount):
        mem = members.get(username)
        if not mem: return
        acct, _ = Account.objects.get_or_create(club_member=mem, defaults={'balance': D('0')})
        AccountTransaction.objects.get_or_create(
            account=acct, transaction_type='deposit', direction='credit',
            amount=D(str(amount)),
            defaults=dict(
                description='Opening credit deposit',
                created_by=admin_user, payment_method='eftpos',
            )
        )
        acct.apply_transaction(D(str(amount)), 'credit')
        log(f'Top-up: {username} +${amount}')

    top_up('tom_hire', 500)
    top_up('fiona_h', 300)
    top_up('raj_k',   400)
    top_up('lisa_m',  250)
    top_up('chris_n', 600)
    top_up('sean',    200)

    # ── Past flights ──────────────────────────────────────────────────────────
    # Format: reg, member, flight_type, instructor, hours, days_ago, paid_method
    make_flight('ZK-TAW', 'sean',     'Student Dual', 'jane',   1.3, 45, paid_method='eftpos')
    make_flight('ZK-TAW', 'lucy_w',   'Student Dual', 'jane',   1.1, 42, paid_method='eftpos')
    make_flight('ZK-EKE', 'tom_hire', 'Private Hire', None,     2.2, 40, paid_method='credit')
    make_flight('ZK-TWR', 'james_c',  'Student Dual', 'richard',1.4, 38, paid_method='eftpos')
    make_flight('ZK-WAC', 'fiona_h',  'Private Hire', None,     1.8, 36, paid_method='credit')
    make_flight('ZK-TAW', 'david_f',  'Student Dual', 'helen',  1.2, 34, paid_method='eftpos', landing_aerodrome='NZMS')
    make_flight('ZK-EKE', 'raj_k',    'Private Hire', None,     2.5, 32, paid_method='credit')
    make_flight('ZK-GHX', 'kate_m',   'Student Dual', 'peter_i',1.0, 30, paid_method='eftpos')
    make_flight('ZK-MGA', 'lisa_m',   'Private Hire', None,     1.6, 28, paid_method='credit')
    make_flight('ZK-TAW', 'ben_r',    'Student Dual', 'jane',   1.3, 25, paid_method='eftpos')
    make_flight('ZK-EKE', 'chris_n',  'Private Hire', None,     3.0, 22, paid_method='credit', landing_aerodrome='NZNH')
    make_flight('ZK-NEP', 'dan_p',    'Private Hire', None,     2.1, 20, paid_method='eftpos')
    make_flight('ZK-WAC', 'rob_r',    'Private Hire', None,     1.4, 18, paid_method='eftpos')
    make_flight('ZK-TAW', 'sophie_a', 'Student Dual', 'richard',1.2, 15, paid_method='eftpos')
    make_flight('ZK-GHX', 'liam_g',   'Student Dual', 'sarah_i',1.5, 14, paid_method='eftpos')
    make_flight('ZK-EKE', 'paul_t',   'Solo Hire',    None,     1.8, 12, paid_method='eftpos')
    make_flight('ZK-MGA', 'tom_hire', 'Private Hire', None,     2.0, 10, paid_method='credit')
    make_flight('ZK-TAW', 'chloe_d',  'Student Dual', 'jane',   1.1,  8, paid_method='eftpos')
    make_flight('ZK-BFR', 'fiona_h',  'Private Hire', None,     1.7,  7, paid_method='credit')
    make_flight('ZK-EKE', 'alex',     'Student Solo', None,     1.3,  6, paid_method='eftpos')
    make_flight('ZK-TWR', 'jack_s',   'Student Dual', 'helen',  1.0,  5, paid_method='eftpos')
    make_flight('ZK-GHX', 'noah_p',   'Student Dual', 'richard',1.2,  4, paid_method='eftpos')
    # Unpaid — awaiting payment at the desk
    make_flight('ZK-WAC', 'raj_k',    'Private Hire', None,     2.2,  2, paid_method=None)
    make_flight('ZK-EKE', 'sean',     'Student Dual', 'jane',   1.4,  1, paid_method=None,
                extra_charge=('Pre-flight briefing materials', 15))

    # ── One currently departed (in flight now) ────────────────────────────────
    dep_mem  = members.get('tom_hire')
    dep_ac   = aircraft.get('ZK-NEP')
    dep_ft   = ft.get('Private Hire')
    dep_instr = None
    if dep_mem and dep_ac and dep_ft:
        dep_start = NOW - timedelta(hours=1, minutes=20)
        dep_end   = dep_start + timedelta(hours=3)
        dep_b = Booking.objects.create(
            club=club, member=dep_mem, aircraft=dep_ac, flight_type=dep_ft,
            instructor=dep_instr, status='departed',
            scheduled_start=dep_start, scheduled_end=dep_end,
            departed_at=dep_start, created_by=admin_user,
        )
        from core.models import FuelSurchargeRate
        fuel_r = FuelSurchargeRate.current_rate(club, dep_ac)
        FlightCompletion.objects.create(
            booking=dep_b, logged_by=admin_user,
            fuel_surcharge_rate_snapshot=fuel_r.rate if fuel_r else None,
            departed_with_aircraft=dep_ac,
            departed_with_instructor=dep_instr,
        )
        log(f'Departed: ZK-NEP (Tom Henderson) — currently in flight')

    # ── Upcoming bookings (next 14 days) ─────────────────────────────────────
    head('Upcoming bookings')

    def book_future(reg, member_un, ft_name, instr_un, days_ahead, hour_start, duration):
        if not aircraft.get(reg) or not members.get(member_un): return
        start = NOW.replace(hour=hour_start, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
        end   = start + timedelta(hours=duration)
        instr = members[instr_un].user if instr_un and members.get(instr_un) else None
        b = Booking.objects.create(
            club=club, member=members[member_un], aircraft=aircraft[reg],
            flight_type=ft.get(ft_name), instructor=instr,
            status='confirmed', scheduled_start=start, scheduled_end=end,
            created_by=admin_user,
            confirmed_by=admin_user, confirmed_at=NOW - timedelta(hours=2),
        )
        log(f'Upcoming: {reg} {member_un} in {days_ahead}d ({hour_start}:00 +{duration}h)')

    book_future('ZK-TAW', 'sean',     'Student Dual',  'jane',     1,  9, 1.5)
    book_future('ZK-GHX', 'lucy_w',   'Student Dual',  'richard',  1, 11, 1.5)
    book_future('ZK-EKE', 'tom_hire', 'Private Hire',  None,       1, 14, 2.5)
    book_future('ZK-WAC', 'fiona_h',  'Private Hire',  None,       2,  9, 2.0)
    book_future('ZK-TWR', 'james_c',  'Student Dual',  'helen',    2, 11, 1.5)
    book_future('ZK-MGA', 'raj_k',    'Private Hire',  None,       2, 14, 2.0)
    book_future('ZK-TAW', 'david_f',  'Student Dual',  'jane',     3,  9, 1.5)
    book_future('ZK-EKE', 'chris_n',  'Private Hire',  None,       3, 14, 3.0)
    book_future('ZK-GHX', 'ben_r',    'Student Dual',  'peter_i',  4, 11, 1.5)
    book_future('ZK-NEP', 'dan_p',    'Private Hire',  None,       5,  9, 2.5)
    book_future('ZK-BFR', 'lisa_m',   'Private Hire',  None,       6, 10, 2.0)
    book_future('ZK-TAW', 'kate_m',   'Student Dual',  'sarah_i',  7,  9, 1.5)
    book_future('ZK-MGA', 'paul_t',   'Solo Hire',     None,       7, 14, 1.5)
    book_future('ZK-EKE', 'sophie_a', 'Student Solo',  None,       8, 11, 1.5)
    book_future('ZK-WAC', 'rob_r',    'Private Hire',  None,      10,  9, 2.0)

# ── Contact types ─────────────────────────────────────────────────────────────
head('Contact types')
CT_DATA = [
    ('Trial flight',      0),
    ('Young Eagles',      1),
    ('Gateway Scheme',    2),
    ('Commercial client', 3),
    ('Guest',             4),
    ('Other',             5),
]
ct = {}
for ct_name, ct_order in CT_DATA:
    if DRY:
        log(f'ContactType: {ct_name}'); continue
    obj, created = ContactType.objects.get_or_create(
        club=club, name=ct_name,
        defaults=dict(sort_order=ct_order, is_active=True),
    )
    ct[ct_name] = obj
    log(f'{"+" if created else "="} {ct_name}')

# ── Contacts (non-member clients) ────────────────────────────────────────────
head('Contacts')
CONTACT_DATA = [
    # (name, email, phone, is_org, organisation, contact_type_name, notes)
    ('Jamie Tane',          'jamie.tane@example.com',       '021 555 0101', False, 'Wellington East School',  'Young Eagles',      'Year 9 student, Young Eagles programme'),
    ('Aroha Ngata',         '',                             '021 555 0102', False, 'Wellington East School',  'Young Eagles',      'Year 10 student'),
    ('Liam Cooper',         'liam.cooper@example.com',      '021 555 0103', False, '',                        'Trial flight',      'Paid trial flight, interested in joining'),
    ('Emma Walsh',          'emma.walsh@example.com',       '027 555 0104', False, '',                        'Trial flight',      'Birthday gift from family — converted to member'),
    ('Wellington East School', 'admin@wellingtoneast.school.nz', '04 555 0200', True,  '',                    'Young Eagles',      'Young Eagles sponsor — invoiced per term'),
    ('Kapiti Air Services', 'ops@kapitiair.co.nz',          '04 555 0300', True,  '',                        'Commercial client', 'Buys avgas from club. Invoiced monthly.'),
    ('Marcus Bright',       'marcus.bright@example.com',    '021 555 0105', False, '',                        'Trial flight',      'Walk-in trial flight enquiry'),
    ('Sunrise Aviation Trust', 'admin@sunriseaviation.org.nz', '04 555 0400', True, '',                       'Gateway Scheme',    'Gateway Scheme sponsor organisation'),
]
contacts_seed = {}
admin_user = User.objects.filter(is_superuser=True).first()
for name, email, phone, is_org, org, ctype_name, notes in CONTACT_DATA:
    if DRY:
        log(f'Contact: {name}'); continue
    c, created = Contact.objects.get_or_create(
        club=club, name=name,
        defaults=dict(email=email, phone=phone, is_organisation=is_org,
                      organisation=org, contact_type=ct.get(ctype_name),
                      notes=notes, created_by=admin_user),
    )
    contacts_seed[name] = c
    log(f'{"+" if created else "="} {name} ({ctype_name}{"  ORG" if is_org else ""})')

# Attach some contacts to existing trial-flight bookings and mark one converted
if not DRY and contacts_seed:
    from core.services import booking_service as _bs
    trial_ft = ft.get('Trial Flight')
    jamie    = contacts_seed.get('Jamie Tane')
    school   = contacts_seed.get('Wellington East School')
    liam     = contacts_seed.get('Liam Cooper')
    marcus   = contacts_seed.get('Marcus Bright')

    # Find an instructor member
    instr_m = ClubMember.objects.filter(club=club, is_on_instructor_roster=True).first()

    def _make_trial(contact, billed_to, days_ago=30):
        """Create a completed trial flight booking with FlightCompletion for a contact."""
        ac = Aircraft.objects.filter(club=club, status='online').first()
        if not (ac and instr_m and trial_ft):
            return
        from django.utils import timezone as _tz
        start = NOW.replace(hour=14, minute=0, second=0, microsecond=0) - timezone.timedelta(days=days_ago)
        arrived = start + timezone.timedelta(hours=1)
        b = Booking.objects.create(
            club=club,
            member=instr_m,
            client=contact,
            billed_to=billed_to,
            aircraft=ac,
            flight_type=trial_ft,
            instructor=instr_m.user,
            status='completed',
            scheduled_start=start,
            scheduled_end=arrived,
            departed_at=start + timezone.timedelta(minutes=5),
            arrived_at=arrived,
            created_by=instr_m.user,
        )

        # Get current hobbs from last FlightCompletion or fall back to initial
        last_fc = (FlightCompletion.objects
                   .filter(booking__aircraft=ac, hobbs_end__isnull=False)
                   .order_by('-booking__arrived_at').first())
        h_start = float(last_fc.hobbs_end) if last_fc else float(ac.hobbs_initial or 0)
        h_end   = round(h_start + 1.0, 1)

        fc = FlightCompletion.objects.create(
            booking=b, outcome='completed', logged_by=admin_user,
            hobbs_start=D(str(h_start)), hobbs_end=D(str(h_end)),
            actual_flight_hours=D('1.0'),
            departed_with_aircraft=ac,
            departed_with_instructor=instr_m.user,
        )

        # Hire charge
        rate_obj = ChargeRate.objects.filter(
            aircraft=ac, flight_type=trial_ft, time_method=ac.total_time_method
        ).first()
        total = D('0')
        if rate_obj:
            hire_amt = D(str(round(float(rate_obj.amount) * 1.0, 2)))
            FlightChargeItem.objects.create(
                flight_completion=fc, item_type='hire',
                description=f'Aircraft hire — {ac.registration}',
                amount=hire_amt,
            )
            total += hire_amt

        # Club-absorbed flights have no charge to the payer
        if billed_to == Booking.BILLED_CLUB:
            total = D('0')
            fc.charge_items.all().delete()

        fc.total_charge = total
        fc.save(update_fields=['total_charge'])

        # Mark as paid
        if total == D('0'):
            # Zero-charge: is_paid is True automatically
            fc.paid_at = arrived
            fc.save(update_fields=['paid_at'])
        else:
            pay_method = 'invoice' if billed_to == Booking.BILLED_ORGANISATION else 'eftpos'
            acct, _ = Account.objects.get_or_create(
                club_member=instr_m, defaults={'balance': D('0')}
            )
            FlightPayment.objects.create(
                completion=fc, member=instr_m, amount=total,
                method=pay_method, paid_at=arrived, recorded_by=admin_user,
            )
            fc._sync_payment_cache()

        return b

    if jamie and school:
        b1 = _make_trial(jamie, Booking.BILLED_ORGANISATION, days_ago=45)
        if b1: log(f'  Trial booking for {jamie.name} (billed to org)')

    if liam:
        b2 = _make_trial(liam, Booking.BILLED_CONTACT, days_ago=20)
        if b2: log(f'  Trial booking for {liam.name} (client pays)')

    if marcus:
        b3 = _make_trial(marcus, Booking.BILLED_CLUB, days_ago=10)
        if b3: log(f'  Trial booking for {marcus.name} (club absorbs)')

    # Mark Emma Walsh as converted to member (find a suitable member)
    emma = contacts_seed.get('Emma Walsh')
    if emma and not emma.converted_to_member:
        converted_m = ClubMember.objects.filter(
            club=club, is_on_instructor_roster=False
        ).exclude(has_admin_access=True).last()
        if converted_m:
            emma.converted_to_member = converted_m
            emma.save(update_fields=['converted_to_member'])
            log(f'  Emma Walsh → converted to member {converted_m.user.get_full_name()}')


# ── Flying budget (current FY, per online aircraft) ──────────────────────────
head('Flying budget')
if not DRY:
    from datetime import date as _date
    _cfg = ClubConfig.objects.get(club=club)
    _today = _date.today()
    _fy_year = _today.year if _today.month >= _cfg.fy_start_month else _today.year - 1
    # Monthly budget hours per aircraft registration (slightly varied to look realistic)
    _BUDGETS = {
        'ZK-TAW': [28, 30, 32, 30, 28, 25, 22, 25, 28, 30, 32, 30],  # C152 trainer
        'ZK-EKE': [22, 24, 26, 24, 22, 20, 18, 20, 22, 24, 26, 24],  # C172
        'ZK-TWR': [20, 22, 24, 22, 20, 18, 16, 18, 20, 22, 24, 22],  # PA38
        'ZK-WAC': [16, 18, 20, 18, 16, 14, 12, 14, 16, 18, 20, 18],  # PA28
        'ZK-MGA': [22, 24, 26, 24, 22, 20, 18, 20, 22, 24, 26, 24],  # C172
        'ZK-GHX': [26, 28, 30, 28, 26, 22, 20, 22, 26, 28, 30, 28],  # C152
        'ZK-NEP': [14, 16, 18, 16, 14, 12, 10, 12, 14, 16, 18, 16],  # DA40
        'ZK-BFR': [16, 18, 20, 18, 16, 14, 12, 14, 16, 18, 20, 18],  # PA28
        'ZK-JEZ': [10, 12, 14, 12, 10,  8,  8,  8, 10, 12, 14, 12],  # Grumman
    }
    # Build 12 month list starting from fy_start_month
    _fy_months = []
    _m, _y = _cfg.fy_start_month, _fy_year
    for _ in range(12):
        _fy_months.append(_m)
        _m += 1
        if _m > 12:
            _m = 1
    count = 0
    for reg, monthly_hrs in _BUDGETS.items():
        ac = Aircraft.objects.filter(club=club, registration=reg).first()
        if not ac:
            continue
        for i, month in enumerate(_fy_months):
            FlyingBudget.objects.update_or_create(
                club=club, aircraft=ac, fy_year=_fy_year, month=month,
                defaults={'budgeted_hours': monthly_hrs[i]},
            )
            count += 1
    log(f'Flying budget: {count} entries for FY{_fy_year}')

print('\n' + '─'*55)
if DRY:
    print('  Dry run complete — no data written.')
else:
    from core.models import Booking, FlightCompletion, Account, MaintenanceLogEntry
    print(f'  Members:          {ClubMember.objects.filter(club=club).count()}')
    print(f'  Aircraft (online):{Aircraft.objects.filter(club=club, status="online").count()}')
    print(f'  Charge rates:     {ChargeRate.objects.filter(club=club).count()}')
    print(f'  Past flights:     {Booking.objects.filter(club=club, status="completed").count()} completed')
    print(f'  Departed now:     {Booking.objects.filter(club=club, status="departed").count()}')
    print(f'  Upcoming:         {Booking.objects.filter(club=club, status="confirmed").count()} confirmed')
    print(f'  Maint log entries:{MaintenanceLogEntry.objects.filter(aircraft__club=club).count()}')
    print(f'  Accounts:         {Account.objects.filter(club_member__club=club).count()}')
    print(f'  Contact types:    {ContactType.objects.filter(club=club).count()}')
    print(f'  Contacts:         {Contact.objects.filter(club=club).count()} ({Contact.objects.filter(club=club, is_organisation=True).count()} orgs)')
print()
