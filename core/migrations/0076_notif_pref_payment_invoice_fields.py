from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0075_add_invoice_subscription_expiry_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationpreference',
            name='payment_reminder',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='notificationpreference',
            name='invoice_sent',
            field=models.BooleanField(default=True),
        ),
    ]
