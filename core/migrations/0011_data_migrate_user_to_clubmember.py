from datetime import date
from django.db import migrations


def migrate_forward(apps, schema_editor):
    ClubMember = apps.get_model('core', 'ClubMember')

    for m in ClubMember.objects.select_related('user').all():
        changed = False

        # ── Personal details from User ────────────────────────────────────────
        if m.user:
            u = m.user
            m.caa_number    = u.caa_number    or ''
            m.phone_mobile  = u.phone_mobile  or ''
            m.phone_home    = u.phone_home    or ''
            m.phone_work    = u.phone_work    or ''
            m.address_line1 = u.address_line1 or ''
            m.address_line2 = u.address_line2 or ''
            m.suburb        = u.suburb        or ''
            m.postcode      = u.postcode      or ''
            m.date_of_birth = u.date_of_birth
            changed = True

        # ── subscription_expires ← expiry_date ───────────────────────────────
        if m.expiry_date is not None:
            m.subscription_expires = m.expiry_date
            changed = True

        # ── standing ← member_status + is_active ─────────────────────────────
        old_status = m.member_status   # 'member' | 'non_member'
        old_active = m.is_active       # True | False

        if old_status == 'non_member':
            m.standing = 'non_member'
        elif old_status == 'member' and old_active:
            # Check subscription — if already expired, mark as lapsed
            if m.subscription_expires and m.subscription_expires < date.today():
                m.standing = 'lapsed'
            else:
                m.standing = 'active'
        else:
            # member but is_active=False → resigned or lapsed; use lapsed as default
            m.standing = 'lapsed'
        changed = True

        if changed:
            m.save()


def migrate_backward(apps, schema_editor):
    # Reverse: restore member_status + is_active from standing
    ClubMember = apps.get_model('core', 'ClubMember')
    for m in ClubMember.objects.all():
        if m.standing == 'non_member':
            m.member_status = 'non_member'
            m.is_active = True
        elif m.standing == 'active':
            m.member_status = 'member'
            m.is_active = True
        else:
            m.member_status = 'member'
            m.is_active = False
        m.expiry_date = m.subscription_expires
        m.save()


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0010_clubmember_standing_personal_account_credit'),
    ]

    operations = [
        migrations.RunPython(migrate_forward, migrate_backward),
    ]
