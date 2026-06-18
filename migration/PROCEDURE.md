# Paper Aviator → ClubHangar Migration Procedure

## Overview

One-time migration for Wellington Aero Club. Replaces Paper Aviator (PA) with ClubHangar (CH).

**What is migrated:** members, account balances, 2+ years of flight history, instructor comments.  
**What is NOT migrated:** transaction history, aircraft setup, member credentials, cancelled bookings.  
**Cut-over timing:** last day of a calendar month, after all flying for the day is done and logged in PA.

---

## File layout

Place PA export files in this `migration/` directory before running imports:

```
migration/
  PROCEDURE.md             ← this file
  Members.csv              ← from PA: Reports → Members
  MemberBalances.csv       ← from PA: Reports → Member Balances (run on cut-over day)
  FlyingSheet_YYYY-MM.csv  ← one file per 30-day chunk (PA crashes on longer exports)
  comments/
    Smith-InstructorComments.csv
    Jones-InstructorComments.csv
    ...                    ← per-member exports from PA
```

Commit these files, push, deploy to Railway, then run the import commands via Railway shell.  
**Remove the CSV files and push again after the import is complete.**

---

## Phase 0 — CH setup (weeks before cut-over)

These must be done before any import commands are run.

### 0a. Create flight types in CH Settings

In CH → Settings → Flight Types, create an entry for each PA flight type below.
The import command matches case-insensitively. Recommended CH names and the `--flight-type-map` JSON to use:

| PA name | CH name |
|---------|---------|
| Student Dual | Student Dual |
| Solo | Solo |
| Private Hire | Private Hire |
| Private Hire - Staff (Dual) | Private Hire (Staff) |
| Staff Training Dual | Staff Training |
| Owner Dual | Owner Dual |
| Three Flight Package | Three Flight Package |
| Maintenance/Test/Ferry | Maintenance / Test / Ferry |

`--flight-type-map` argument to pass to the import command (copy-paste):

```json
{"Private Hire - Staff (Dual)":"Private Hire (Staff)","Staff Training Dual":"Staff Training","Maintenance/Test/Ferry":"Maintenance / Test / Ferry"}
```

Only non-identical names need to be in the map. Adjust if you name things differently in CH.

### 0b. Set up aircraft in CH Settings

In CH → Settings → Aircraft: add each aircraft manually.  
Source: AircraftStatus.pdf (maintenance schedules) and AircraftTechLog.pdf (current Hobbs/Tacho).

The import matches aircraft by **registration** (e.g. `ZK-ABC`). Aircraft must exist before flights can be imported.

### 0c. Export flight history from PA (do this now, not on cut-over day)

PA crashes if you export more than 30 days of FlyingSheet.csv at once.

Export in 30-day chunks going back 2 years. Name them: `FlyingSheet_2024-07.csv`, `FlyingSheet_2024-08.csv`, etc.

You said you have already done 2+ years of exports. Confirm the date range is complete.

### 0d. Export per-member InstructorComments.csv

From PA: each active member's profile → export InstructorComments. Put them all in `migration/comments/`.

### 0e. Member announcement (2 weeks before cut-over)

Email all members:

> We're switching to ClubHangar on [DATE]. Your account balance will carry across. You will receive a login invitation by email. When you first log in, please verify your credentials (medical, BFR dates) — we'll have entered what we know but ask you to confirm.

---

## Phase 1 — Cut-over day (last day of month)

### 1a. Freeze PA

Announce to committee: **PA is now read-only.** No new flights or payments to be entered.

### 1b. Export cut-over data from PA

In this order (balance must be taken after all charges for the month are posted):

1. `Members.csv` — PA → Reports → Members (all members, all statuses)
2. `MemberBalances.csv` — PA → Reports → Member Balances
3. `FlyingSheet_YYYY-MM.csv` for the current month (to catch flights not yet in your archive)

Add these to `migration/`, commit, and push:

```bash
git add migration/
git commit -m "Migration: add PA export files for cut-over [DATE]"
git push
```

Then trigger a Railway deploy (or it auto-deploys on push).

---

## Phase 2 — Import commands (Railway shell)

Open Railway shell:

```bash
railway run --service ClubHangar bash
```

Files will be at `/app/migration/` inside the container.

**Club slug:** check CH Settings — e.g. `wac`  
**Admin email:** your login email — e.g. `dom.donald@gmail.com`

---

### Step 1 — Members (dry run first)

```bash
/opt/venv/bin/python manage.py import_pa \
  --club wac \
  --members /app/migration/Members.csv \
  --dry-run
```

Check the output:
- Corporate/organisation entries: should be skipped (no first+last name)
- `non member` and `deceased` entries: should be skipped
- Count of `active` / `lapsed` / `resigned` should look right

### Step 2 — Members (live)

```bash
/opt/venv/bin/python manage.py import_pa \
  --club wac \
  --members /app/migration/Members.csv \
  --created-by dom.donald@gmail.com
```

Members are created with login disabled (`is_active=False`). Accounts are created ready for balances.

---

### Step 3 — Balances (dry run)

```bash
/opt/venv/bin/python manage.py import_pa \
  --club wac \
  --balances /app/migration/MemberBalances.csv \
  --dry-run
```

Look for `MISS` lines — these are names in MemberBalances.csv that couldn't be matched to a CH member.  
Each `MISS` needs investigation: either a name mismatch or someone who was intentionally skipped.

### Step 4 — Balances (live)

```bash
/opt/venv/bin/python manage.py import_pa \
  --club wac \
  --balances /app/migration/MemberBalances.csv \
  --created-by dom.donald@gmail.com
```

Each non-zero balance creates an `AccountTransaction` (type: adjustment) with description "Opening balance imported from Paper Aviator".

---

### Step 5 — Flights (dry run one chunk first)

```bash
/opt/venv/bin/python manage.py import_pa \
  --club wac \
  --flights /app/migration/FlyingSheet_2024-07.csv \
  --flight-type-map '{"Private Hire - Staff (Dual)":"Private Hire (Staff)","Staff Training Dual":"Staff Training","Maintenance/Test/Ferry":"Maintenance / Test / Ferry"}' \
  --dry-run
```

At the bottom of the output, check `Unmapped PA flight types`. If any appear, either:
- Add them to CH Settings → Flight Types, or
- Add them to `--flight-type-map`

before running live.

### Step 6 — Flights (live, all chunks)

```bash
/opt/venv/bin/python manage.py import_pa \
  --club wac \
  --flights \
    /app/migration/FlyingSheet_2024-07.csv \
    /app/migration/FlyingSheet_2024-08.csv \
    /app/migration/FlyingSheet_2024-09.csv \
    /app/migration/FlyingSheet_2024-10.csv \
    /app/migration/FlyingSheet_2024-11.csv \
    /app/migration/FlyingSheet_2024-12.csv \
    /app/migration/FlyingSheet_2025-01.csv \
    /app/migration/FlyingSheet_2025-02.csv \
    /app/migration/FlyingSheet_2025-03.csv \
    /app/migration/FlyingSheet_2025-04.csv \
    /app/migration/FlyingSheet_2025-05.csv \
    /app/migration/FlyingSheet_2025-06.csv \
    /app/migration/FlyingSheet_2025-07.csv \
    /app/migration/FlyingSheet_2025-08.csv \
    /app/migration/FlyingSheet_2025-09.csv \
    /app/migration/FlyingSheet_2025-10.csv \
    /app/migration/FlyingSheet_2025-11.csv \
    /app/migration/FlyingSheet_2025-12.csv \
    /app/migration/FlyingSheet_2026-01.csv \
    /app/migration/FlyingSheet_2026-02.csv \
    /app/migration/FlyingSheet_2026-03.csv \
    /app/migration/FlyingSheet_2026-04.csv \
    /app/migration/FlyingSheet_2026-05.csv \
    /app/migration/FlyingSheet_2026-06.csv \
  --flight-type-map '{"Private Hire - Staff (Dual)":"Private Hire (Staff)","Staff Training Dual":"Staff Training","Maintenance/Test/Ferry":"Maintenance / Test / Ferry"}' \
  --created-by dom.donald@gmail.com
```

**The command deduplicates by `[PA-NNN]` marker** — re-running a chunk is safe. Already-imported flights are skipped.

Add or remove date chunks to match what you actually exported. The final month's chunk is exported on cut-over day (Step 1b).

---

### Step 7 — Instructor comments (dry run)

```bash
/opt/venv/bin/python manage.py import_pa \
  --club wac \
  --comments /app/migration/comments/*.csv \
  --dry-run
```

`MISS` lines here mean a comment couldn't be matched to a flight by date + aircraft + description. Some misses are expected for old history.

### Step 8 — Instructor comments (live)

```bash
/opt/venv/bin/python manage.py import_pa \
  --club wac \
  --comments /app/migration/comments/*.csv
```

---

## Phase 3 — Post-import checks (same day)

Spot-check before sending invites:

- [ ] 5 random members: name correct, standing correct, balance correct
- [ ] 3–4 historical flights: date, aircraft reg, charge hours, charge amount
- [ ] 1 debtor (negative balance): shows correctly in CH
- [ ] 1 member with instructor comments: comment appears on flight completion record
- [ ] Aircraft Hobbs/Tacho in CH Settings are set to current values from AircraftTechLog.pdf  
  *(the import sets historical flight values but does NOT update aircraft current state)*

---

## Phase 4 — Send login invites

In CH → Members: invite all active members. Imported members are `is_active=False` — the invite email activates them on first login.

Cover in the invite email:
- Link to ClubHangar mobile app
- Ask them to verify their credentials (CAA number, BFR date, medical expiry) when they first log in

---

## Phase 5 — Manual credentials entry

For each of the ~80 active members:  
CH → Member detail → Credentials tab → enter Licence type, CAA number, BFR due, Medical class + expiry.

The Attention Items page will flag anyone with missing or lapsed credentials — use it as your priority queue.  
Ask members to verify their own data once they have access.

---

## Phase 6 — Clean up migration files

After imports are confirmed complete and correct:

```bash
git rm migration/Members.csv migration/MemberBalances.csv migration/FlyingSheet_*.csv
git rm -r migration/comments/
git commit -m "Migration: remove PA CSV exports (import complete)"
git push
```

Keep `migration/PROCEDURE.md` for the record.

---

## Phase 7 — Post-cut-over period (3 months)

- **Months 1–3:** PA stays accessible read-only. All new activity goes into CH only.
- **Bookkeeper:** confirm Xero reconciliation procedure (GL codes on charge types, export format). Meeting pending.
- **End of month 3:** formally decommission PA.

---

## Open items before cut-over

| Item | Owner | Status |
|------|-------|--------|
| Confirm flight type names match plan above or update `--flight-type-map` | Dominic | Before Phase 0a |
| Set up aircraft in CH Settings with Hobbs/Tacho from AircraftStatus.pdf | Dominic | Phase 0b |
| Confirm exact date range of FlyingSheet exports already held | Dominic | Phase 0c |
| Bookkeeper meeting: GL codes on charge types, Xero reconciliation procedure | Dominic + bookkeeper | Before cut-over |
| Decide cut-over month (July or August 2026) | Dominic | Now |
