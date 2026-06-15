from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0046_seed_system_roles'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Add cash/split to FlightCompletion.payment_method choices (cosmetic — no DB change needed for CharField)
        migrations.AlterField(
            model_name='flightcompletion',
            name='payment_method',
            field=models.CharField(
                blank=True, default='', max_length=20,
                choices=[
                    ('credit', 'Account credit'),
                    ('eftpos', 'EFTPOS'),
                    ('invoice', 'Invoice (bank transfer)'),
                    ('split', 'Split'),
                    ('cash', 'Cash'),
                ],
            ),
        ),
        migrations.CreateModel(
            name='FlightPayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=8)),
                ('method', models.CharField(
                    max_length=20,
                    choices=[
                        ('credit', 'Account credit'),
                        ('eftpos', 'EFTPOS'),
                        ('cash', 'Cash'),
                        ('invoice', 'Invoice (bank transfer)'),
                    ],
                )),
                ('paid_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('completion', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='payments',
                    to='core.flightcompletion',
                )),
                ('member', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='flight_payments',
                    to='core.clubmember',
                )),
                ('recorded_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),
    ]
