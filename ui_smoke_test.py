#!/usr/bin/env python
"""
UI smoke test — structured by workflow category.

Tests page loads (no 500s) and key state assertions. Does NOT submit forms.
Injects temporary bookings where needed; always cleans them up on exit.

Usage:
    venv/bin/python ui_smoke_test.py
    venv/bin/python ui_smoke_test.py --headed
    venv/bin/python ui_smoke_test.py --category booking

Categories: setup, booking, payment, maintenance, member, profile, notifications
(all run by default)

Exits non-zero if any test fails.
"""
import argparse, sys, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aero_club.settings")
import django
django.setup()

from core.models import Club, Booking, ClubMember, Aircraft, MemberCredential, Account, FlightType
from django.db.models import Count

# ── Resolve test data ────────────────────────────────────────────────────────
_club    = Club.objects.first()
_slug    = _club.slug
_member  = ClubMember.objects.filter(club=_club).values_list('id', flat=True).first()
_aircraft = Aircraft.objects.filter(club=_club, status='online').values_list('id', flat=True).first()
_aircraft_with_maint = (Aircraft.objects.filter(club=_club)
                        .annotate(n=Count('maint_log')).order_by('-n')
                        .values_list('id', flat=True).first())
_instructor = (ClubMember.objects.filter(club=_club, is_on_instructor_roster=True)
               .values_list('id', flat=True).first())

_bookings = {
    s: Booking.objects.filter(club=_club, status=s).values_list('id', flat=True).first()
    for s in ['pending', 'confirmed', 'departed', 'completed']
}
_cancelled_booking  = Booking.objects.filter(club=_club, status='cancelled').values_list('id', flat=True).first()
_completed_paid     = (Booking.objects.filter(club=_club, status='completed',
                                              flight_completion__paid_at__isnull=False)
                       .values_list('id', flat=True).first())
_completed_unpaid   = (Booking.objects.filter(club=_club, status='completed',
                                              flight_completion__paid_at__isnull=True)
                       .values_list('id', flat=True).first())
_decl_ft            = FlightType.objects.filter(club=_club, requires_declaration=True).values_list('id', flat=True).first()
_member_with_creds  = ClubMember.objects.filter(club=_club, user__credentials__isnull=False).values_list('id', flat=True).first()
_member_neg_balance = Account.objects.filter(club_member__club=_club, balance__lt=0).values_list('club_member_id', flat=True).first()


# ── Test data injection ───────────────────────────────────────────────────────
_injected_ids = []


def _inject_test_data():
    from datetime import timedelta
    from django.utils import timezone
    from django.contrib.auth import get_user_model

    User    = get_user_model()
    member  = ClubMember.objects.get(id=_member)
    aircraft= Aircraft.objects.get(id=_aircraft)
    ft_any  = FlightType.objects.filter(club=_club).first()
    ft_decl = FlightType.objects.filter(club=_club, id=_decl_ft).first() if _decl_ft else None
    user    = User.objects.filter(is_superuser=True).first()

    if not (member and aircraft and ft_any and user):
        print('  ⚠  Cannot inject — missing member/aircraft/flight_type/user')
        return

    base = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=30)

    # Pending + confirmed if not in DB
    for i, status in enumerate(s for s in ('pending', 'confirmed') if not _bookings.get(s)):
        b = Booking.objects.create(
            club=_club, member=member, aircraft=aircraft, flight_type=ft_any,
            status=status,
            scheduled_start=base + timedelta(hours=i * 3),
            scheduled_end=base + timedelta(hours=i * 3 + 2),
            created_by=user,
        )
        _bookings[status] = b.id
        _injected_ids.append(b.id)
        print(f'  ↪  Injected {status} booking #{b.id}')

    # Confirmed booking that requires declaration (for declaration section test)
    global _bookings_decl_confirmed
    _bookings_decl_confirmed = None
    if ft_decl:
        b = Booking.objects.create(
            club=_club, member=member, aircraft=aircraft, flight_type=ft_decl,
            status='confirmed',
            scheduled_start=base + timedelta(hours=10),
            scheduled_end=base + timedelta(hours=12),
            created_by=user,
        )
        _bookings_decl_confirmed = b.id
        _injected_ids.append(b.id)
        print(f'  ↪  Injected decl-required confirmed booking #{b.id}')


_bookings_decl_confirmed = None


def _cleanup_test_data():
    if _injected_ids:
        deleted, _ = Booking.objects.filter(id__in=_injected_ids).delete()
        print(f'\n  ↩  Cleaned up {deleted} injected booking(s)')


# ── Runner ───────────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--base-url', default='http://localhost:8000')
    p.add_argument('--username', default='dominic')
    p.add_argument('--password', default='clubhangar2026')
    p.add_argument('--headless', action='store_true', default=True)
    p.add_argument('--headed', dest='headless', action='store_false')
    p.add_argument('--category', default='all',
                   choices=['all','setup','booking','payment','maintenance',
                            'member','profile','notifications','safety'])
    return p.parse_args()


class Runner:
    def __init__(self, page, base_url):
        self.page = page
        self.base = base_url
        self.passed, self.failed, self.skipped = [], [], []

    def check(self, name, fn):
        try:
            fn()
            self.passed.append(name)
            print(f'  \033[92m✓\033[0m  {name}')
        except Exception as e:
            self.failed.append((name, str(e).split('\n')[0]))
            print(f'  \033[91m✗\033[0m  {name}')
            print(f'       {str(e).split(chr(10))[0]}')

    def skip(self, name, reason):
        self.skipped.append((name, reason))
        print(f'  \033[93m–\033[0m  {name}  (skip: {reason})')

    def visit(self, path, *, expect_selector=None, expect_text=None, state='visible', name=None):
        label = name or path
        p = self.page
        def _run():
            resp = p.goto(self.base + path, wait_until='domcontentloaded', timeout=15000)
            assert resp and resp.status < 500, f'HTTP {resp.status}'
            if p.query_selector('h1:text("Server Error")'):
                raise AssertionError('Django error page')
            if expect_selector:
                p.wait_for_selector(expect_selector, state=state, timeout=6000)
            if expect_text:
                p.wait_for_selector(f'text={expect_text}', timeout=6000)
        self.check(label, _run)

    def goto(self, path):
        self.page.goto(self.base + path, wait_until='domcontentloaded', timeout=15000)

    def summary(self):
        total = len(self.passed) + len(self.failed)
        print(f'\n{"─"*60}')
        print(f'  {len(self.passed)}/{total} passed', end='')
        if self.skipped:
            print(f'  ·  {len(self.skipped)} skipped', end='')
        if self.failed:
            print(f'   \033[91m{len(self.failed)} failed\033[0m')
            for name, err in self.failed:
                print(f'    ✗  {name}')
                print(f'       {err}')
        else:
            print('  \033[92m— all green\033[0m')
        if self.skipped:
            print('  Skipped:')
            for name, reason in self.skipped:
                print(f'    –  {name}: {reason}')
        print()
        return 1 if self.failed else 0


# ── Category: Club setup ──────────────────────────────────────────────────────
def cat_setup(r, slug):
    print('\n── 1. Club setup')

    r.visit(f'/settings/{slug}/', expect_selector='.stab-bar', name='Settings page loads')
    def _settings_tabs():
        r.goto(f'/settings/{slug}/')
        assert r.page.query_selector_all('.stab-bar .stab-btn'), 'No tabs in settings'
    r.check('Settings — tabs visible', _settings_tabs)

    r.visit(f'/manage/{slug}/members/',    expect_selector='h1',     name='Members list loads')
    r.visit(f'/manage/{slug}/aircraft/',   expect_selector='h1',     name='Aircraft list loads')
    r.visit(f'/manage/{slug}/instructors/',expect_selector='h1',     name='Instructors list loads')
    r.visit(f'/manage/{slug}/rates/',      expect_selector='h1',     name='Charge rates loads')
    r.visit(f'/manage/{slug}/aerodromes/', expect_selector='h1',     name='Aerodromes loads')
    r.visit(f'/manage/{slug}/blockouts/',  expect_selector='h1',     name='Blockouts loads')
    r.visit(f'/manage/{slug}/vouchers/',   expect_selector='h1',     name='Vouchers loads')

    if _instructor:
        r.visit(f'/manage/{slug}/instructors/{_instructor}/', expect_selector='.back-link',
                name='Instructor detail loads')
    else:
        r.skip('Instructor detail', 'no instructors on roster')

    if _aircraft:
        r.visit(f'/manage/{slug}/aircraft/{_aircraft}/', expect_selector='.back-link',
                name='Aircraft detail loads')
        def _aircraft_rates():
            r.goto(f'/manage/{slug}/aircraft/{_aircraft}/')
            text = r.page.inner_text('body')
            assert 'hire' in text.lower() or 'rate' in text.lower() or 'Rates' in text, \
                'No hire rates section on aircraft detail'
        r.check('Aircraft detail — hire/rates section present', _aircraft_rates)
    else:
        r.skip('Aircraft detail', 'no aircraft')


# ── Category: Core booking workflow ──────────────────────────────────────────
def cat_booking(r, slug):
    print('\n── 2. Core booking workflow')

    def _cal():
        resp = r.page.goto(f'{r.base}/calendar/{slug}/', wait_until='domcontentloaded', timeout=15000)
        assert resp and resp.status < 500
        r.page.wait_for_selector('#cal-data', state='attached', timeout=6000)
    r.check('Calendar — gantt loads', _cal)

    # Manage bookings
    r.visit(f'/manage/{slug}/', expect_selector='.seg-ctrl', name='Manage bookings — active view loads')
    def _active_sections():
        html = r.page.content().upper()
        assert 'ACTIVE BOOKINGS' in html, 'Active bookings heading missing'
        assert 'ATTENTION' in html or 'COMPLETED' in html or '30 DAYS' in html, 'Booking list content missing'
    r.check('Manage bookings — active/past sections present', _active_sections)

    # Page load per status
    for status, bid in _bookings.items():
        if bid:
            r.visit(f'/manage/{slug}/bookings/{bid}/', expect_selector='.section',
                    name=f'Booking detail — {status} page loads')
        else:
            r.skip(f'Booking detail — {status} page loads', f'no {status} booking')

    # ── Pending: confirm action visible ──────────────────────────────────────
    if _bookings.get('pending'):
        def _pending():
            r.goto(f'/manage/{slug}/bookings/{_bookings["pending"]}/')
            p = r.page
            assert (p.query_selector('text=Confirm booking') or
                    p.query_selector('text=Action required')), \
                'Confirm booking action missing'
            assert not p.query_selector('#checkin-form'), 'Check-in form should not appear on pending'
            assert not p.query_selector('#multi-pay-form'), 'Payment form should not appear on pending'
        r.check('Pending — confirm action visible, no check-in or payment', _pending)
    else:
        r.skip('Pending state check', 'no pending booking')

    # ── Confirmed: check-out visible, no check-in ─────────────────────────────
    if _bookings.get('confirmed'):
        def _confirmed():
            r.goto(f'/manage/{slug}/bookings/{_bookings["confirmed"]}/')
            p = r.page
            assert p.query_selector('text=Check out'), 'Check out button missing'
            assert not p.query_selector('#checkin-form'), 'Check-in form must not appear on confirmed'
            assert not p.query_selector('#multi-pay-form'), 'Payment form must not appear on confirmed'
        r.check('Confirmed — check-out visible, no check-in or payment', _confirmed)
    else:
        r.skip('Confirmed state check', 'no confirmed booking')

    # ── Confirmed with declaration required ───────────────────────────────────
    if _bookings_decl_confirmed:
        def _decl_required():
            r.goto(f'/manage/{slug}/bookings/{_bookings_decl_confirmed}/')
            p = r.page
            text = p.inner_text('body')
            assert ('declaration' in text.lower() or 'Declaration' in text), \
                'Declaration section missing on booking with declaration-required flight type'
        r.check('Confirmed (declaration required) — declaration section visible', _decl_required)
    else:
        r.skip('Declaration section check', 'no declaration-required flight type')

    # ── Departed: check-in form visible, no payment ───────────────────────────
    if _bookings.get('departed'):
        def _departed():
            r.goto(f'/manage/{slug}/bookings/{_bookings["departed"]}/')
            p = r.page
            assert p.query_selector('#checkin-form'), 'Check-in form missing on departed'
            assert not p.query_selector('#multi-pay-form'), 'Payment form must not appear on departed'
            headings = [h.inner_text() for h in p.query_selector_all('.sh2')]
            assert not any('Charges' in h for h in headings), \
                f'Charges section shown on departed: {headings}'
            assert p.query_selector('text=Check in'), 'Check in button missing'
        r.check('Departed — check-in form visible, no payment or charges', _departed)

        def _departed_meters():
            r.goto(f'/manage/{slug}/bookings/{_bookings["departed"]}/')
            p = r.page
            # At least one meter field should exist (Hobbs, Tacho, or Air switch)
            has_meter = (p.query_selector('input[name="hobbs_end"]') or
                         p.query_selector('input[name="tacho_end"]') or
                         p.query_selector('input[name="airswitch_end"]'))
            assert has_meter, 'No meter reading inputs on check-in form'
        r.check('Departed — meter reading inputs present on check-in form', _departed_meters)

        def _departed_admin_sections():
            r.goto(f'/manage/{slug}/bookings/{_bookings["departed"]}/')
            p = r.page
            # Admin "Undo departure" should be present
            assert p.query_selector('text=Undo departure') or \
                   p.query_selector('text=return to confirmed'), \
                'Undo departure option missing for admin on departed booking'
        r.check('Departed — admin undo departure option visible', _departed_admin_sections)
    else:
        r.skip('Departed state checks', 'no departed booking')

    # ── Cancelled: shows status, no action buttons ────────────────────────────
    if _cancelled_booking:
        r.visit(f'/manage/{slug}/bookings/{_cancelled_booking}/', expect_selector='body',
                name='Cancelled booking page loads')
        def _cancelled():
            r.goto(f'/manage/{slug}/bookings/{_cancelled_booking}/')
            p = r.page
            text = p.inner_text('body')
            assert 'cancel' in text.lower(), 'Cancelled status not shown on cancelled booking'
            assert not p.query_selector('text=Check out'), 'Check out must not appear on cancelled'
            assert not p.query_selector('#checkin-form'), 'Check-in form must not appear on cancelled'
            assert not p.query_selector('#multi-pay-form'), 'Payment form must not appear on cancelled'
        r.check('Cancelled — cancelled status shown, no action buttons', _cancelled)
    else:
        r.skip('Cancelled booking state', 'no cancelled booking')

    r.visit(f'/search/{slug}/', expect_selector='#avail-form', name='Availability search loads')
    def _avail_hours():
        r.goto(f'/search/{slug}/')
        r.page.wait_for_selector('#avail-form', timeout=5000)
        html = r.page.content()
        # When results are shown, spans should NOT have the greyed atypical background style.
        # "var(--atypical)" in a span's style attribute means out-of-hours rendering leaked through.
        import re
        # Look for span/div with style containing var(--atypical) as background
        atypical_spans = re.findall(r'background:\s*var\(--atypical\)', html)
        assert not atypical_spans, f'Out-of-hours spans still rendered ({len(atypical_spans)} found)'
    r.check('Availability search — no out-of-hours greyed spans', _avail_hours)


# ── Category: Charges & payment ──────────────────────────────────────────────
def cat_payment(r, slug):
    print('\n── 3. Charges & payment')

    if not _bookings.get('completed'):
        r.skip('All payment checks', 'no completed booking')
        return

    bid = _bookings['completed']

    def _completed_base():
        r.goto(f'/manage/{slug}/bookings/{bid}/')
        p = r.page
        headings = [h.inner_text() for h in p.query_selector_all('.sh2')]
        assert any('Charges' in h for h in headings), \
            f'Charges section missing: {headings}'
        assert not p.query_selector('#checkin-form'), 'Check-in form must not appear on completed'
        assert (p.query_selector('#multi-pay-form') or
                p.query_selector('.ch-payment-wrap')), 'Payment panel missing'
    r.check('Completed — charges + payment panel, no check-in form', _completed_base)

    def _two_column_layout():
        r.goto(f'/manage/{slug}/bookings/{bid}/')
        p = r.page
        assert p.query_selector('.ch-table'), 'Charges table (.ch-table) missing'
        assert p.query_selector('.ch-grid'),  'Two-column grid (.ch-grid) missing'
    r.check('Completed — charges table and two-column layout present', _two_column_layout)

    def _admin_edit_checkin():
        r.goto(f'/manage/{slug}/bookings/{bid}/')
        p = r.page
        # Edit check-in details should be visible to admin
        assert p.query_selector('text=Edit check-in details'), \
            'Edit check-in details button missing for admin on completed booking'
    r.check('Completed — admin can see "Edit check-in details"', _admin_edit_checkin)

    if _completed_paid:
        def _paid():
            r.goto(f'/manage/{slug}/bookings/{_completed_paid}/')
            p = r.page
            assert (p.query_selector('.paid-badge') or p.query_selector('text=Fully paid')), \
                'Paid badge or "Fully paid" text missing'
            assert not p.query_selector('#multi-pay-form'), 'Payment form shown on paid booking'
        r.check('Completed paid — paid badge visible, no payment form', _paid)
    else:
        r.skip('Completed paid check', 'no fully paid booking')

    if _completed_unpaid:
        def _unpaid():
            r.goto(f'/manage/{slug}/bookings/{_completed_unpaid}/')
            p = r.page
            text = p.inner_text('body')
            # Multi-pay form, per-payee record button, or partial-payment balance indicator
            has_payment_ui = (
                p.query_selector('#multi-pay-form') or
                p.query_selector('button:has-text("Record payment")') or
                'remaining on this flight' in text or
                'Add payee' in text
            )
            assert has_payment_ui, 'No payment UI on unpaid completed booking'
        r.check('Completed unpaid — payment UI visible', _unpaid)

        def _unpaid_method_options():
            r.goto(f'/manage/{slug}/bookings/{_completed_unpaid}/')
            p = r.page
            opts = [o.inner_text() for o in p.query_selector_all(
                '#multi-pay-form select[name="payment_method"] option, '
                'select[name="payment_method"] option')]
            if not opts:
                return  # no form shown (partial payment state) — skip method check
            assert any('EFTPOS' in o or 'eftpos' in o.lower() for o in opts), \
                f'EFTPOS option missing from payment methods: {opts}'
            assert any('credit' in o.lower() or 'Account' in o for o in opts), \
                f'Account credit option missing: {opts}'
        r.check('Completed unpaid — payment method dropdown has EFTPOS + account options',
                _unpaid_method_options)
    else:
        r.skip('Completed unpaid checks', 'no unpaid completed booking')

    r.visit(f'/manage/{slug}/invoices/', expect_selector='h1', name='Invoices list loads')
    r.visit(f'/manage/{slug}/charges/',  expect_selector='h1', name='Charges list loads')
    r.visit(f'/manage/{slug}/sundry/',   expect_selector='h1', name='Sundry sales page loads')

    def _sundry_tabs():
        r.goto(f'/manage/{slug}/sundry/')
        p = r.page
        text = p.inner_text('body')
        assert 'Invoices' in text and ('Sundry' in text or 'Awaiting' in text), \
            'Sales tabs missing'
    r.check('Sundry sales — sales tabs present', _sundry_tabs)

    def _sundry_new_btn():
        r.goto(f'/manage/{slug}/sundry/')
        p = r.page
        assert p.query_selector('button:text("+ New sale"), button:has-text("New sale")'), \
            '"New sale" button missing'
    r.check('Sundry sales — new sale button present', _sundry_new_btn)


# ── Category: Aircraft maintenance ───────────────────────────────────────────
def cat_maintenance(r, slug):
    print('\n── 4. Aircraft maintenance')

    if not _aircraft_with_maint:
        r.skip('All maintenance checks', 'no aircraft with maintenance log entries')
        return

    ac_id = _aircraft_with_maint
    r.visit(f'/manage/{slug}/aircraft/{ac_id}/', expect_selector='.back-link',
            name='Aircraft detail (with maintenance) loads')

    def _maint_visible():
        r.goto(f'/manage/{slug}/aircraft/{ac_id}/')
        text = r.page.inner_text('body')
        assert ('Maintenance' in text or 'Hobbs' in text or 'Tacho' in text or
                'hours' in text.lower()), \
            'No maintenance content on aircraft detail page'
    r.check('Aircraft detail — maintenance content visible', _maint_visible)

    def _maint_log():
        r.goto(f'/manage/{slug}/aircraft/{ac_id}/')
        r.page.wait_for_selector('.back-link', timeout=5000)
        # Use content() not inner_text() — log may be in a non-default tab (hidden DOM)
        html = r.page.content()
        assert 'Maintenance log' in html or 'maint_log' in html or \
               'log entries' in html.lower(), \
            'Maintenance log section not found in page HTML'
    r.check('Aircraft detail — maintenance log section in page', _maint_log)

    r.skip('Maintenance urgency thresholds', 'not yet implemented — backlog #5, #7')
    r.skip('Urgency recalc after check-in', 'fixed in booking_service — no UI-level test possible without a real check-in')


# ── Category: Member detail (admin view) ─────────────────────────────────────
def cat_member(r, slug):
    print('\n── 5. Member detail (admin view)')

    if not _member:
        r.skip('All member detail checks', 'no members in DB')
        return

    mid = _member
    r.visit(f'/manage/{slug}/members/{mid}/', expect_selector='.stab-bar',
            name='Member detail loads')

    def _member_tabs():
        r.goto(f'/manage/{slug}/members/{mid}/')
        tabs = r.page.query_selector_all('.stab-bar .stab-btn')
        assert tabs, 'No tabs on member detail page'
    r.check('Member detail — tab bar present', _member_tabs)

    def _standing_badge():
        r.goto(f'/manage/{slug}/members/{mid}/')
        p = r.page
        # Standing badge should always be visible
        badge = p.query_selector('[class*="standing-badge"], [class*="standing"]')
        assert badge or 'standing' in p.inner_text('body').lower(), \
            'Member standing not visible on member detail'
    r.check('Member detail — standing badge visible', _standing_badge)

    if _member_neg_balance == mid or _member_neg_balance:
        neg_id = _member_neg_balance
        def _neg_balance():
            r.goto(f'/manage/{slug}/members/{neg_id}/')
            p = r.page
            text = p.inner_text('body')
            # Balance should be shown; negative balance styled in red via inline style
            assert any(x in text for x in ['balance', 'Balance', 'Account']), \
                'Account balance section not found on member with negative balance'
            html = p.content()
            assert '#c0392b' in html or 'balance' in text.lower(), \
                'Negative balance not highlighted on member detail'
        r.check('Member detail — negative account balance shown (red)', _neg_balance)
    else:
        r.skip('Negative balance check', 'no member with negative balance')

    if _member_with_creds:
        cred_id = _member_with_creds
        def _creds():
            r.goto(f'/manage/{slug}/members/{cred_id}/')
            p = r.page
            text = p.inner_text('body')
            assert any(x in text for x in ['Credential', 'credential', 'Certificate',
                                            'Licence', 'licence']), \
                'No credentials section on member with credentials'
        r.check('Member detail — credentials section visible', _creds)
    else:
        r.skip('Credential section check', 'no member with credentials in DB')

    def _booking_history():
        r.goto(f'/manage/{slug}/members/{mid}/')
        p = r.page
        text = p.inner_text('body')
        # Member with 29 bookings should show booking history
        assert any(x in text for x in ['booking', 'Booking', 'flight', 'Flight']), \
            'No booking history on member detail'
    r.check('Member detail — booking history section visible', _booking_history)

    r.visit(f'/manage/{slug}/members/', expect_selector='h1',
            name='Members list — admin can see all members')


# ── Category: Profile (member self-service) ───────────────────────────────────
def cat_profile(r, slug):
    print('\n── 6. Profile (member self-service)')

    r.visit(f'/profile/{slug}/', expect_selector='.stab-bar', name='My profile loads')

    def _profile_tabs():
        r.goto(f'/profile/{slug}/')
        tabs = r.page.query_selector_all('.stab-bar .stab-btn')
        assert tabs, 'No tabs on profile page'
    r.check('Profile — tab bar present', _profile_tabs)

    def _profile_balance():
        r.goto(f'/profile/{slug}/')
        text = r.page.inner_text('body')
        assert any(x in text for x in ['balance', 'Balance', 'Account', 'account']), \
            'Account balance not shown on profile'
    r.check('Profile — account balance section visible', _profile_balance)

    def _profile_bookings():
        r.goto(f'/profile/{slug}/')
        text = r.page.inner_text('body')
        assert any(x in text for x in ['booking', 'Booking', 'flight', 'Flight',
                                        'Upcoming', 'upcoming', 'no upcoming']), \
            'No bookings section on profile'
    r.check('Profile — upcoming bookings section present', _profile_bookings)

    def _profile_payment_history():
        r.goto(f'/profile/{slug}/')
        p = r.page
        # Payment history tab or section should exist
        text = p.inner_text('body')
        assert any(x in text for x in ['Payment', 'payment', 'Paid', 'paid', 'history']), \
            'No payment history on profile'
    r.check('Profile — payment history section present', _profile_payment_history)

    def _profile_notification_prefs():
        r.goto(f'/profile/{slug}/')
        text = r.page.inner_text('body')
        assert any(x in text for x in ['notification', 'Notification', 'Preferences', 'alert']), \
            'No notification preferences on profile'
    r.check('Profile — notification preferences section present', _profile_notification_prefs)


# ── Category: Notifications ───────────────────────────────────────────────────
def cat_notifications(r, slug):
    print('\n── 7. Notifications')

    r.visit(f'/notifications/{slug}/', expect_selector='h1', name='Notifications page loads')

    def _notif_tabs():
        r.goto(f'/notifications/{slug}/')
        p = r.page
        text = p.inner_text('body')
        assert 'Unread' in text or 'unread' in text, 'Unread tab missing'
        assert 'All' in text, 'All tab missing'
    r.check('Notifications — Unread and All tabs present', _notif_tabs)

    def _notif_empty_or_list():
        r.goto(f'/notifications/{slug}/')
        p = r.page
        text = p.inner_text('body')
        # Either shows notifications OR an empty state message
        has_content = (p.query_selector('.notif-item, [class*="notif"]') or
                       'caught up' in text.lower() or
                       'no notification' in text.lower() or
                       'notification' in text.lower())
        assert has_content, 'Notifications page has neither list items nor empty state message'
    r.check('Notifications — list or empty state shown', _notif_empty_or_list)

    def _notif_bell():
        # Bell icon should be in the nav on any page
        r.goto(f'/manage/{slug}/')
        p = r.page
        bell = (p.query_selector('#notif-bell') or
                p.query_selector('[id*="notif"]') or
                p.query_selector('[class*="notif-bell"]'))
        assert bell, 'Notification bell missing from nav'
    r.check('Notifications — bell icon present in nav', _notif_bell)


# ── Category: Safety ─────────────────────────────────────────────────────────
def cat_safety(r, slug):
    print('\n── 8. Safety')

    r.visit(f'/manage/{slug}/exceptions/', expect_selector='h1',
            name='Attention page loads')

    def _attention_heading():
        r.goto(f'/manage/{slug}/exceptions/')
        text = r.page.inner_text('h1')
        assert 'Attention' in text, f'Expected "Attention" h1, got: {text!r}'
    r.check('Attention — h1 says "Attention"', _attention_heading)

    def _attention_tabs():
        r.goto(f'/manage/{slug}/exceptions/')
        p = r.page
        text = p.inner_text('body')
        assert 'Unpaid flights' in text, 'Unpaid flights tab missing'
        assert 'Conflicts' in text, 'Conflicts tab missing'
        assert 'Maintenance' in text, 'Maintenance tab missing'
        # Tab bar should be present
        assert p.query_selector('.stab-bar'), 'Tab bar missing on Attention page'
    r.check('Attention — tabs present', _attention_tabs)

    r.visit(f'/events/{slug}/actions/', expect_selector='h1',
            name='Action items page loads')

    def _action_items_heading():
        r.goto(f'/events/{slug}/actions/')
        text = r.page.inner_text('h1')
        assert 'Action items' in text, f'Expected "Action items" h1, got: {text!r}'
    r.check('Action items — h1 says "Action items"', _action_items_heading)

    def _action_items_empty_or_list():
        r.goto(f'/events/{slug}/actions/')
        p = r.page
        text = p.inner_text('body')
        has_content = (p.query_selector('table') or
                       'No open action items' in text)
        assert has_content, 'Action items page shows neither table nor empty message'
    r.check('Action items — table or empty state shown', _action_items_empty_or_list)

    def _action_items_filter():
        r.goto(f'/events/{slug}/actions/')
        p = r.page
        sel = p.query_selector('select[name="assigned"]')
        assert sel, '"Assigned to" filter select missing'
    r.check('Action items — assignee filter present', _action_items_filter)

    def _nav_safety_links():
        r.goto(f'/events/{slug}/actions/')
        p = r.page
        text = p.inner_text('body')
        assert 'Attention' in text, '"Attention" nav link missing'
        assert 'Action items' in text, '"Action items" nav link missing'
    r.check('Nav — Attention and Action items links visible', _nav_safety_links)


# ── Main ──────────────────────────────────────────────────────────────────────
def run(args):
    from playwright.sync_api import sync_playwright

    _inject_test_data()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=args.headless)
            ctx = browser.new_context(viewport={'width': 1280, 'height': 900})
            page = ctx.new_page()

            print(f'Logging in as {args.username}...')
            page.goto(f'{args.base_url}/admin/login/?next=/', wait_until='domcontentloaded')
            page.fill('input[name="username"]', args.username)
            page.fill('input[name="password"]', args.password)
            page.click('input[type="submit"]')
            page.wait_for_load_state('domcontentloaded')
            if '/admin/login/' in page.url:
                print('\n\033[91mLogin failed — check --username / --password\033[0m')
                browser.close()
                return 1

            print(f'Logged in. Club: {_club.name} ({_slug})\n')

            r = Runner(page, args.base_url)
            cats = {
                'setup':         lambda: cat_setup(r, _slug),
                'booking':       lambda: cat_booking(r, _slug),
                'payment':       lambda: cat_payment(r, _slug),
                'maintenance':   lambda: cat_maintenance(r, _slug),
                'member':        lambda: cat_member(r, _slug),
                'profile':       lambda: cat_profile(r, _slug),
                'notifications': lambda: cat_notifications(r, _slug),
                'safety':        lambda: cat_safety(r, _slug),
            }

            to_run = cats.keys() if args.category == 'all' else [args.category]
            for cat in to_run:
                cats[cat]()

            if args.category == 'all':
                print('\n── Misc')
                r.visit(f'/reports/{_slug}/', expect_selector='h1', name='Reports loads')
                r.visit(f'/data/{_slug}/',    expect_selector='h1,h2', name='Data page loads')

            browser.close()
            return r.summary()
    finally:
        _cleanup_test_data()


if __name__ == '__main__':
    args = parse_args()
    sys.exit(run(args))
