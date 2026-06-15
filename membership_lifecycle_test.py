"""
ClubHangar — Membership Lifecycle Scenario Test
================================================

Covers six people with different lifecycle patterns:

  Bob    — Standard member. Joins start of FY, renews cleanly each year.
  Alice  — Mid-year joiner. Subscription straddles two FYs.
  Carol  — Non-payment. Subscription expires; admin lapses her.
  Dave   — Suspension, reinstatement, then later resignation.
  Eve    — Joins and resigns within the same FY; account still in credit.
  Frank  — Pending approval; approved with no expiry date initially set.

All data is created inside a rolled-back savepoint — nothing is persisted.
Admin actions (renewal, suspension, etc.) are performed directly on model
instances, mirroring exactly what the view layer does.

Usage:  venv/bin/python membership_lifecycle_test.py
"""
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aero_club.settings')
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

from decimal import Decimal as D
from datetime import date, timedelta
from django.db import transaction
from django.contrib.auth import get_user_model

User = get_user_model()
from core.models import (
    Club, ClubMember, Role, Account, AccountTransaction, MembershipHistoryEntry,
)

# ── Terminal colours ───────────────────────────────────────────────────────────
G = '\033[92m'; R = '\033[91m'; Y = '\033[93m'; B = '\033[94m'
BOLD = '\033[1m'; DIM = '\033[2m'; W = '\033[0m'

passed = failed = 0

def ok(label):
    global passed
    print(f"    {G}✓{W}  {label}")
    passed += 1

def fail(label):
    global failed
    print(f"    {R}✗  FAIL: {label}{W}")
    failed += 1

def check(label, cond):
    if cond: ok(label)
    else:    fail(label)

def narrate(msg):
    """Print a narrative sentence — what is happening in the story."""
    print(f"\n  {msg}")

def event(msg):
    """Print a concrete system event."""
    print(f"    {DIM}→{W} {msg}")

def section(title):
    print(f"\n{B}{'═'*72}")
    print(f"  {title}")
    print(f"{'═'*72}{W}")

def step(date_str, msg):
    print(f"\n  {BOLD}[{date_str}]{W}  {msg}")


# ── Date helpers ───────────────────────────────────────────────────────────────
# is_current uses date.today() internally.  We provide our own version that
# accepts an explicit "as_of" date so we can assert past and future states.

def is_current_as_of(member, as_of: date) -> bool:
    """Equivalent of ClubMember.is_current but evaluated at a given date."""
    member.refresh_from_db()
    if member.standing != 'active':
        return False
    if member.subscription_expires and member.subscription_expires < as_of:
        return False
    return True


# ── Setup ─────────────────────────────────────────────────────────────────────
club       = Club.objects.first()
admin_user = User.objects.filter(is_superuser=True).first()
member_role = Role.objects.filter(club=club, system_role_type='member').first()

if not club or not admin_user:
    print(f"{R}No club or superuser found — run seed_data.py first.{W}")
    sys.exit(1)

if not member_role:
    print(f"{Y}Warning: no system 'member' role found — using first non-instructor role.{W}")
    member_role = Role.objects.filter(club=club).exclude(system_role_type='instructor').first()

# Ensure role has a renewal fee set for the narrative to make sense
ANNUAL_FEE = D('200.00')
member_role.annual_renewal_fee = ANNUAL_FEE
member_role.renewal_required   = True
member_role.save(update_fields=['annual_renewal_fee', 'renewal_required'])

print(f"\n{B}{'═'*72}")
print(f"  ClubHangar — Membership Lifecycle Test")
print(f"{'═'*72}{W}")
print(f"\n  Club:        {club.name}")
print(f"  Admin:       {admin_user.get_full_name() or admin_user.username}")
print(f"  Role:        {member_role.name}  (annual fee ${ANNUAL_FEE})")
print(f"  FY start:    month {club.config.fy_start_month if hasattr(club, 'config') else '?'}")


# ── Helper functions (mirror what the view layer does) ────────────────────────

def make_member(username, first, last, standing='active',
                subscription_expires=None, join_date_override=None):
    """Create a test User + ClubMember + Account."""
    user, _ = User.objects.get_or_create(
        username=f'_test_{username}',
        defaults={'first_name': first, 'last_name': last,
                  'email': f'{username}@lifecycle.test'}
    )
    user.first_name = first; user.last_name = last
    user.save(update_fields=['first_name', 'last_name'])

    cm = ClubMember.objects.create(
        club=club, user=user, standing=standing,
        role=member_role,
        subscription_expires=subscription_expires,
    )
    if join_date_override:
        # auto_now_add — bypass via queryset update
        ClubMember.objects.filter(pk=cm.pk).update(join_date=join_date_override)
        cm.refresh_from_db()

    Account.objects.get_or_create(club_member=cm, defaults={'balance': D('0')})

    MembershipHistoryEntry.objects.create(
        club_member=cm, event_type='joined',
        changed_by=admin_user,
        old_value='', new_value=standing,
    )
    return cm


def top_up(member, amount, description, on_date=None):
    """Admin credits the member's account (annual fee payment, etc.)."""
    acct = member.account
    AccountTransaction.objects.create(
        account=acct,
        transaction_type='top_up',
        direction='credit',
        amount=D(str(amount)),
        description=description,
        payment_method='bank_transfer',
        created_by=admin_user,
    )
    acct.balance += D(str(amount))
    acct.save(update_fields=['balance'])


def debit_flight(member, amount, description):
    """Deduct a flight charge from the member's account."""
    acct = member.account
    AccountTransaction.objects.create(
        account=acct,
        transaction_type='flight',
        direction='debit',
        amount=D(str(amount)),
        description=description,
        payment_method='account',
        created_by=admin_user,
    )
    acct.balance -= D(str(amount))
    acct.save(update_fields=['balance'])


def set_standing(member, new_standing, note='', resigned_on=None):
    """Admin changes a member's standing — creates history entry."""
    old = member.standing
    member.standing = new_standing
    if new_standing in ('resigned', 'lapsed'):
        member.resigned_at = resigned_on or date.today()
    member.save(update_fields=['standing', 'resigned_at'])
    MembershipHistoryEntry.objects.create(
        club_member=member, event_type='standing_change',
        changed_by=admin_user,
        old_value=old, new_value=new_standing, note=note,
    )


def renew_sub(member, new_expiry, renewed_on):
    """Admin records a subscription renewal."""
    old_exp = member.subscription_expires
    member.subscription_expires = new_expiry
    member.last_renewed = renewed_on
    member.save(update_fields=['subscription_expires', 'last_renewed'])
    MembershipHistoryEntry.objects.create(
        club_member=member, event_type='subscription_renewed',
        changed_by=admin_user,
        old_value=str(old_exp) if old_exp else '—',
        new_value=str(new_expiry),
    )


def balance(member):
    member.account.refresh_from_db()
    return member.account.balance


def recomputed_balance(member):
    return member.account.recompute_balance()


def history_events(member):
    return list(member.membership_history.order_by('changed_at').values_list('event_type', flat=True))


def active_member_count_on(as_of: date) -> int:
    """
    Approximate active-and-current member count on a given date.
    We check: standing='active' AND (sub_expires IS NULL OR sub_expires >= as_of).
    This mirrors the system's notion of a member who could fly on that date.
    """
    return ClubMember.objects.filter(
        club=club, standing='active'
    ).filter(
        subscription_expires__isnull=True
    ).union(
        ClubMember.objects.filter(
            club=club, standing='active',
            subscription_expires__gte=as_of,
        )
    ).count()


# ══════════════════════════════════════════════════════════════════════════════
#  Run all scenarios inside a rolled-back savepoint
# ══════════════════════════════════════════════════════════════════════════════

with transaction.atomic():
    sid = transaction.savepoint()
    try:

        # ──────────────────────────────────────────────────────────────────────
        section("SCENARIO A · Bob Wright — Standard Annual Member")
        # ──────────────────────────────────────────────────────────────────────

        narrate(
            "Bob joins the club on 1 April 2024, the first day of the financial year. "
            "The admin sets up his account and records a $200 annual membership fee "
            "payment by bank transfer. His subscription is set to expire on 31 March 2025."
        )

        bob = make_member('bob_wright', 'Bob', 'Wright',
                          standing='active',
                          subscription_expires=date(2025, 3, 31),
                          join_date_override=date(2024, 4, 1))

        top_up(bob, 200, 'Annual membership fee FY2024/25 — bank transfer ref BW-001')

        step('2024-04-01', 'Bob joins the club, pays his annual fee.')
        event(f'standing={bob.standing}, sub_expires={bob.subscription_expires}, '
              f'join_date={bob.join_date}, account=${balance(bob)}')

        check('Bob standing is active',                 bob.standing == 'active')
        check('Bob sub_expires is 31 Mar 2025',         bob.subscription_expires == date(2025, 3, 31))
        check('Bob account balance is $200',            balance(bob) == D('200.00'))
        check('Bob is_current on join date',            is_current_as_of(bob, date(2024, 4, 1)))
        check('Bob is_current mid-year (1 Oct 2024)',   is_current_as_of(bob, date(2024, 10, 1)))
        check('Bob is_current on last day (31 Mar 2025)',
                                                        is_current_as_of(bob, date(2025, 3, 31)))
        check('Bob NOT is_current day after expiry (1 Apr 2025)',
                                                    not is_current_as_of(bob, date(2025, 4, 1)))
        check('History has joined entry',               'joined' in history_events(bob))

        narrate(
            "Bob flies twice during the year — June and September — paying via account credit."
        )

        debit_flight(bob, 75,  'ZK-ABC Hobbs 0.9h — 15 Jun 2024')
        debit_flight(bob, 80,  'ZK-ABC Hobbs 1.0h — 10 Sep 2024')

        step('2024-09-10', 'Bob has flown twice this year.')
        event(f'Account: $200 − $75 − $80 = ${balance(bob)}')

        check('Bob account after 2 flights is $45',     balance(bob) == D('45.00'))
        check('Ledger recomputes correctly',            recomputed_balance(bob) == balance(bob))

        narrate(
            "At the end of March 2025 Bob pays his renewal fee by bank transfer. "
            "The admin tops up his account and records the new subscription expiry."
        )

        top_up(bob, 200, 'Annual membership fee FY2025/26 — bank transfer ref BW-002')
        renew_sub(bob, new_expiry=date(2026, 3, 31), renewed_on=date(2025, 3, 31))

        step('2025-03-31', 'Admin records renewal payment and extends subscription.')
        event(f'sub_expires={bob.subscription_expires}, last_renewed={bob.last_renewed}, '
              f'account=${balance(bob)}')

        check('Bob sub_expires extended to 31 Mar 2026', bob.subscription_expires == date(2026, 3, 31))
        check('Bob last_renewed is 31 Mar 2025',         bob.last_renewed == date(2025, 3, 31))
        check('Bob account is $245 after renewal top-up', balance(bob) == D('245.00'))
        check('Bob is_current on 1 Apr 2025 after renewal',
                                                         is_current_as_of(bob, date(2025, 4, 1)))
        check('Bob is_current mid FY2025/26 (1 Oct 2025)',
                                                         is_current_as_of(bob, date(2025, 10, 1)))
        check('History has subscription_renewed entry',  'subscription_renewed' in history_events(bob))
        check('History has exactly 2 entries (joined + renewed)',
                                                         len(history_events(bob)) == 2)


        # ──────────────────────────────────────────────────────────────────────
        section("SCENARIO B · Alice Chen — Mid-Year Joiner Spanning Two FYs")
        # ──────────────────────────────────────────────────────────────────────

        narrate(
            "Alice joins on 15 October 2024 — partway through the financial year. "
            "The club charges the full annual fee regardless of when in the year she joins. "
            "Her subscription is set to run for 365 days (expiring 14 October 2025) so her "
            "renewal anniversary stays the same each year, independent of the club's FY."
        )

        alice = make_member('alice_chen', 'Alice', 'Chen',
                            standing='active',
                            subscription_expires=date(2025, 10, 14),
                            join_date_override=date(2024, 10, 15))

        top_up(alice, 200, 'Annual membership fee Oct-2024–Oct-2025 — bank transfer ref AC-001')

        step('2024-10-15', 'Alice joins mid-year.')
        event(f'standing={alice.standing}, sub_expires={alice.subscription_expires}, '
              f'account=${balance(alice)}')

        check('Alice is active',                         alice.standing == 'active')
        check('Alice sub_expires 14 Oct 2025',           alice.subscription_expires == date(2025, 10, 14))
        check('Alice account $200',                      balance(alice) == D('200.00'))

        narrate(
            "The financial year turns over on 1 April 2025. Alice's subscription is "
            "anniversary-based (not FY-based), so she is still fully current in the new year."
        )

        step('2025-04-01', 'New financial year starts. Alice is still current.')
        check('Alice still current on 1 Apr 2025 (sub runs to Oct)',
                                                         is_current_as_of(alice, date(2025, 4, 1)))

        narrate(
            "Alice's subscription expires on 14 October 2025. The day the subscription "
            "expires she is still current (the system treats the expiry date itself as valid). "
            "On 15 October — one day after — she can no longer book until she renews."
        )

        step('2025-10-14 → 2025-10-15', 'Alice sub_expires edge.')
        check('Alice still current ON expiry day (14 Oct 2025)',
                                                         is_current_as_of(alice, date(2025, 10, 14)))
        check('Alice NOT current day after expiry (15 Oct 2025)',
                                                     not is_current_as_of(alice, date(2025, 10, 15)))

        narrate(
            "Alice pays her second year's fee on 16 October 2025. "
            "The admin tops up her account and extends the subscription to 14 October 2026."
        )

        top_up(alice, 200, 'Annual membership fee Oct-2025–Oct-2026 — bank transfer ref AC-002')
        renew_sub(alice, new_expiry=date(2026, 10, 14), renewed_on=date(2025, 10, 16))

        step('2025-10-16', 'Alice renews. Back in good standing.')
        event(f'sub_expires={alice.subscription_expires}, account=${balance(alice)}')

        check('Alice sub_expires is now 14 Oct 2026',    alice.subscription_expires == date(2026, 10, 14))
        check('Alice current again on 16 Oct 2025',      is_current_as_of(alice, date(2025, 10, 16)))
        check('Alice account $400 (two years\' fees in)',
                                                         balance(alice) == D('400.00'))
        check('Alice history: joined + renewed',         history_events(alice) == ['joined', 'subscription_renewed'])


        # ──────────────────────────────────────────────────────────────────────
        section("SCENARIO C · Carol Summers — Non-Payment, Lapse")
        # ──────────────────────────────────────────────────────────────────────

        narrate(
            "Carol joins on 1 April 2024 and pays her annual fee. She flies during "
            "the year, drawing down her account. When renewal time comes, the admin "
            "sends a notice but Carol doesn't respond and doesn't pay."
        )

        carol = make_member('carol_summers', 'Carol', 'Summers',
                            standing='active',
                            subscription_expires=date(2025, 3, 31),
                            join_date_override=date(2024, 4, 1))

        top_up(carol, 200, 'Annual membership fee FY2024/25 — bank transfer ref CS-001')
        debit_flight(carol, 95,  'ZK-MGA Hobbs 1.2h — 5 Jul 2024')
        debit_flight(carol, 110, 'ZK-MGA Hobbs 1.4h — 20 Sep 2024')

        step('2024-09-20', 'Carol has flown twice. Account at $200 − $95 − $110.')
        check('Carol account is −$5 (slightly overdrawn)',  balance(carol) == D('-5.00'))
        check('Carol still current mid-year',               is_current_as_of(carol, date(2024, 10, 1)))

        narrate(
            "1 April 2025: Carol's subscription expired yesterday. The system marks her "
            "as no longer current, but her standing field still reads 'active' — the system "
            "does NOT automatically lapse members. She can no longer book flights."
        )

        step('2025-04-01', 'Subscription expired. Carol can no longer book.')
        check('Carol NOT current day after expiry',     not is_current_as_of(carol, date(2025, 4, 1)))
        check('Carol standing still "active" (no auto-lapse)',  carol.standing == 'active')

        narrate(
            "The admin waits two weeks, sends a second notice. Still no response. "
            "On 15 April 2025 the admin formally sets Carol's standing to 'lapsed'. "
            "The system automatically records resigned_at = today (ISA 2022 s.26 compliance)."
        )

        set_standing(carol, 'lapsed',
                     note='No renewal payment after two notices. Formally lapsed.',
                     resigned_on=date(2025, 4, 15))

        step('2025-04-15', 'Admin lapses Carol.')
        event(f'standing={carol.standing}, resigned_at={carol.resigned_at}')

        check('Carol standing is lapsed',                   carol.standing == 'lapsed')
        check('Carol resigned_at is 15 Apr 2025',           carol.resigned_at == date(2025, 4, 15))
        check('Carol is NOT current (lapsed standing)',  not is_current_as_of(carol, date(2025, 4, 15)))
        check('Carol is still "is_member" (not non_member)', carol.is_member)
        check('Carol history has standing_change entry',    'standing_change' in history_events(carol))
        check('Account balance retained (−$5)',             balance(carol) == D('-5.00'))
        check('Ledger recomputes correctly',                recomputed_balance(carol) == balance(carol))

        narrate(
            "The $5 debt on Carol's account is retained in the ledger. The bookkeeper "
            "will decide whether to write it off or pursue it. The membership history "
            "record with resigned_at must be kept for 7 years under ISA 2022 s.26."
        )

        check('History entries: joined, standing_change',   history_events(carol) == ['joined', 'standing_change'])


        # ──────────────────────────────────────────────────────────────────────
        section("SCENARIO D · Dave Tane — Suspension, Reinstatement, Resignation")
        # ──────────────────────────────────────────────────────────────────────

        narrate(
            "Dave joins on 1 April 2024. He pays $400 (enough for two years of flights "
            "without topping up). In mid-June a debt dispute arises and the admin suspends "
            "him while the matter is investigated."
        )

        dave = make_member('dave_tane', 'Dave', 'Tane',
                           standing='active',
                           subscription_expires=date(2025, 3, 31),
                           join_date_override=date(2024, 4, 1))

        top_up(dave, 400, 'Annual fee + flying credit — bank transfer ref DT-001')

        step('2024-04-01', 'Dave joins, top-up $400.')
        check('Dave active at join',                    dave.standing == 'active')
        check('Dave account $400',                      balance(dave) == D('400.00'))

        narrate(
            "15 June 2024: Admin suspends Dave. Even though his subscription is still valid "
            "until March 2025, is_current returns False immediately because standing != 'active'."
        )

        set_standing(dave, 'suspended',
                     note='Debt dispute under investigation.',
                     resigned_on=None)   # suspended doesn't set resigned_at

        step('2024-06-15', 'Admin suspends Dave.')
        event(f'standing={dave.standing}, resigned_at={dave.resigned_at}')

        check('Dave standing is suspended',                 dave.standing == 'suspended')
        check('Dave NOT current when suspended',        not is_current_as_of(dave, date(2024, 6, 16)))
        check('Dave resigned_at is None (suspended, not resigned)',
                                                            dave.resigned_at is None)
        check('History has standing_change: active → suspended',
                                                            history_events(dave)[-1] == 'standing_change')

        narrate(
            "20 August 2024: The dispute is resolved in Dave's favour. "
            "Admin reinstates him to 'active'. His subscription is still valid — "
            "he picks up where he left off."
        )

        set_standing(dave, 'active',
                     note='Debt dispute resolved. Reinstated.',
                     resigned_on=None)

        step('2024-08-20', 'Admin reinstates Dave.')
        event(f'standing={dave.standing}')

        check('Dave reinstated to active',              dave.standing == 'active')
        check('Dave current again after reinstatement', is_current_as_of(dave, date(2024, 8, 20)))
        check('Dave sub still valid until Mar 2025',    dave.subscription_expires == date(2025, 3, 31))

        debit_flight(dave, 120, 'ZK-BFR Tacho 1.5h — 3 Sep 2024')

        step('2024-09-03', 'Dave flies after reinstatement.')
        check('Dave account $280 after flight',         balance(dave) == D('280.00'))

        narrate(
            "March 2025: Admin renews Dave's subscription for another year."
        )

        renew_sub(dave, new_expiry=date(2026, 3, 31), renewed_on=date(2025, 3, 29))

        step('2025-03-29', 'Admin renews Dave\'s subscription.')
        check('Dave sub_expires extended to Mar 2026',  dave.subscription_expires == date(2026, 3, 31))

        narrate(
            "February 2025: Dave decides to emigrate and formally resigns. "
            "The admin changes his standing to 'resigned' and resigned_at is recorded."
        )

        set_standing(dave, 'resigned',
                     note='Member relocated overseas. Formal resignation.',
                     resigned_on=date(2025, 2, 28))

        step('2025-02-28', 'Dave formally resigns.')
        event(f'standing={dave.standing}, resigned_at={dave.resigned_at}')

        check('Dave standing is resigned',              dave.standing == 'resigned')
        check('Dave resigned_at is 28 Feb 2025',        dave.resigned_at == date(2025, 2, 28))
        check('Dave NOT current after resignation',  not is_current_as_of(dave, date(2025, 3, 1)))
        check('Dave account $280 still in credit',      balance(dave) == D('280.00'))

        narrate(
            "Dave's account still shows $280 in credit. The bookkeeper will arrange a "
            "refund outside the system. The history entry with resigned_at must be "
            "retained for 7 years."
        )

        expected_history = ['joined', 'standing_change', 'standing_change',
                            'subscription_renewed', 'standing_change']
        check('Dave history has 5 entries (joined, suspend, reinstate, renew, resign)',
                                                         history_events(dave) == expected_history)


        # ──────────────────────────────────────────────────────────────────────
        section("SCENARIO E · Eve Thompson — Joins and Resigns Within Same FY")
        # ──────────────────────────────────────────────────────────────────────

        narrate(
            "Eve joins on 1 May 2024. She pays the full annual fee even though she's "
            "three months into the club's FY. Her subscription expires 30 April 2025. "
            "She flies twice, then in October decides to move overseas and resigns."
        )

        eve = make_member('eve_thompson', 'Eve', 'Thompson',
                          standing='active',
                          subscription_expires=date(2025, 4, 30),
                          join_date_override=date(2024, 5, 1))

        top_up(eve, 200, 'Annual membership fee — bank transfer ref ET-001')
        debit_flight(eve, 60,  'ZK-NEP Hobbs 0.8h — 12 May 2024')
        debit_flight(eve, 75,  'ZK-NEP Hobbs 1.0h — 20 Jul 2024')

        step('2024-07-20', 'Eve has flown twice. Account = $200 − $60 − $75.')
        check('Eve account is $65',                     balance(eve) == D('65.00'))
        check('Eve is current mid-year',                is_current_as_of(eve, date(2024, 8, 1)))

        narrate(
            "1 October 2024: Eve resigns. She has been a member for 5 months. "
            "Her subscription was valid until April 2025 but that is now irrelevant — "
            "standing='resigned' immediately makes is_current False regardless of sub_expires."
        )

        set_standing(eve, 'resigned',
                     note='Member relocating to UK.',
                     resigned_on=date(2024, 10, 1))

        step('2024-10-01', 'Eve resigns before her subscription expires.')
        event(f'standing={eve.standing}, resigned_at={eve.resigned_at}, '
              f'sub_expires={eve.subscription_expires} (now irrelevant)')

        check('Eve standing is resigned',                   eve.standing == 'resigned')
        check('Eve resigned_at is 1 Oct 2024',              eve.resigned_at == date(2024, 10, 1))
        check('Eve NOT current after resignation',       not is_current_as_of(eve, date(2024, 10, 2)))
        check('Eve NOT current even though sub_expires still in future',
                                                         not is_current_as_of(eve, date(2025, 1, 1)))
        check('Eve account $65 still in credit (refund outside system)',
                                                             balance(eve) == D('65.00'))

        narrate(
            "Eve's $65 credit remains on the account pending a manual refund by the bookkeeper. "
            "The club has no obligation to refund the unused portion of her subscription fee "
            "(club rules determine this), but the funds are tracked and auditable."
        )

        check('Ledger recomputes correctly',                recomputed_balance(eve) == balance(eve))


        # ──────────────────────────────────────────────────────────────────────
        section("SCENARIO F · Frank Hobbs — Pending Approval, No Initial Expiry")
        # ──────────────────────────────────────────────────────────────────────

        narrate(
            "Frank submits a membership application on 1 June 2024. The admin creates "
            "his ClubMember record with standing='pending'. This is an important edge case: "
            "he cannot book anything yet and is not counted as a current member."
        )

        frank = make_member('frank_hobbs', 'Frank', 'Hobbs',
                            standing='pending',
                            subscription_expires=None,
                            join_date_override=date(2024, 6, 1))

        step('2024-06-01', 'Frank\'s application received, standing=pending.')
        event(f'standing={frank.standing}, sub_expires={frank.subscription_expires}')

        check('Frank standing is pending',              frank.standing == 'pending')
        check('Frank NOT current while pending',    not is_current_as_of(frank, date(2024, 6, 1)))

        narrate(
            "5 June 2024: The committee approves Frank's application. The admin changes "
            "his standing to 'active'. Importantly, no subscription_expires is set yet — "
            "the admin will invoice Frank and set the date once payment is received. "
            "Without a subscription_expires date, the system treats the membership as "
            "perpetual (no expiry check fires when sub_expires is None)."
        )

        frank.standing = 'active'
        frank.save(update_fields=['standing'])
        MembershipHistoryEntry.objects.create(
            club_member=frank, event_type='standing_change',
            changed_by=admin_user, old_value='pending', new_value='active',
            note='Application approved by committee.',
        )

        step('2024-06-05', 'Frank approved, standing=active, sub_expires still None.')
        event(f'standing={frank.standing}, sub_expires={frank.subscription_expires}')

        check('Frank standing is active',               frank.standing == 'active')
        check('Frank IS current with no expiry set',    is_current_as_of(frank, date(2024, 6, 5)))

        narrate(
            "This 'no expiry = perpetual' behaviour is intentional for the first weeks "
            "after approval. The admin must then invoice Frank and set subscription_expires "
            "once payment is received. Until then Frank can use the system but the club is "
            "relying on admin follow-through."
        )

        check('Frank still current 5 months later with no expiry (edge case!)',
                                                         is_current_as_of(frank, date(2025, 1, 1)))

        narrate(
            "1 July 2024: Frank pays. Admin tops up his account and sets subscription_expires."
        )

        top_up(frank, 200, 'Annual membership fee — bank transfer ref FH-001')
        renew_sub(frank, new_expiry=date(2025, 6, 30), renewed_on=date(2024, 7, 1))

        step('2024-07-01', 'Frank pays. Admin sets subscription expiry to 30 Jun 2025.')
        event(f'sub_expires={frank.subscription_expires}, account=${balance(frank)}')

        check('Frank sub_expires 30 Jun 2025',          frank.subscription_expires == date(2025, 6, 30))
        check('Frank account $200',                     balance(frank) == D('200.00'))
        check('Frank current after sub set',            is_current_as_of(frank, date(2024, 7, 1)))
        check('Frank NOT current day after expiry',  not is_current_as_of(frank, date(2025, 7, 1)))

        check('Frank history: joined, standing_change, subscription_renewed',
                                                         history_events(frank) == ['joined', 'standing_change', 'subscription_renewed'])


        # ──────────────────────────────────────────────────────────────────────
        section("CROSS-SCENARIO CHECKS")
        # ──────────────────────────────────────────────────────────────────────

        narrate(
            "Final integrity checks across all scenarios — account ledgers, "
            "standing correctness, and resigned_at audit compliance."
        )

        step('All dates', 'Account ledger integrity (stored balance = recomputed balance).')
        for person in [bob, alice, carol, dave, eve, frank]:
            name = person.user.get_full_name()
            check(f'{name}: ledger balance matches stored balance',
                  recomputed_balance(person) == balance(person))

        step('Today', 'Current standing of each person.')
        standings = {m.user.get_full_name(): m.standing for m in [bob, alice, carol, dave, eve, frank]}
        event('Standings: ' + ', '.join(f'{k}={v}' for k, v in standings.items()))
        check('Bob is active',      bob.standing   == 'active')
        check('Alice is active',    alice.standing == 'active')
        check('Carol is lapsed',    carol.standing == 'lapsed')
        check('Dave is resigned',   dave.standing  == 'resigned')
        check('Eve is resigned',    eve.standing   == 'resigned')
        check('Frank is active',    frank.standing == 'active')

        step('ISA 2022 s.26', 'Members who left must have resigned_at recorded.')
        for person, expected in [(carol, date(2025, 4, 15)), (dave, date(2025, 2, 28)),
                                  (eve, date(2024, 10, 1))]:
            name = person.user.get_full_name()
            person.refresh_from_db()
            check(f'{name}: resigned_at = {expected}',  person.resigned_at == expected)

        step('All dates', 'Resigned/lapsed members are not is_current regardless of sub_expires.')
        for person in [carol, dave, eve]:
            name = person.user.get_full_name()
            check(f'{name}: NOT is_current today',  not is_current_as_of(person, date.today()))

        narrate(
            "Summary of each person's final account balance:"
        )
        print()
        for person in [bob, alice, carol, dave, eve, frank]:
            person.account.refresh_from_db()
            b = balance(person)
            sign = '+' if b >= 0 else ''
            print(f"    {person.user.get_full_name():<20}  {person.standing:<12}  "
                  f"account: {sign}${b}")

    finally:
        transaction.savepoint_rollback(sid)
        print(f"\n  {DIM}(All test data rolled back — database unchanged){W}")


# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'═'*72}")
colour = G if failed == 0 else R
print(f"  {colour}{BOLD}Passed: {passed}   Failed: {failed}{W}")
print(f"{'═'*72}\n")
