"""
One-time import: WAC verified financial members FY2026-27.

Source: WAC_FY2627_Verified_Members (81 members, expiry 31/03/2027)
Contact details merged from Paper Aviator full member export.
2 members (Kawharu-Wells, McNaughton) have no contact details in PA —
they will be created with placeholder emails and flagged.

All 81 are imported as plain Members (single category). PA types
(Flying - Pilot, Flying - Student, Staff, Corporate, Three Flight Package)
are not carried over — the PPL distinction is handled by CH credentials,
and the constitution does not recognise corporate or package categories.
Instructor roles for staff members (Hillson, Kemp) are assigned manually.

Usage:
    python manage.py import_wac_fy2627 --club <slug> --dry-run
    python manage.py import_wac_fy2627 --club <slug>

Railway:
    /opt/venv/bin/python manage.py import_wac_fy2627 --club wellington-aero-club --dry-run
    /opt/venv/bin/python manage.py import_wac_fy2627 --club wellington-aero-club
"""
import re
import secrets
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Account, Club, ClubMember, MembershipCategory, Role

SUB_EXPIRES  = date(2027, 3, 31)
LAST_RENEWED = date(2026, 6, 30)

# (first, last, email, mobile, category)
# category is 'Member' or 'Gateway Student'
# email='' → placeholder created and flagged for follow-up
# School/parent emails are imported as-is; flagged in output for admin awareness.
GS = 'Gateway Student'
M  = 'Member'

MEMBERS = [
    ('Nick',               'Calavrias (JNR)', 'nzcalavrias@gmail.com',             '027 589 9522',    M),
    ('Andrew',             'Abernethy',       'proabltd@gmail.com',                '021 251 5640',    M),
    ('William',            'Groombridge',     'williamgroombridgepilot@gmail.com', '02041580831',     M),
    ('Eyal',               'Aharoni',         'eyal@primeproperty.co.nz',          '021 455 033',     M),
    ('Terence',            'Bao',             '23419@wc.school.nz',                '021 911 618',     GS),
    ('Joseph',             'Batey',           'bateyjoseph@yahoo.co.nz',           '0279105087',      M),
    ('Daniel',             'Bennett',         'dan_benuts@xtra.co.nz',             '0272230067',      M),
    ('Kate',               'Bolton',          'katelbolton@outlook.com',           '0277755512',      M),
    ('Glenn',              'Bouzaid',         'gbouzaid@gmail.com',                '027 564 6886',    M),
    ('James',              'Bowler',          '23217@wc.school.nz',                '021 403 208',     GS),
    ('Esther',             'Carey-Smith',     'careyes@whs.school.nz',             '0211367279',      GS),
    ('Ross',               'Chalmers',        'cts.ltd@outlook.com',               '021 210 0293',    M),
    ('Aarti',              'Chauhan',         '23489ac@hvhs.school.nz',            '022 074 9822',    GS),
    ('Jason',              'Clements',        'jcle41@gmail.com',                  '021323435',       M),
    ('David',              'Costello',        'davidacostello@gmail.com',          '022 6393 823',    M),
    ('Olivia',             'Cunningham',      'oliviagracecunningham123@gmail.com','0212666130',      M),
    ('Jamie',              'Curtis',          'jamie.curtis@fpc.nz',               '0272542793',      M),
    ('Dominic',            'Donald',          'dom.donald@gmail.com',              '0278395314',      M),
    ('Feo',                'Feng',            '25435@wc.school.nz',                '021 106 3996',    GS),
    ('Cody',               'Fletcher',        '26427@wc.school.nz',                '027 332 8446',    GS),
    ('Chris',              'Forbes',          'chrisf@ijw.co.nz',                  '027 424 5104',    M),
    ('Peter',              'Galuszka',        'p.galuszka@xtra.co.nz',             '027 443 9846',    M),
    ('Malcolm',            'Goddard',         'malcolmgoddard@xtra.co.nz',         '021 257 4444',    M),
    ('Oliver',             'Griffin',         '23280@wc.school.nz',                '021 026 48213',   GS),
    ('Alma',               'Hardhani',        '23222@wc.school.nz',                '022 620 6900',    GS),
    ('Greg',               'Hayes',           'gdhys@aol.com',                     '001 425 785 1188',M),
    ('Briana',             'Hill',            'b@5678.nz',                         '02108602345',     M),
    ('James',              'Hillson',         'jphillson@gmail.com',               '027 413 4863',    M),  # assign instructor role manually after import
    ('Blair',              'Hinton',          'blair.hinton@wormald.co.nz',        '0275248058',      M),
    ('Simon',              'Holdsworth',      'simon@evander.co.nz',               '027 269 9202',    M),
    ('Boone',              'Houghton',        'ddhoughton2@gmail.com',             '022 496 2470',    M),  # parent email — flag
    ('Frank',              'Hsu',             'frank.hsu01@gmail.com',             '0212127327',      M),
    ('Luke',               'Hubscher',        'keeweeclare@gmail.com',             '',                GS), # parent email — flag
    ('Gareth',             'Humphries',       'gareth1@erozen.org',                '021 026 09242',   M),
    ('Nick',               'Hyland',          'hynick4@gmail.com',                 '0278726653',      M),
    ('Hauraki',            'Kawharu-Wells',   '',                                  '',                M),  # no PA record → placeholder
    ('Sean',               'Kemp',            'sean@tasjc.com',                    '022 633 6022',    M),  # already in CH — will update subscription
    ('Shivi',              'Kulugammana',     'shivi.kulu98@gmail.com',            '0211820024',      M),
    ('Andrew',             'Langton',         'andrewlangton@hotmail.com',         '0273948565',      M),
    ('Jocelyn',            'Lelez',           'jocelynlelez@hotmail.com',          '0272060437',      M),
    ('Steven',             'Letts',           'steveletts@gmail.com',              '0274933379',      M),
    ('Tim',                'Lewis',           'timothy.m.lewis@gmail.com',         '021 863 950',     M),
    ('Jiapeng (Victoria)', 'Li',              'li.jp189@gmail.com',                '021313698',       M),
    ('Erich',              'Livengood',       'elivengood@gmail.com',              '021 221 6524',    M),
    ('Tony',               'Lloyd',           'tony@aglconsulting.co.nz',          '021 379 165',     M),
    ('Lance',              'Lones',           'lance@l2vr.com',                    '021 026 26581',   M),
    ('Rodney',             'Maas',            'rodney@flying.geek.nz',             '021 182 9222',    M),
    ('Cliff',              'Marchant',        'cliff.marchant@gmail.com',          '021 476 845',     M),
    ('Ratu',               'Mataira',         '',                                  '',                M),  # confirmed member, not in PA export → placeholder
    ('Kevin',              'Mason',           'mason.kevinc@gmail.com',            '0204715135',      M),
    ('Andrew',             'Matheson',        'andrew.matheson.nz@gmail.com',      '021 442 297',     M),
    ('Gene',               'McNaughton',      '',                                  '',                M),  # no PA record → placeholder
    ('Drew',               'Meiklejohn',      'drewm@hotmail.co.nz',               '027 843 1301',    M),
    ('Peter',              'Mitchell',        'peter.99mitchell@gmail.com',        '027 862 0454',    M),
    ('Lafi',               'Mokded',          'mookdedlafi@hotmail.com',           '021469561',       M),
    ('Neil',               'Moore',           'horseandpaula@xtra.co.nz',          '0274380890',      M),
    ('Jacob',              'Reynolds Muir',   'jreynoldsmuir@gmail.com',           '0220651957',      M),
    ('Kasey',              'Mulot',           'kaseymulot@gmail.com',              '0272527396',      M),
    ('Josh',               'Narayanan',       'joshnarayanan@icloud.com',          '02108577978',     M),
    ('Grace',              'Penlain',         'gracepenlain@protonmail.com',       '022 692 6104',    M),
    ('Jake',               'Percival',        'jakeper@live.co.uk',                '0274312276',      M),
    ('Chithil',            'Perera',          'pererch26362@whs.school.nz',        '0274889321',      GS),
    ('Jack',               'Radmall',         'kerryradmall@gmail.com',            '',                GS), # parent email — flag
    ('Samuel',             'Rix',             '26434@wc.school.nz',                '027 201 9747',    GS),
    ('Carl',               'Robertson',       'carl.j.robertson@icloud.com',       '02108016867',     M),
    ('Scott',              'Shrimpton',       'scott.matai.shrimpton@gmail.com',   '02041669937',     M),
    ('Tanveer',            'Singh',           '25535@wc.school.nz',                '021 242 2897',    GS),
    ('Fabian',             'Sivorarath',      'fabian.sivorarath@gmail.com',       '0212381213',      M),
    ('Declan',             'Slater',          '23158@wc.school.nz',                '027 248 2885',    GS),
    ('James',              'Small',           '23416@wc.school.nz',                '027 455 5431',    GS),
    ('Alastair',           'Smith',           'alismithnz@gmail.com',              '021528037',       M),
    ('Tristam',            'Sparks',          'tristam@restlesseye.com',           '027 372 4597',    M),
    ('Matthew',            'Stevens',         'matt@zamm7.co.nz',                  '027 531 0382',    M),
    ('Quinn',              'Stratton',        '23182@wc.school.nz',                '021 138 0737',    GS),
    ('Kees',               'Taniela',         '23388@wc.school.nz',                '021 125 1614',    GS),
    ('Marisa',             'Tucker',          'marisa_tucker@hotmail.com',         '0278397415',      M),
    ('Thomas',             'Tyson',           '23394@wc.school.nz',                '021 292 4451',    GS),
    ('Anusha',             'Verma',           '23537av@hvhs.school.nz',            '022 088 6334',    GS),
    ('Mathew',             'Webster',         'matmw001@gmail.com',                '02102569122',     M),
    ('Matt',               'Whittaker',       'matt@whittakers.co.nz',             '021 412151',      M),
    ('Darren',             'Wiltshire',       'dazzzandleanne@yahoo.co.nz',        '021 028 47428',   M),
    ('Charlotte',          'Woolley',         'cw23127@stmw.school.nz',            '',                GS), # parent mobile
]

SCHOOL_EMAIL_DOMAINS = {'wc.school.nz', 'whs.school.nz', 'hvhs.school.nz', 'stmw.school.nz'}
PARENT_EMAILS = {
    'ddhoughton2@gmail.com',   # Boone Houghton — dad's email
    'keeweeclare@gmail.com',   # Luke Hubscher — mum's email
    'kerryradmall@gmail.com',  # Jack Radmall — mum's email
}


def _is_school_email(email):
    domain = email.split('@')[-1].lower() if '@' in email else ''
    return domain in SCHOOL_EMAIL_DOMAINS


class Command(BaseCommand):
    help = 'Import WAC FY2026-27 verified financial members (one-time, idempotent)'

    def add_arguments(self, parser):
        parser.add_argument('--club', required=True, help='Club slug, e.g. wellington-aero-club')
        parser.add_argument('--dry-run', action='store_true',
                            help='Validate and preview without saving')

    def handle(self, *args, **options):
        try:
            club = Club.objects.get(slug=options['club'])
        except Club.DoesNotExist:
            raise CommandError(f"No club with slug '{options['club']}'")

        member_role = (
            Role.objects.filter(club=club, system_role_type=Role.SYSTEM_MEMBER).first()
            or Role.objects.filter(club=club, name__iexact='member').first()
        )
        if member_role is None:
            raise CommandError(
                f"Club has no Member role — run: manage.py setup_defaults --club {options['club']}")

        User = get_user_model()
        stats = dict(users_created=0, members_created=0, members_updated=0,
                     accounts_created=0, placeholders=0)
        flag_notes = []

        cat_cache = {}

        def get_category(name):
            if name not in cat_cache:
                cat_cache[name], _ = MembershipCategory.objects.get_or_create(
                    club=club, name=name, defaults={'is_member': True})
            return cat_cache[name]

        with transaction.atomic():
            for first, last, email, mobile, cat_name in MEMBERS:
                email = email.strip().lower()

                if not email:
                    email = self._placeholder_email(User, first, last)
                    flag_notes.append(f'  PLACEHOLDER  {first} {last} → {email}')
                    stats['placeholders'] += 1
                elif _is_school_email(email):
                    flag_notes.append(f'  SCHOOL EMAIL {first} {last} <{email}>')
                elif email in PARENT_EMAILS:
                    flag_notes.append(f'  PARENT EMAIL {first} {last} <{email}>')

                if not options['dry_run']:
                    user = User.objects.filter(email__iexact=email).first()
                    if user is None:
                        user = User.objects.create(
                            username=self._unique_username(User, email),
                            email=email,
                            first_name=first,
                            last_name=last,
                            password=make_password(secrets.token_hex(20)),
                        )
                        stats['users_created'] += 1
                    else:
                        changed = []
                        if first and user.first_name != first:
                            user.first_name = first; changed.append('first_name')
                        if last and user.last_name != last:
                            user.last_name = last; changed.append('last_name')
                        if changed:
                            user.save(update_fields=changed)

                    cm, made = ClubMember.objects.update_or_create(
                        club=club, user=user,
                        defaults=dict(
                            role=member_role,
                            membership_category=get_category(cat_name),
                            phone_mobile=mobile,
                            standing=ClubMember.STANDING_ACTIVE,
                            subscription_expires=SUB_EXPIRES,
                            last_renewed=LAST_RENEWED,
                        ),
                    )
                    stats['members_created' if made else 'members_updated'] += 1

                    _, acct_made = Account.objects.get_or_create(club_member=cm)
                    if acct_made:
                        stats['accounts_created'] += 1
                else:
                    self.stdout.write(f'  DRY RUN  {first} {last} <{email}> [{cat_name}]')

            if options['dry_run']:
                transaction.set_rollback(True)

        label = 'DRY RUN — nothing saved' if options['dry_run'] else 'Import complete'
        self.stdout.write(self.style.MIGRATE_HEADING(f'\n{label}'))
        self.stdout.write(
            f'  Total in list:     {len(MEMBERS)}\n'
            f'  Users created:     {stats["users_created"]}\n'
            f'  Members created:   {stats["members_created"]}\n'
            f'  Members updated:   {stats["members_updated"]}\n'
            f'  Accounts created:  {stats["accounts_created"]}\n'
            f'  Placeholders:      {stats["placeholders"]}'
        )
        if flag_notes:
            self.stdout.write(self.style.WARNING(
                f'\nAttention — {len(flag_notes)} items need email follow-up:'))
            for note in flag_notes:
                self.stdout.write(note)
        self.stdout.write(self.style.SUCCESS('\nDone.'))

    def _placeholder_email(self, User, first, last):
        base = re.sub(r'[^a-z0-9.]', '',
                      f"{first}.{last}".lower()).strip('.') or 'member'
        candidate = f"{base}@migrated.invalid"
        n = 2
        while User.objects.filter(email__iexact=candidate).exists():
            candidate = f"{base}{n}@migrated.invalid"; n += 1
        return candidate

    def _unique_username(self, User, email):
        base = re.sub(r'[^A-Za-z0-9.@+_-]', '', email.split('@')[0])[:30] or 'member'
        username, n = base, 1
        while User.objects.filter(username=username).exists():
            suffix = str(n)
            username = base[:30 - len(suffix)] + suffix
            n += 1
        return username
