from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0111_add_surcharge_rate_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='clubconfig',
            name='density',
            field=models.CharField(
                max_length=12,
                default='compact',
                choices=[('comfortable', 'Comfortable'), ('compact', 'Compact'), ('ultra', 'Ultra-compact')],
            ),
        ),
        migrations.RunSQL(
            "UPDATE core_clubconfig SET density = CASE WHEN compact_mode THEN 'compact' ELSE 'comfortable' END",
            reverse_sql="UPDATE core_clubconfig SET compact_mode = (density != 'comfortable')",
        ),
        migrations.RemoveField(
            model_name='clubconfig',
            name='compact_mode',
        ),
    ]
