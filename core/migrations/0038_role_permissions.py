from django.db import migrations, models


def seed_existing_role_permissions(apps, schema_editor):
    """
    Set sensible defaults for roles that already exist based on their name.
    New roles created after this migration default to minimum (view_own, no manage/settings/reports).
    """
    Role = apps.get_model('core', 'Role')
    for role in Role.objects.all():
        n = role.name.lower()
        if n in ('admin', 'administrator'):
            role.bookings_access   = 'manage_all'
            role.can_access_manage  = True
            role.can_access_settings = True
            role.can_access_reports  = True
        elif n == 'instructor':
            role.bookings_access   = 'manage_all'
            role.can_access_manage  = True
            role.can_access_settings = False
            role.can_access_reports  = True
        elif n in ('member', 'pilot', 'student'):
            role.bookings_access   = 'manage_own'
            role.can_access_manage  = False
            role.can_access_settings = False
            role.can_access_reports  = False
        # else: leave at field defaults (view_own, all False)
        role.save()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0037_sar_time'),
    ]

    operations = [
        migrations.AddField(
            model_name='role',
            name='bookings_access',
            field=models.CharField(
                choices=[
                    ('none', 'No booking access'),
                    ('view_own', 'View own bookings only'),
                    ('manage_own', 'Manage own bookings'),
                    ('manage_all', 'Manage all bookings'),
                ],
                default='view_own',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='role',
            name='can_access_manage',
            field=models.BooleanField(default=False, help_text='Access to Manage menu (members, aircraft, bookings, charges)'),
        ),
        migrations.AddField(
            model_name='role',
            name='can_access_settings',
            field=models.BooleanField(default=False, help_text='Access to Settings and club configuration'),
        ),
        migrations.AddField(
            model_name='role',
            name='can_access_reports',
            field=models.BooleanField(default=False, help_text='Access to Reports and Analytics'),
        ),
        migrations.AddField(
            model_name='role',
            name='is_superadmin',
            field=models.BooleanField(default=False, help_text='Grants full access to everything in the club'),
        ),
        migrations.RunPython(seed_existing_role_permissions, migrations.RunPython.noop),
    ]
