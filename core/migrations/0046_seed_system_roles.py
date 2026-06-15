from django.db import migrations


SYSTEM_ROLES = [
    ('member', 'Member', {
        'bookings_access': 'view_own',
        'can_access_manage': False,
        'can_access_settings': False,
        'can_access_reports': False,
        'is_superadmin': False,
        'renewal_required': True,
    }),
    ('instructor', 'Instructor', {
        'bookings_access': 'manage_all',
        'can_access_manage': True,
        'can_access_settings': False,
        'can_access_reports': False,
        'is_superadmin': False,
        'renewal_required': True,
    }),
    ('admin', 'Admin', {
        'bookings_access': 'manage_all',
        'can_access_manage': True,
        'can_access_settings': True,
        'can_access_reports': True,
        'is_superadmin': True,
        'renewal_required': False,
    }),
]


def seed_system_roles(apps, schema_editor):
    Club = apps.get_model('core', 'Club')
    Role = apps.get_model('core', 'Role')

    for club in Club.objects.all():
        for role_type, default_name, perms in SYSTEM_ROLES:
            # Try to promote an existing matching role rather than creating a duplicate
            existing = None
            if role_type == 'instructor':
                existing = Role.objects.filter(
                    club=club, bookings_access='manage_all',
                    can_access_manage=True, system_role_type=''
                ).first()
            elif role_type == 'admin':
                existing = Role.objects.filter(
                    club=club, is_superadmin=True, system_role_type=''
                ).first()
            elif role_type == 'member':
                existing = Role.objects.filter(
                    club=club, bookings_access='view_own', system_role_type=''
                ).exclude(can_access_manage=True).first()

            if existing:
                existing.system_role_type = role_type
                existing.save(update_fields=['system_role_type'])
            else:
                name = default_name
                if Role.objects.filter(club=club, name=name).exists():
                    name = f'{default_name} (System)'
                Role.objects.create(club=club, name=name, system_role_type=role_type, **perms)


def remove_system_roles(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    Role.objects.exclude(system_role_type='').update(system_role_type='')


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0045_role_system_type'),
    ]
    operations = [
        migrations.RunPython(seed_system_roles, remove_system_roles),
    ]
