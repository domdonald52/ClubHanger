"""
Migration: replace CredentialType TextChoices enum with a proper DB model,
and move MemberCredential.club_member (FK → ClubMember) to .member (FK → User)
so credentials are shared across clubs.

Steps
-----
1. Create CredentialType table
2. Add nullable member + ct_new columns to MemberCredential
3. RunPython — seed NZ-CAA catalogue, migrate existing credential rows
4. Drop old club_member + credential_type columns
5. Rename ct_new → credential_type; make member non-nullable
"""

from django.db import migrations, models
import django.db.models.deletion


# ---------------------------------------------------------------------------
# NZ-CAA catalogue — same data as seed_nz_caa command (kept in sync)
# ---------------------------------------------------------------------------

NZ_CAA = 'NZ-CAA'

NZ_CAA_CATALOGUE = [
    # (code, name, category, expires, default_validity_months, requires_aircraft_type, display_order)
    ('ppl',        'Private Pilot Licence (PPL)',       'licence',            False, None, False, 10),
    ('cpl',        'Commercial Pilot Licence (CPL)',    'licence',            False, None, False, 20),
    ('atpl',       'Air Transport Pilot Licence (ATPL)','licence',            False, None, False, 30),
    ('rpl',        'Recreational Pilot Licence (RPL)',  'licence',            False, None, False, 40),
    ('instr_a',    'Instructor Rating Cat A',           'instructor_rating',  False, None, False, 50),
    ('instr_b',    'Instructor Rating Cat B',           'instructor_rating',  False, None, False, 60),
    ('instr_c',    'Instructor Rating Cat C',           'instructor_rating',  False, None, False, 70),
    ('instr_d',    'Instructor Rating Cat D',           'instructor_rating',  False, None, False, 80),
    ('instr_e',    'Instructor Rating Cat E',           'instructor_rating',  False, None, False, 90),
    ('examiner',   'Flight Examiner Rating',            'instructor_rating',  True,  None, False, 100),
    ('medical_c1', 'Medical Class 1',                   'medical',            True,  None, False, 110),
    ('medical_c2', 'Medical Class 2',                   'medical',            True,  None, False, 120),
    ('medical_c3', 'Medical Class 3',                   'medical',            True,  None, False, 130),
    ('dlr9',       'Medical DLR9',                      'medical',            True,  None, False, 140),
    ('night',      'Night Rating',                      'operational_rating', False, None, False, 150),
    ('ir',         'IFR Rating',                        'operational_rating', True,  None, False, 160),
    ('aerobatic',  'Aerobatic Rating',                  'operational_rating', False, None, False, 170),
    ('ag1',        'Agricultural Rating Grade 1',       'operational_rating', False, None, False, 180),
    ('ag2',        'Agricultural Rating Grade 2',       'operational_rating', False, None, False, 190),
    ('nvis',       'NVIS Rating',                       'operational_rating', False, None, False, 200),
    ('fr',         'Flight Review (BFR)',               'operational_rating', True,  24,   False, 210),
    ('tailwheel',  'Tailwheel Endorsement',             'endorsement',        False, None, False, 220),
    ('me',         'Multi-engine Endorsement',          'endorsement',        False, None, False, 230),
    ('ru',         'Retractable Undercarriage',         'endorsement',        False, None, False, 240),
    ('csu',        'Constant Speed Propeller (CSU)',    'endorsement',        False, None, False, 250),
    ('turbo',      'Turbocharged / Supercharged',       'endorsement',        False, None, False, 260),
    ('seaplane',   'Seaplane / Float Endorsement',      'endorsement',        False, None, False, 270),
    ('xc',         'Cross Country Endorsement',         'endorsement',        False, None, False, 280),
    ('type',       'Type Rating',                       'type_rating',        False, None, True,  290),
    ('student',    'Student Pilot',                     'status',             False, None, False, 300),
    ('other',      'Other',                             'status',             False, None, False, 310),
]

# Old CharField values → new catalogue codes
# 'night_vfr' renamed to 'night'; 'instrument' was 'ir' (unchanged)
OLD_TO_NEW_CODE = {
    'ppl': 'ppl', 'cpl': 'cpl', 'atpl': 'atpl',
    'xc': 'xc', 'night_vfr': 'night', 'ir': 'ir', 'me': 'me',
    'type': 'type', 'tailwheel': 'tailwheel', 'aerobatic': 'aerobatic',
    'seaplane': 'seaplane',
    'instr_c': 'instr_c', 'instr_b': 'instr_b', 'instr_a': 'instr_a',
    'examiner': 'examiner',
    'medical_c1': 'medical_c1', 'medical_c2': 'medical_c2',
    'medical_c3': 'medical_c3', 'dlr9': 'dlr9',
    'fr': 'fr', 'other': 'other',
}


def seed_catalogue_and_migrate(apps, schema_editor):
    CredentialType = apps.get_model('core', 'CredentialType')
    MemberCredential = apps.get_model('core', 'MemberCredential')

    # 1. Seed NZ-CAA catalogue
    catalogue = {}
    for code, name, category, expires, validity, req_ac, order in NZ_CAA_CATALOGUE:
        ct, _ = CredentialType.objects.get_or_create(
            region=NZ_CAA, code=code,
            defaults=dict(name=name, category=category, expires=expires,
                          default_validity_months=validity,
                          requires_aircraft_type=req_ac, display_order=order),
        )
        catalogue[code] = ct

    # 2. Migrate existing MemberCredential rows
    for mc in MemberCredential.objects.select_related('club_member__user').order_by('id'):
        mc.member = mc.club_member.user
        old_code = mc.old_credential_type or 'other'
        new_code = OLD_TO_NEW_CODE.get(old_code, 'other')
        mc.ct_new = catalogue.get(new_code, catalogue['other'])
        mc.save(update_fields=['member', 'ct_new'])


def reverse_migrate(apps, schema_editor):
    # Reversing to the enum is lossy (new codes like 'night' had no old value)
    # but we can do a best-effort reverse for the member FK
    MemberCredential = apps.get_model('core', 'MemberCredential')
    ClubMember = apps.get_model('core', 'ClubMember')
    NEW_TO_OLD = {v: k for k, v in OLD_TO_NEW_CODE.items()}
    for mc in MemberCredential.objects.select_related('member').order_by('id'):
        cm = ClubMember.objects.filter(user=mc.member).first()
        if cm:
            mc.club_member = cm
        if mc.ct_new_id:
            ct = mc.ct_new
            mc.old_credential_type = NEW_TO_OLD.get(ct.code, 'other')
        mc.save(update_fields=['club_member', 'old_credential_type'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0091_gantt_redesign_theme'),
    ]

    operations = [
        # ── 1. Create CredentialType table ───────────────────────────────────
        migrations.CreateModel(
            name='CredentialType',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('region', models.CharField(db_index=True, default='NZ-CAA', max_length=20)),
                ('code', models.CharField(max_length=20)),
                ('name', models.CharField(max_length=100)),
                ('category', models.CharField(max_length=20, choices=[
                    ('licence', 'Licence'),
                    ('instructor_rating', 'Instructor Rating'),
                    ('medical', 'Medical'),
                    ('endorsement', 'Endorsement'),
                    ('operational_rating', 'Operational Rating'),
                    ('type_rating', 'Type Rating'),
                    ('status', 'Status'),
                ])),
                ('expires', models.BooleanField(default=False)),
                ('default_validity_months', models.IntegerField(blank=True, null=True)),
                ('requires_aircraft_type', models.BooleanField(default=False)),
                ('display_order', models.IntegerField(default=0)),
            ],
            options={
                'verbose_name': 'Credential type',
                'verbose_name_plural': 'Credential types',
                'ordering': ['display_order', 'name'],
                'unique_together': {('region', 'code')},
            },
        ),

        # ── 2a. Rename old credential_type CharField so we can reuse the name later
        migrations.RenameField(
            model_name='membercredential',
            old_name='credential_type',
            new_name='old_credential_type',
        ),

        # ── 2b. Add nullable member FK + ct_new FK
        migrations.AddField(
            model_name='membercredential',
            name='member',
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='credentials',
                to='core.user',
            ),
        ),
        migrations.AddField(
            model_name='membercredential',
            name='ct_new',
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='+',
                to='core.credentialtype',
            ),
        ),

        # ── 3. Data migration
        migrations.RunPython(seed_catalogue_and_migrate, reverse_migrate),

        # ── 4. Drop old columns
        migrations.RemoveField(model_name='membercredential', name='club_member'),
        migrations.RemoveField(model_name='membercredential', name='old_credential_type'),

        # ── 5. Rename ct_new → credential_type; make member non-nullable
        migrations.RenameField(
            model_name='membercredential',
            old_name='ct_new',
            new_name='credential_type',
        ),
        migrations.AlterField(
            model_name='membercredential',
            name='member',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='credentials',
                to='core.user',
            ),
        ),
        migrations.AlterField(
            model_name='membercredential',
            name='credential_type',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='member_credentials',
                to='core.credentialtype',
            ),
        ),
    ]
