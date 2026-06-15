from django.db import migrations


def convert_tacho_less5(apps, schema_editor):
    Aircraft = apps.get_model('core', 'Aircraft')
    Aircraft.objects.filter(total_time_method='tacho_less_5').update(total_time_method='tacho')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0063_font_choice'),
    ]

    operations = [
        migrations.RunPython(convert_tacho_less5, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='aircraft',
            name='total_time_method',
            field=__import__('django.db.models', fromlist=['CharField']).CharField(
                max_length=20,
                choices=[
                    ('hobbs', 'Hobbs meter'),
                    ('tacho', 'Tachometer'),
                    ('airswitch', 'Air switch'),
                ],
                default='hobbs',
            ),
        ),
    ]
