from django.db import migrations, models


def seed_renewal_defaults(apps, schema_editor):
    """
    Roles named 'instructor' or 'staff' default renewal_required=False
    (contracted staff aren't in the membership renewal cycle).
    All others keep renewal_required=True (the field default).
    """
    Role = apps.get_model('core', 'Role')
    for role in Role.objects.all():
        if role.name.lower() in ('instructor', 'staff', 'external instructor', 'contracted instructor'):
            role.renewal_required = False
        role.save(update_fields=['renewal_required'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0038_role_permissions'),
    ]

    operations = [
        migrations.AddField(
            model_name='role',
            name='renewal_required',
            field=models.BooleanField(default=True,
                help_text='Members with this role are included in the annual renewal cycle'),
        ),
        migrations.AddField(
            model_name='role',
            name='annual_renewal_fee',
            field=models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                help_text='Annual fee for this role. Leave blank if not applicable.'),
        ),
        migrations.RunPython(seed_renewal_defaults, migrations.RunPython.noop),
    ]
