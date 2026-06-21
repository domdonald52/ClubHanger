"""
Management command: seed (or refresh) the NZ-CAA credential catalogue.

Idempotent — safe to re-run.  Only creates missing rows; updates name/category/
expires/display_order on existing ones so the catalogue can be kept current by
re-running after code changes.  Never deletes rows (existing member credentials
may reference them).

Usage:
    python manage.py seed_nz_caa
"""

from django.core.management.base import BaseCommand
from core.models import CredentialType

NZ_CAA = 'NZ-CAA'

CATALOGUE = [
    # (code, name, category, expires, default_validity_months, requires_aircraft_type, display_order)

    # Licences
    ('ppl',        'Private Pilot Licence (PPL)',        'licence',            False, None, False, 10),
    ('cpl',        'Commercial Pilot Licence (CPL)',     'licence',            False, None, False, 20),
    ('atpl',       'Air Transport Pilot Licence (ATPL)', 'licence',            False, None, False, 30),
    ('rpl',        'Recreational Pilot Licence (RPL)',   'licence',            False, None, False, 40),

    # Instructor ratings
    ('instr_a',    'Instructor Rating Cat A',            'instructor_rating',  False, None, False, 50),
    ('instr_b',    'Instructor Rating Cat B',            'instructor_rating',  False, None, False, 60),
    ('instr_c',    'Instructor Rating Cat C',            'instructor_rating',  False, None, False, 70),
    ('instr_d',    'Instructor Rating Cat D',            'instructor_rating',  False, None, False, 80),
    ('instr_e',    'Instructor Rating Cat E',            'instructor_rating',  False, None, False, 90),
    ('examiner',   'Flight Examiner Rating',             'instructor_rating',  True,  None, False, 100),

    # Medicals
    ('medical_c1', 'Medical Class 1',                    'medical',            True,  None, False, 110),
    ('medical_c2', 'Medical Class 2',                    'medical',            True,  None, False, 120),
    ('medical_c3', 'Medical Class 3',                    'medical',            True,  None, False, 130),
    ('dlr9',       'Medical DLR9',                       'medical',            True,  None, False, 140),

    # Operational ratings
    ('night',      'Night Rating',                       'operational_rating', False, None, False, 150),
    ('ir',         'IFR Rating',                         'operational_rating', True,  None, False, 160),
    ('aerobatic',  'Aerobatic Rating',                   'operational_rating', False, None, False, 170),
    ('ag1',        'Agricultural Rating Grade 1',        'operational_rating', False, None, False, 180),
    ('ag2',        'Agricultural Rating Grade 2',        'operational_rating', False, None, False, 190),
    ('nvis',       'NVIS Rating',                        'operational_rating', False, None, False, 200),
    ('fr',         'Flight Review (BFR)',                'operational_rating', True,  24,   False, 210),

    # Endorsements (aircraft class / feature)
    ('tailwheel',  'Tailwheel Endorsement',              'endorsement',        False, None, False, 220),
    ('me',         'Multi-engine Endorsement',           'endorsement',        False, None, False, 230),
    ('ru',         'Retractable Undercarriage',          'endorsement',        False, None, False, 240),
    ('csu',        'Constant Speed Propeller (CSU)',     'endorsement',        False, None, False, 250),
    ('turbo',      'Turbocharged / Supercharged',        'endorsement',        False, None, False, 260),
    ('seaplane',   'Seaplane / Float Endorsement',       'endorsement',        False, None, False, 270),
    ('xc',         'Cross Country Endorsement',          'endorsement',        False, None, False, 280),

    # Type ratings
    ('type',       'Type Rating',                        'type_rating',        False, None, True,  290),

    # Status
    ('student',    'Student Pilot',                      'status',             False, None, False, 300),
    ('other',      'Other',                              'status',             False, None, False, 310),
]


class Command(BaseCommand):
    help = 'Seed (or refresh) the NZ-CAA credential type catalogue. Idempotent.'

    def handle(self, *args, **options):
        created = updated = 0
        for code, name, category, expires, validity, req_ac, order in CATALOGUE:
            ct, is_new = CredentialType.objects.get_or_create(
                region=NZ_CAA,
                code=code,
                defaults=dict(
                    name=name,
                    category=category,
                    expires=expires,
                    default_validity_months=validity,
                    requires_aircraft_type=req_ac,
                    display_order=order,
                ),
            )
            if is_new:
                created += 1
            else:
                changed = False
                for attr, val in [
                    ('name', name), ('category', category), ('expires', expires),
                    ('default_validity_months', validity),
                    ('requires_aircraft_type', req_ac), ('display_order', order),
                ]:
                    if getattr(ct, attr) != val:
                        setattr(ct, attr, val)
                        changed = True
                if changed:
                    ct.save()
                    updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'NZ-CAA catalogue: {created} created, {updated} updated, '
                f'{len(CATALOGUE) - created - updated} unchanged.'
            )
        )
