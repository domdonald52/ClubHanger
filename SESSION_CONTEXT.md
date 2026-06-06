# ClubHanger — Session Context
**Last updated:** 2026-06-06 | **Reload this file at the start of every new session.**

---

## What this is
Django booking system for Wellington Aero Club (NZ). Replacing "Paper Aviator". Multi-club capable.
- **Stack:** Django 6.0.5, Python 3.12, SQLite (dev)
- **Run:** `cd ~/projects/aero_club && venv/bin/python manage.py runserver`
- **GitHub:** https://github.com/domdonald52/ClubHanger
- **Latest migration:** `0034_fy_start_month`

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

### Models (migration 0034)
- **ClubConfig** — compliance settings (medical intervals, BFR interval, warning days, `fy_start_month`)
- **Aircraft** — `is_leased` boolean, `aircraft_type` FK(AircraftType), `records_hobbs/tacho/airswitch`
- **AircraftType** — club-scoped managed list
- **MemberCredential** — `aircraft_type` FK nullable (for type ratings)
- **FlightCompletion** — `amount_paid`, `balance_owing` property, `is_paid`, `is_partially_paid`
- **Invoice** — draft→sent→paid/void lifecycle
- **DepartureDeclaration** — model exists, standalone UI not yet built

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
- Stacked bar chart (Chart.js 4 CDN): flight hours by aircraft, grouped by month, for current financial year
- All/Owned/Leased toggle
- FY determined by `ClubConfig.fy_start_month` (default April=4, NZ)

### Other views
- Member profile: details, account, bookings, upcoming instruction schedule (7 days)
- Aircraft detail: rates, surcharges, maintenance (progress bars, urgency), block-outs, history
- Instructor detail: availability windows, block-outs, upcoming bookings
- Manage: Bookings, Block-outs, Members, Aircraft, Instructors, Aerodromes, Payments, Invoices
- Club settings: General, Appearance, Scheduling, Charge types, Block-out types, Aircraft types, Billing (with FY start)

---

## Known gaps vs the brief

### Still in views, not services (should migrate eventually)
- `booking_detail` POST handler still contains inline depart/checkin logic (calls services but also has direct model manipulation for checkin)
- `manage_aircraft_detail` POST handler — add/edit rates, maintenance items
- No `maintenance_service.py` yet

### Not yet built (from backlog)
- **DepartureDeclaration UI** — model exists, no standalone form. Currently soft-blocked at check-out.
- **Manage > Exceptions screen** — one triage view: booking conflicts + unpaid flights + lapsed members with bookings + amber/red maintenance. Rename "Conflicts" tab.
- **Reporting:** FlyingBudget model (aircraft + month + budgeted_hours), budget vs actual, instructor hours, 4-year historical chart
- **Member profile fields:** date of birth (exists on ClubMember?), next of kin (on ClubMember), frequent passengers (FrequentPassenger model — exists but no UI)
- **SAR time** on declaration
- **Slot release notifications** — opt-in by aircraft/instructor/period
- **Incident/Occurrence log** — standalone module, feeds committee paper Appendix A
- **Hard/soft block-out enforcement** — `BlockOutType.is_hard` exists; soft block UI/override flow not built
- **Meeting rooms** — DO NOT BUILD until Part 2A decided

---

## Rules every new session must re-confirm

Before writing any code in a new session, re-read this file and check:
- [ ] Is the change to a service (not a view)?
- [ ] Is the business rule pinned?
- [ ] Does the block/warn/override pattern apply?
- [ ] Is there a schema change? If yes: state migration cost, blast radius, propose smallest version first.
