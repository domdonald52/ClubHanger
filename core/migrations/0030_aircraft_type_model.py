from django.db import migrations, models
import django.db.models.deletion


def create_aircraft_types(apps, schema_editor):
    """Seed AircraftType from distinct Aircraft.aircraft_type char values, then set FK."""
    Aircraft = apps.get_model('core', 'Aircraft')
    AircraftType = apps.get_model('core', 'AircraftType')

    # Group by club to ensure types are club-scoped
    from collections import defaultdict
    by_club = defaultdict(set)
    for ac in Aircraft.objects.select_related('club').all():
        val = (ac.aircraft_type_legacy or '').strip()
        if val:
            by_club[ac.club_id].add(val)

    # Create AircraftType records per club
    type_map = {}  # (club_id, name) -> AircraftType pk
    for club_id, names in by_club.items():
        for name in sorted(names):
            at = AircraftType.objects.create(club_id=club_id, name=name)
            type_map[(club_id, name)] = at.pk

    # Set aircraft_type_fk on each aircraft
    for ac in Aircraft.objects.all():
        val = (ac.aircraft_type_legacy or '').strip()
        if val:
            pk = type_map.get((ac.club_id, val))
            if pk:
                ac.aircraft_type_fk_id = pk
                ac.save(update_fields=['aircraft_type_fk_id'])

    # Best-effort: match MemberCredential type ratings by name substring
    MemberCredential = apps.get_model('core', 'MemberCredential')
    for cred in MemberCredential.objects.filter(credential_type='type').select_related('club_member'):
        if not cred.name:
            continue
        club_id = cred.club_member.club_id
        name_lower = cred.name.lower()
        for (cid, atype_name), pk in type_map.items():
            if cid == club_id and atype_name.lower() in name_lower:
                cred.aircraft_type_id = pk
                cred.save(update_fields=['aircraft_type_id'])
                break


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0029_amount_paid'),
    ]

    operations = [
        # 1. Create AircraftType model
        migrations.CreateModel(
            name='AircraftType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=60)),
                ('icao_designator', models.CharField(blank=True, max_length=10,
                                                      help_text='ICAO type designator, e.g. C172, PA38')),
                ('club', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                            related_name='aircraft_types', to='core.club')),
            ],
            options={'ordering': ['name']},
        ),
        migrations.AlterUniqueTogether(
            name='aircrafttype',
            unique_together={('club', 'name')},
        ),

        # 2. Rename old CharField so it coexists during data migration
        migrations.RenameField(
            model_name='aircraft',
            old_name='aircraft_type',
            new_name='aircraft_type_legacy',
        ),

        # 3. Add new nullable FK (temporary name to avoid clash)
        migrations.AddField(
            model_name='aircraft',
            name='aircraft_type_fk',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='aircraft',
                to='core.aircrafttype',
            ),
        ),

        # 4. Add FK to MemberCredential (nullable, permanent name)
        migrations.AddField(
            model_name='membercredential',
            name='aircraft_type',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='type_ratings',
                to='core.aircrafttype',
            ),
        ),

        # 5. Data migration
        migrations.RunPython(create_aircraft_types, migrations.RunPython.noop),

        # 6. Remove the legacy CharField
        migrations.RemoveField(
            model_name='aircraft',
            name='aircraft_type_legacy',
        ),

        # 7. Rename FK to the canonical field name
        migrations.RenameField(
            model_name='aircraft',
            old_name='aircraft_type_fk',
            new_name='aircraft_type',
        ),
    ]
