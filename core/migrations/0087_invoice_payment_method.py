from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0086_invoice_per_payee'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='payment_method',
            field=models.CharField(
                blank=True,
                choices=[
                    ('cash',           'Cash'),
                    ('eftpos',         'EFTPOS'),
                    ('credit_card',    'Credit card'),
                    ('bank_transfer',  'Bank transfer'),
                    ('account_credit', 'Account credit'),
                ],
                max_length=20,
            ),
        ),
    ]
