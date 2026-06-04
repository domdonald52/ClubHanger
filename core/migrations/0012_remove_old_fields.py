from django.db import migrations


class Migration(migrations.Migration):
    """
    Remove personal fields from User (now on ClubMember) and remove
    the superseded fields member_status, is_active, expiry_date from ClubMember.
    """

    dependencies = [
        ('core', '0011_data_migrate_user_to_clubmember'),
    ]

    operations = [
        # Remove personal fields from User
        migrations.RemoveField(model_name='user', name='caa_number'),
        migrations.RemoveField(model_name='user', name='phone_mobile'),
        migrations.RemoveField(model_name='user', name='phone_home'),
        migrations.RemoveField(model_name='user', name='phone_work'),
        migrations.RemoveField(model_name='user', name='address_line1'),
        migrations.RemoveField(model_name='user', name='address_line2'),
        migrations.RemoveField(model_name='user', name='suburb'),
        migrations.RemoveField(model_name='user', name='postcode'),
        migrations.RemoveField(model_name='user', name='date_of_birth'),
        # Remove superseded ClubMember fields
        migrations.RemoveField(model_name='clubmember', name='member_status'),
        migrations.RemoveField(model_name='clubmember', name='is_active'),
        migrations.RemoveField(model_name='clubmember', name='expiry_date'),
    ]
