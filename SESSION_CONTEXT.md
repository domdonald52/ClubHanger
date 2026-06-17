# ClubHangar — Session Context
**Last updated:** 2026-06-17 | **Reload this file at the start of every new session.**

---

## What this is
Django booking system for Wellington Aero Club (NZ). Replacing "Paper Aviator". Multi-club capable.
- **Stack:** Django 6.0.5, Python 3.12, SQLite (dev)
- **Run:** `cd ~/projects/aero_club && venv/bin/python manage.py runserver`
- **GitHub:** https://github.com/domdonald52/ClubHangar
- **Latest migration:** `0036_voucher`

---

## Architecture brief (must follow — see BRIEF_tomorrow.md)

1. **Business rules live in service modules**, not views. Services: `core/services/booking_service.py`, `availability_service.py`, `charging_service.py`, `qualification_service.py`. Views call services; services return `ServiceResult(ok, error, data)`.
2. **Pin the rule before writing code.** Ambiguous rule → ask first.
3. **Block/warn/override is the default pattern** for any rule that can fail. Hard-block members; staff see warn + override reason field; override is audit-logged. Don't re-ask per feature — assume this shape.
4. **State migration cost and blast radius** before any schema change.
5. **Refactors are behaviour-preserving** unless explicitly agreed otherwise.
6. **Audit everything** that changes a booking, member, or resource.
7. **Migrations run locally** in the venv. Never import from elsewhere.

---

## Part 2 decisions — STATUS

| Decision | Status |
|---|---|
| **A. Resource abstraction** (aircraft/instructor/rooms) | ❌ NOT settled. Do not build rooms. |
| **B. Service layer shape** | ✅ Decided: `ServiceResult(ok, error, data)` dataclass; pure functions with club/config passed in; no Django objects returned. |
| **C. Eligibility** | ✅ Block/warn/override confirmed. Check at confirmation AND check-out. CAA-fixed rules (PPL, medical, BFR) are not club-configurable; club conventions (recency, warning days) are. |
| **D. Location API** | ❌ NOT scoped. Do not build. |

---

## What's built

### Service layer (`core/services/`)
- `booking_service.py` — `confirm()`, `depart()`, `reschedule()`, `cancel()`, `update_total()`, `audit()`, `check_blockout()`
- `availability_service.py` — slot finding
- `charging_service.py` — `add_charge()`, `delete_charge()`, `record_payment()`
- `qualification_service.py` — `check_eligibility()` → `EligibilityResult` with `EligibilityItem(check, label, severity, message)`. Checks: PPL, medical (class 1/2/3, age-adjusted), BFR, type rating, recency.

### Models (migration 0035)
- **ClubConfig** — compliance settings (medical intervals, BFR interval, warning days, `fy_start_month`); now also has `maint_warn_hours/alert_hours/warn_days/alert_days` defaults
- **Aircraft** — `is_leased` boolean, `aircraft_type` FK(AircraftType), `records_hobbs/tacho/airswitch`; now also `maint_time_source` (hobbs/tacho/airswitch), `maint_time_fraction` (e.g. 0.95), `maint_hours_initial`
- **AircraftType** — club-scoped managed list
- **MemberCredential** — `aircraft_type` FK nullable (for type ratings)
- **FlightCompletion** — `amount_paid`, `balance_owing` property, `is_paid`, `is_partially_paid`
- **Invoice** — draft→sent→paid/void lifecycle
- **DepartureDeclaration** — model exists, standalone UI not yet built
- **AircraftMaintenanceItem** — now has `interval_hours`, `interval_days`, `warn_hours/days`, `alert_hours/days`, `notes`; `due_hours` is cumulative maintenance hours; has `hours_remaining`, `current_maint_hours`, `recalc_urgency()` properties
- **MaintenanceLogEntry** *(new)* — one per flight check-in; records raw meter readings + `maint_hours_flight` + `maint_hours_total` (running cumulative). Auto-created via `create_maint_log_entry(flight_completion)` called from check-in view.

### Flight lifecycle
Status: `PENDING → CONFIRMED → DEPARTED (checked out) → COMPLETED → CANCELLED`
- Check-out (`depart` action): runs `qualification_service.check_eligibility()`; if any `block` items, requires override reason (POST-validated, audit-logged)
- Check-in (`checkin` action): records hobbs/tacho, computes flight hours, auto-creates charge line items
- Terminology: "Check out" / "Check in" (not depart/return)

### Gantt calendar
- Booking create/edit/confirm/cancel via modal
- Pill drag=move (blocked for departed), resize=extend
- Instructor conflict warning in modal (client-side)
- Now-line: 1px JS overlay, updates every 30s
- Row-label width: JS-measured from scrollWidth
- Section separators: Instructors / Aircraft

### Navigation
- Hamburger button → slide-out sidenav (left panel)
- Groups: Navigation (Calendar, Find Slots), Manage (Bookings, Block-outs, Members, Aircraft, Instructors, Payments, Invoices), Analytics (Reports), Admin (Settings), Account

### Reports (`/reports/<club>/`)
- **Tabbed** (Aircraft hours, Instructor hours, Members, Payments, Analytics)
- Aircraft: stacked bar chart per aircraft per month; All/Owned/Leased filter
- Instructors: stacked bar chart per instructor per month
- Members: table of flight count + hours, sorted by most hours
- Payments: monthly table of flights, charged, collected; totals row
- Analytics: drag-and-drop concept demo — drag fields to Rows/Values zones; groups demo data into a pivot table. Fields: aircraft, flight_type, member, month, hours, charge. Not yet connected to full dataset.
- All charts: Chart.js 4 CDN. FY from `ClubConfig.fy_start_month`
- Tab preference persisted in localStorage

### Other views
- Member profile: details, account, bookings, upcoming instruction schedule (7 days)
- Aircraft detail: rates, surcharges, maintenance (progress bars, urgency), block-outs, history
- Instructor detail: availability windows, block-outs, upcoming bookings
- Manage: Bookings, Block-outs, Members, Aircraft, Instructors, Aerodromes, Payments, Invoices
- Club settings: General, Appearance, Scheduling, Charge types, Block-out types, Aircraft types, Billing (with FY start)

---

## Known gaps vs the brief

### Still in views, not services (should migrate eventually)
- `booking_detail` POST handler still contains inline depart/checkin logic (calls services but also has direct model manipulation for checkin); `create_maint_log_entry()` is called inline here after fc.save()
- `manage_aircraft_detail` POST handler — add/edit rates, maintenance items
- No `maintenance_service.py` yet

### Not yet built (from backlog)
- **DepartureDeclaration UI** — model exists, no standalone form. Currently soft-blocked at check-out.
- **Manage > Exceptions screen** — one triage view: booking conflicts + unpaid flights + lapsed members with bookings + amber/red maintenance. Rename "Conflicts" tab.
- **Maintenance log UI** — `MaintenanceLogEntry` model exists, auto-populated on check-in; needs view/tab on aircraft detail showing log entries + per-item `hours_remaining`. Aircraft maintenance tab currently shows items but not the new log or computed remaining hours.
- **Club settings — Maintenance tab** — `maint_warn_hours/alert_hours/warn_days/alert_days` in ClubConfig; not yet shown in settings UI.
- **Reporting — FlyingBudget** — `FlyingBudget` model (aircraft + month + budgeted_hours) not yet created; budget vs actual chart, 4-year historical chart.
- **Analytics builder** — drag-and-drop concept is done (demo only); needs server-side query engine to handle real pivot queries.
- **Member profile fields** — date of birth, next of kin (on ClubMember?), frequent passengers (FrequentPassenger model — exists but no UI).
- **SAR time** on declaration.
- **Slot release notifications** — opt-in by aircraft/instructor/period.
- **Incident/Occurrence log** — standalone module, feeds committee paper Appendix A.
- **Hard/soft block-out enforcement** — `BlockOutType.is_hard` exists; soft block UI/override flow not built.
- **Flight Following contacts** — notification contacts with delay (like Paper Aviator); model not yet built.
- **Meeting rooms** — DO NOT BUILD until Part 2A decided.

### Global CSS/component conventions (session 3, now enforced)
- **`base.html`** defines ALL shared CSS: `seg-ctrl/seg-btn` (filter toggles), `status-pill sp-on/sp-off/sp-warn` (row boolean), `stab-bar/stab-btn/stab-panel` (in-page tabs), `ft-pill` (table pill), `tog-sw` (iOS toggle), `mnav/mnav-link` (manage nav)
- **`base_inline.html`** mirrors all the above for overlay-rendered pages
- **Rule:** Never define shared component CSS in a template `<style>` block — global only
- Local `.mnav` overrides removed from all manage templates; `overflow-x:auto` was the scrollbar bug on blockouts/aerodromes
- Toggles standardized: `seg-btn` for dual/solo and all/conflicts; `status-pill` for per-row active/inactive and aircraft online/retired; `ft-pill` for table inline properties

### Vouchers (migration 0036)
- `Voucher` model: club, code, value, description, is_redeemed, redeemed_by, redeemed_at
- `/manage/<club>/vouchers/` — create voucher; redeem → credits member's Account via AccountTransaction (top_up, credit)
- Listed in manage nav (admin only)

### Still to standardize (deferred)
- **Aircraft `is_available_for_hire` vs `status`** — `status=ONLINE/RETIRED` means "is the aircraft still in service"; `is_available_for_hire` means "can members book it". The list now shows a `status-pill` click-toggle for ONLINE/RETIRED (with confirm for retire). The `is_available_for_hire` checkbox on Details tab is still a raw checkbox — consider replacing with `tog-sw`.
- **`tog-sw`** defined in base.html but not yet applied anywhere — candidate for `is_available_for_hire`, `is_leased`, blockout type hard/soft boolean settings.

### Paper Aviator feature parity notes (from screenshots)
Paper Aviator settings tabs: Company Details, Resources, Flight Types, Airports, Landing Fees, Airways Fees, Charge Types, Other Charges, Member Types, Voucher Types, Flight Following, Maintenance Alerts.
- **Resources** — not seen in our UI yet; likely maps to aircraft/instructor config
- **Airports / Landing Fees / Airways Fees** — we have Aerodromes model; landing fees and airways fees are charge types in our model
- **Other Charges** — per-flight surcharges; we have `AircraftSurchargeType` and `FlightChargeItem`
- **Member Types** — we have `Role`; PA has richer member type config (renewal price, track BFR, track medical, create bookings)
- **Voucher Types** — not yet in our model
- **Flight Following** — notification contacts with delay; not yet built
- **Maintenance Alerts** — PA has hours-based thresholds (show/warn/alert at 50/20/5 hours, 30/14/7 days); our `ClubConfig` now has these
- **Invoice concept** — PA has no invoice lifecycle; it prints/emails and forgets. Our invoice model (draft→sent→paid/void) is a significant improvement for reconciliation.

---

## Data migration (Paper Aviator → ClubHangar) — IN PROGRESS

Goal: a **reusable importer** (re-run many times before go-live), fed by data
shaped out of Paper Aviator's reports (mixed PDF/CSV, some per-member/aircraft).
Load order follows FKs: reference data → Aircraft → Members → Credentials →
Accounts/balances → Flights → Invoices → Maintenance → Occurrences.

**Pinned decisions:**
- **Test club**, not the live club — protects demo seed data. Imports target an
  isolated club; reset between test runs.
- **Aircraft**: done **manually** (only a few live; PA has many retired ones).
- **Members importer (built)**: everyone → default **Member** role (set
  instructor/admin by hand later); no email → synth `first.last@migrated.invalid`,
  flagged; **never** sends invite/welcome emails on import; opening balances and
  medical/BFR expiry **deferred** to later financial/credentials passes.
- Natural key for re-runnable upsert = **email**.

**Management commands added** (`core/management/commands/`):
- `setup_test_club.py` — creates/ensures isolated club, runs `setup_defaults`,
  stamps built-in roles with `system_role_type`. `--reset` wipes members +
  orphaned users. `--slug` (default `migration-test`).
- `import_members.py` — reads the 'Members' template sheet (.xlsx) or CSV;
  header-driven (tolerant of optional Standing / Subscription expires columns);
  `--dry-run` (validates in a rolled-back transaction); idempotent upsert on
  email; collects row-level errors; one atomic transaction.
  Run: `manage.py import_members <file> --club migration-test --dry-run`
- **NOT yet run end-to-end** — container can't install Django 6.0.5. Verify on a
  real 6.0 env. Next slices: Credentials, Accounts/balances, Flights.

## Before go-live — deployment & data isolation (forward checklist)

Captured 2026-06-17. Nothing here needs code changes now — these are
decisions to action when the real club goes live, so they don't have to be
re-figured-out under pressure.

**How clubs are routed:** by **slug in the URL path** (`/app/<club_slug>/`,
`/manage/<club_slug>/`, …) — one Django app, one domain. As currently
deployed, the demo and a real club would share the **same Railway domain
and the same database**, differing only by slug:
- Demo → `/app/wac-demo/`
- Production → `/app/wellington-aero-club/`

**Decisions to action before real members onboard:**
- [ ] **Separate Railway environments (recommended).** Put the real club on a
      `production` environment with its **own database + domain**; keep the
      demo/dev club on `staging`. Then demo seeding/`--reset` physically
      *cannot* touch production data, and the prod domain never serves the
      demo club. (Alternative = stay on one deployment + DB, relying only on
      the slug guard below. Fine while building; not for go-live.)
- [ ] **Production club populated via the Paper Aviator migration importer**
      (see Data migration section), **never** the demo seed.
- [ ] **Real billing/rates/member data entered via the Settings UI or the
      importer — never via the demo seed.** The seed is demo-only.
- [ ] **Reserve/protect the production slug** `wellington-aero-club`. The demo
      seed already has a **guard** that refuses to write to the production
      slug, so a stray seed/reset can't clobber the real club even on a shared
      DB. Keep that guard; on separate environments it's belt-and-braces.

When the time comes, ask Claude for the exact Railway steps to spin up the
separate `staging` environment — it's all Railway-side config, no code change.

### Authentication & login — DONE (2026-06-17)

Branded login flow built (was previously the bare Django admin login):
- **Routes** registered at project root in `aero_club/urls.py` with the standard
  un-namespaced names so `redirect('login')` (~50 call sites) and
  `LOGIN_URL='login'` resolve: `login`, `logout`, `password_reset`,
  `password_reset_done`, `password_reset_confirm`, `password_reset_complete`.
- **Templates** in `core/templates/registration/` extend `auth_base.html`
  (ClubHangar card branding, matches `invite_accept.html`).
- **Login form**: `core/auth_forms.py::EmailAuthenticationForm` — labels the
  username field "Email" (members log in with email = username) + friendly
  error copy. django-axes lockout still applies.
- **Password reset** uses Django's built-in views + the configured
  `EMAIL_BACKEND` (console in dev; set SMTP env vars in prod).
- **Logout** is now POST (Django 6 requirement). The 3 sign-out links
  (`base.html` header dropdown + sidenav, `app/profile.html`) submit a hidden
  POST form to `{% url 'logout' %}`; `LOGOUT_REDIRECT_URL='login'`.
- **Note:** login screen is ClubHangar (product) branded, not club-specific —
  it sits before club selection. Per-club login branding would need login under
  a club slug; deferred unless wanted.
- **Multi-club login** → `index` view: 0 clubs = `no_access.html`; 1 club =
  straight to that club's calendar; 2+ = `club_select.html` chooser. Header
  dropdown also has "Switch to <club>" links. **Confirmed good** (2026-06-17):
  the club picker should only appear for users who belong to 2+ clubs;
  single-club users go straight in.
- [ ] **TO DO — decide post-login landing per role.** Today every fresh sign-in
      lands on the **web calendar** (`index` → `gantt_day`), even normal members
      who'd more naturally start in the **mobile app** (`/app/<slug>/`). A
      `?next=` link is honoured, but a bare login isn't. Decide: should members
      land on the mobile app home and staff on the web calendar? (Applies after
      club selection for multi-club users.)

### Help guides (member-facing docs) — DONE (2026-06-17)

- [x] **Member web-app help guide** — `member_guide` view, `/guide/<club>/`,
      template `core/member_guide.html` (reuses the staff guide's CSS). Covers
      sign-in, mobile app, calendar, booking, standing, account, credentials,
      help. Visible to **any club member**; linked in the web sidenav `{% else %}`
      (non-staff) as "Help guide".
- [x] **Mobile-app (PWA) help guide** — `app_guide` view, `/app/<club>/guide/`,
      template `core/app/guide.html` (extends `core/app/base.html`). Short,
      card-based tour. Linked from the app Profile page ("📖 Help guide").
- [x] **Guide visibility gated by role:** existing staff guide (`manage_guide`,
      `require_staff`) → instructors & admins, link shown only to staff; member
      guide → everyone else. Role test = `ClubMember.is_staff`
      (= `is_admin or is_instructor`).
- [x] **Invite sequence** documented in the staff/admin guide (`#mem-new`
      step-flow) — now also notes the new Copy-link option and clarifies that
      inviting does NOT create the member until they accept.

### Invite member — Copy-link + behaviour (DONE 2026-06-17)

- **Copy invite link** button added to the *Pending invites* table on
  `manage_members` (admin only). Builds the full accept URL client-side
  (`{{ request.scheme }}://{{ request.get_host }}{% url accept_invite token %}`)
  and copies via `navigator.clipboard`. Lets you demo the join flow without
  relying on email (e.g. while `EMAIL_OVERRIDE_TO` is set for testing).
- **Key behaviour (confirmed):** sending an invite creates ONLY a `ClubInvite`
  (7-day expiry). The `User` + `ClubMember` (standing `pending`) are created
  when the recipient opens the link and sets name/password (`accept_invite`).
  Exception: *+ Add manually* with "Send invite email" unticked creates the
  account immediately with a (possibly auto-generated) password.
- **Email reality:** `EMAIL_OVERRIDE_TO` (if set) redirects ALL outgoing mail to
  that address; `_send()` swallows failures (the green "Invite sent" toast does
  NOT prove delivery — check the inbox/logs). SMTP set up in Railway but
  overridden on purpose for testing as of this date.

### Mobile PWA name + multi-club picker (DONE 2026-06-17)

- **Installed-app name is now "ClubHangar"** (was the club name). `pwa_manifest`
  sets `name`/`short_name` = "ClubHangar"; the icon still uses the club's logo
  and theme. **iOS caches the home-screen name at install time** — existing
  installs must remove & re-add the icon to pick up the new name.
- **Mobile club picker:** new slug-less entry `path('app/', views.app_root,
  name='app_root')`. The manifest `start_url`/`scope` are now `/app/`, so opening
  the installed app routes through `app_root`:
  - 0 clubs → `no_access`; 1 club → redirect straight to `app_home`;
    2+ clubs → `core/app/club_select.html` (mobile-styled picker → each club's
    `app_home`).
- So the **club pick-list now appears in BOTH contexts**: web (`index` →
  `club_select.html` → web calendar) and mobile (`app_root` →
  `app/club_select.html` → mobile app). Single-club members never see it.
- Member guide "open the app" URL updated to `/app/`.

### False "instructor off roster" warnings — FIXED (2026-06-17)

- **Root cause:** the off-roster conflict check treated an instructor with NO
  availability windows as *unavailable* — contradicting the model
  (`InstructorAvailability`: "no records = available all operating hours") and
  the gantt ghost-row logic (which correctly treats none = available). So every
  instructor booking was flagged on the Attention page / gantt. Affected real
  clubs too, not just the demo.
- **Fixes (`core/views.py`):** `_check_live_conflict` now flags only when
  `roster is False` (was `is not True`); `_instructor_off_roster` returns
  `False` when there are no windows (was `True`). Now only instructors who HAVE
  windows and none apply on the date are flagged.
- **Seed:** `_setup_instructor_availability` gives every demo instructor
  full-week all-day windows (realism + populates the availability search).
- **Part B (discoverability):** changing the instructor on a confirmed booking
  was possible but hidden behind a vague "Edit details" button on the checkout
  screen — relabelled to **"Change instructor / aircraft"** in
  `booking_detail.html` (the `change_details` action already worked).

### Two demo clubs + Railway healthcheck (DONE 2026-06-17)

- **Railway is staging/testing only**; production will move elsewhere at go-live.
  The real `wellington-aero-club` club exists in the Railway DB (pre-dates the
  demo work) — fine for staging, not an isolation problem there.
- **`seed_demos`** (new command) seeds TWO clearly-fictional demo clubs —
  **Skyhaven Aero Club** (`skyhaven`) and **Brightwater Flying Club**
  (`brightwater`) — via `seed_demo`. The seeded admin is a member of both, so
  signing in shows the **club picker** (web + mobile). Run:
  `python manage.py seed_demos` (or `--reset`).
- **`seed_demo --reset` is now multi-club-safe**: it only deletes users orphaned
  from ALL clubs, so reseeding one demo club no longer deletes members shared
  with another.
- **Railway healthcheck** moved from `/admin/login/` → `/login/` in
  `railway.toml`, so setting `ADMIN_URL` (to hide the admin) doesn't fail the
  healthcheck.

### Production environment variables (Railway) — GO-LIVE CHECKLIST

Set these in the hosting platform's Variables (Railway → service → Variables),
NOT in source. Re-deploy after changing.

- [ ] **`ADMIN_URL`** — hide the Django admin. Set to a non-guessable path
      ending in `/`, e.g. `flightdeck-7g3k/`. Defaults to `admin/` if unset.
      Do the `dominic` username/email fix via `/admin/` FIRST, then set this.
- [ ] **`SEED_ADMIN_EMAIL`** — only relevant if you ever (re)run `seed_demo`.
      Sets the demo admin's login email (default placeholder
      `admin@wac-demo.example`). Set to a real address to receive its
      password-reset emails.
- [ ] **`EMAIL_OVERRIDE_TO`** — currently set on purpose for testing (redirects
      ALL outgoing mail to you). **Clear it at go-live** so members get their own
      mail. SMTP already configured in Railway.
- [ ] **`SITE_URL`** — must be the live `https://…` URL (used to build links in
      invite / password-reset emails; blank = broken links).

### Seeded admin login + emails (DONE 2026-06-17)

- Seeded admin's **username is now an email** (`ADMIN_EMAIL`, from
  `SEED_ADMIN_EMAIL`, default `admin@wac-demo.example`) so it works with the
  email-enforcing login (the old `dominic` username could not be typed there).
- Every seeded user now gets a non-blank email (`<username>@wac-demo.example`)
  so password reset never silently no-ops on a missing address.
- Existing live `dominic` account was fixed manually by the user (username +
  email set to their real address via `/admin/`).

### Auth pages no-cache (DONE 2026-06-17)

- `NoCacheAppMiddleware` now also no-caches `/login`, `/logout`,
  `/password-reset`, `/reset/` (previously only `/app/`). Prevents a stale
  cached login/reset page (which can look like an old Django screen).
- NOTE: "Forgot password?" → branded `/password-reset/` has ALWAYS been correct
  in code (git-verified). If the Django admin reset still shows, it's a stale
  deploy/cache — confirm Railway deployed latest `main` + hard refresh.

### Configurable Django admin path (DONE 2026-06-17)

- Django admin is no longer hardcoded at `/admin/`. `aero_club/urls.py` uses
  `path(settings.ADMIN_URL, admin.site.urls)`, where `ADMIN_URL` comes from the
  `ADMIN_URL` env var (default `admin/`). Keeps the real, non-obvious path out
  of source control (repo is on GitHub).
- **To lock down production:** set `ADMIN_URL` in Railway to a non-guessable
  path, e.g. `flightdeck-7g3k/` (must end with `/`), then redeploy. Defends
  against bots hitting `/admin/`; django-axes still throttles brute-force and
  admin still requires `is_staff`.
- **Sequence note:** do the `dominic` username/email fix via `/admin/` FIRST
  (while the default path still works), then set `ADMIN_URL` and redeploy.

## Rules every new session must re-confirm

Before writing any code in a new session, re-read this file and check:
- [ ] Is the change to a service (not a view)?
- [ ] Is the business rule pinned?
- [ ] Does the block/warn/override pattern apply?
- [ ] Is there a schema change? If yes: state migration cost, blast radius, propose smallest version first.
