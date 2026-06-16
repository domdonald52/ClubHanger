# ClubHangar — Session Context
**Last updated:** 2026-06-06 (session 3) | **Reload this file at the start of every new session.**

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

## Rules every new session must re-confirm

Before writing any code in a new session, re-read this file and check:
- [ ] Is the change to a service (not a view)?
- [ ] Is the business rule pinned?
- [ ] Does the block/warn/override pattern apply?
- [ ] Is there a schema change? If yes: state migration cost, blast radius, propose smallest version first.
