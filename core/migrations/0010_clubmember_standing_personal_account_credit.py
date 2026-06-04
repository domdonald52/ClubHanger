from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add new fields to ClubMember (standing, personal details, subscription dates)
    and make Account.credit_limit nullable.
    Old fields (member_status, is_active, expiry_date on ClubMember; personal fields
    on User) are retained here for the data migration in 0011.
    """

    dependencies = [
        ('core', '0009_blockouttype_target'),
    ]

    operations = [
        # ClubMember: membership standing (replaces member_status + is_active)
        migrations.AddField(
            model_name='clubmember',
            name='standing',
            field=models.CharField(
                choices=[
                    ('pending',    'Pending Approval'),
                    ('active',     'Active'),
                    ('suspended',  'Suspended'),
                    ('lapsed',     'Lapsed'),
                    ('resigned',   'Resigned'),
                    ('non_member', 'Non-member'),
                ],
                default='active',
                max_length=20,
            ),
        ),
        # ClubMember: subscription dates (subscription_expires replaces expiry_date)
        migrations.AddField(
            model_name='clubmember',
            name='subscription_expires',
            field=models.DateField(blank=True, null=True,
                                   help_text='Current subscription valid until'),
        ),
        migrations.AddField(
            model_name='clubmember',
            name='last_renewed',
            field=models.DateField(blank=True, null=True,
                                   help_text='Date of most recent subscription payment'),
        ),
        # ClubMember: personal details (moved from User)
        migrations.AddField(model_name='clubmember', name='caa_number',
                            field=models.CharField(blank=True, max_length=10)),
        migrations.AddField(model_name='clubmember', name='phone_mobile',
                            field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name='clubmember', name='phone_home',
                            field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name='clubmember', name='phone_work',
                            field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name='clubmember', name='address_line1',
                            field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name='clubmember', name='address_line2',
                            field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name='clubmember', name='suburb',
                            field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name='clubmember', name='postcode',
                            field=models.CharField(blank=True, max_length=10)),
        migrations.AddField(model_name='clubmember', name='date_of_birth',
                            field=models.DateField(blank=True, null=True)),
        # Account: credit_limit nullable (null = exempt, typically instructors)
        migrations.AlterField(
            model_name='account',
            name='credit_limit',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True,
                help_text='Max negative balance allowed. Null = exempt (e.g. instructors).',
            ),
        ),
    ]
