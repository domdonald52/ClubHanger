# Generated manually 2026-06-19

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0085_add_app_banner'),
    ]

    operations = [
        migrations.AlterField(
            model_name='invoice',
            name='flight_completion',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='invoices',
                to='core.flightcompletion',
            ),
        ),
        migrations.AlterUniqueTogether(
            name='invoice',
            unique_together={('club', 'invoice_number'), ('flight_completion', 'member')},
        ),
    ]
