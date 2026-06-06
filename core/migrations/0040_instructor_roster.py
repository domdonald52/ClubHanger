from django.db import migrations, models


def seed_roster_from_role(apps, schema_editor):
    """Members who currently have an instructor-type role go onto the roster."""
    ClubMember = apps.get_model('core', 'ClubMember')
    for m in ClubMember.objects.select_related('role').all():
        if m.role and m.role.name.lower() in ('instructor', 'cfi', 'chief flying instructor'):
            m.is_on_instructor_roster = True
            m.save(update_fields=['is_on_instructor_roster'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0039_role_renewal'),
    ]

    operations = [
        migrations.AddField(
            model_name='clubmember',
            name='is_on_instructor_roster',
            field=models.BooleanField(
                default=False,
                help_text='Appears as an instructor row in the calendar and is selectable on bookings.',
            ),
        ),
        migrations.RunPython(seed_roster_from_role, migrations.RunPython.noop),
    ]
