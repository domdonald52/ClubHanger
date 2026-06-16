# ClubHangar UI Test Plan

Tests live in `ui_smoke_test.py`. Run with:
```
venv/bin/python ui_smoke_test.py                      # all categories
venv/bin/python ui_smoke_test.py --category booking   # one category
venv/bin/python ui_smoke_test.py --headed             # show browser
```

Test data is injected before each run and cleaned up in a `finally` block — safe to run any time against the live dev DB.

Status key: ✓ implemented · ○ planned · – skipped (backlog dependency noted)

---

## 1. Club Setup

| Status | Test |
|--------|------|
| ✓ | Settings page loads, tabs visible |
| ✓ | Members list loads |
| ✓ | Aircraft list loads |
| ✓ | Instructors list loads |
| ✓ | Charge rates page loads |
| ✓ | Aerodromes page loads |
| ✓ | Blockouts page loads |
| ✓ | Vouchers page loads |
| ✓ | Instructor detail loads |
| ✓ | Aircraft detail loads |
| ✓ | Aircraft detail — hire/rates section present |
| ○ | New member created → appears in member list |
| ○ | New aircraft created → appears in aircraft list |
| ○ | Instructor added to roster → visible in gantt |
| ○ | Charge rate configured → persisted in rate list |
| ○ | Operating hours saved → reflected in availability search |

---

## 2. Core Booking Workflow

### 2a. Simple lifecycle — one booking from each state

| Status | Test |
|--------|------|
| ✓ | Calendar (gantt) loads |
| ✓ | Manage bookings — active view shows active/past sections |
| ✓ | Manage bookings — conflicts and all-history views load |
| ✓ | Pending page loads |
| ✓ | Confirmed page loads |
| ✓ | Departed page loads |
| ✓ | Completed page loads |
| ✓ | Pending — confirm action visible, no check-in or payment |
| ✓ | Confirmed — check-out visible, no check-in or payment |
| ✓ | Departed — check-in form visible, no payment or charges |
| ✓ | Departed — meter reading inputs present |
| ✓ | Departed — admin undo departure option visible |
| ✓ | Cancelled — status shown, no action buttons |
| ○ | Pending → confirmed (submit, status updates) |
| ○ | Confirmed → departed (confirm dialog, status updates) |
| ○ | Departed → completed (fill meters, charges screen opens) |
| ○ | Completed → paid (fill payment, paid badge appears) |
| ○ | Manage bookings — departed booking appears in active section |
| ○ | Manage bookings — paid booking moves to past section |

### 2b. Declaration required

| Status | Test |
|--------|------|
| ✓ | Confirmed booking with declaration-required flight type — declaration section visible |
| ○ | Declaration submitted → ✓ Submitted shown, check-out proceeds |
| ○ | Stale declaration (>6h) → amber warning shown |
| ○ | Check out without declaration → override reason required |
| ○ | Override reason provided → departed_without_declaration recorded |

### 2c. Booking cancellation

| Status | Test |
|--------|------|
| ✓ | Cancelled booking shows cancelled status, no action buttons |
| ○ | Cancel confirmed booking → cancel dialog appears, reason required |
| ○ | Cancelled booking disappears from active view |
| ○ | Cancel departed booking → blocked (backlog #B4) |

### 2d. Availability search

| Status | Test |
|--------|------|
| ✓ | Availability search loads |
| ✓ | No out-of-hours greyed spans in results |
| ○ | Results respect block-outs (gaps in free spans where block-outs exist) |
| ○ | Solo search shows aircraft-only rows |
| ○ | Dual search shows aircraft + instructor rows |

---

## 3. Charges & Payment

### 3a. Single payment

| Status | Test |
|--------|------|
| ✓ | Completed booking — charges table and two-column layout present |
| ✓ | Completed booking — payment panel visible |
| ✓ | Admin — "Edit check-in details" visible on completed booking |
| ✓ | Paid booking — paid badge visible, no payment form |
| ✓ | Unpaid booking — payment form visible |
| ✓ | Payment method dropdown has EFTPOS + account credit options |
| ○ | EFTPOS payment submitted → paid badge appears |
| ○ | Account credit payment → account balance decreases |
| ○ | Credit limit exceeded → error shown, payment blocked |

### 3b. Split payment (one booking, multiple payees)

| Status | Test |
|--------|------|
| ✓ | Charges screen shows "Add payee" when unpaid |
| ○ | Add second payee → two rows appear |
| ○ | Record first payee → row shows ✓ Paid, second remains unpaid |
| ○ | Record second payee → "Fully paid" shown |
| ○ | Reverse one payee → flight returns to partially paid |

### 3c. Multi-flight payment (outstanding from other flights)

| Status | Test |
|--------|------|
| ✓ | Completed unpaid booking — payment form visible |
| ○ | Member with other outstanding flights — payment rows show other flights |
| ○ | Uncheck other flight → total reduces |
| ○ | Record combined payment → each booking gets own FlightPayment |
| ○ | Amount received < total → current flight paid first |

### 3d. Partial payment and reversal

| Status | Test |
|--------|------|
| ○ | Partial payment → balance_owing reduces, form reappears |
| ○ | Payment reversed → paid badge disappears, form reappears |
| ○ | Overpayment → warning shown with refund option |
| ○ | Refund to account → account balance increases |

---

## 4. Aircraft Maintenance

| Status | Test |
|--------|------|
| ✓ | Aircraft detail (with maintenance data) loads |
| ✓ | Maintenance content visible on aircraft detail |
| ✓ | Maintenance log section present in page |
| – | Maintenance urgency thresholds visible — backlog #5, #7 |
| – | Urgency recalc called after check-in — backlog #6 |
| ○ | Check-in with Hobbs readings → MaintenanceLogEntry created |
| ○ | Cumulative hours updated on aircraft after check-in |
| ○ | Maintenance item shown as overdue when threshold passed |

---

## 5. Member Detail (Admin View)

| Status | Test |
|--------|------|
| ✓ | Member detail loads, tab bar present |
| ✓ | Standing badge visible |
| ✓ | Negative account balance shown in red |
| ✓ | Credentials section visible |
| ✓ | Booking history section visible |
| ✓ | Members list shows all members |
| ○ | Suspended member — standing badge shows "Suspended" |
| ○ | Expired subscription — warning shown |
| ○ | Credential expired — shown in red |
| ○ | Credential expiring soon — shown in amber |
| ○ | Admin top-up account → balance increases |
| ○ | Admin deduct account → balance decreases |
| ○ | Instructor credentials visible on instructor member detail |

---

## 6. Profile (Member Self-Service)

| Status | Test |
|--------|------|
| ✓ | Profile loads, tab bar present |
| ✓ | Account balance section visible |
| ✓ | Upcoming bookings section present |
| ✓ | Payment history section present |
| ✓ | Notification preferences section present |
| ○ | Upcoming booking shown with correct status chip |
| ○ | Declaration button shown for confirmed booking requiring declaration |
| ○ | Instructor schedule section shown for instructor members |
| ○ | Notification pref saved → preference persists |

---

## 7. Notifications

| Status | Test |
|--------|------|
| ✓ | Notifications page loads |
| ✓ | Unread and All tabs present |
| ✓ | List or empty state shown correctly |
| ✓ | Bell icon present in nav |
| ○ | Unread count badge shown when notifications exist |
| ○ | Mark read → notification moves from Unread to All tab |
| ○ | Delete notification → removed from list |
| ○ | "All caught up" shown when unread tab is empty |

---

## Future Categories (planned)

### 8. Membership & Compliance
| Status | Test |
|--------|------|
| ○ | Booking detail — expired subscription compliance block shown |
| ○ | Booking detail — lapsed member compliance warning shown |
| ○ | Check out with compliance block → override reason required |
| ○ | Annual renewal cycle — backlog #29 |

### 9. Blockouts & Availability
| Status | Test |
|--------|------|
| ○ | Block-out visible as shaded band on gantt |
| ○ | Hard block-out → booking blocked — backlog #24 |
| ○ | Soft block-out → booking proceeds with warning — backlog #24 |
| ○ | Availability search excludes block-out periods |

### 10. Split Flight (Scenario B)
One booking, two pilots, separate Hobbs segments, separate charges and payment.
Not yet built — backlog #30 Scenario B.
