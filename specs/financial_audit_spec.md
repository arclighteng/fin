# Financial Audit & Transparency System

**Status:** Draft
**Priority:** P0 — Core Trust Feature
**Author:** Product/Engineering
**Date:** 2026-02-07

---

## Problem Statement

The fin app silently hides, misclassifies, and drops financial data at multiple layers of the pipeline. Users have no way to detect or correct these issues. Real examples discovered in production:

1. **$4,188/month mortgage invisible in bills** — `get_bills()` only showed "utility" and "insurance" types, silently excluding "debt_payment". The user's largest monthly obligation was completely hidden.

2. **$0 recurring spending** — `report_period()` never passed pattern data to the classifier, so every expense defaulted to `SpendingBucket.DISCRETIONARY`. The "Recurring/Fixed" total was always $0.

3. **"31 unmatched transfers" was actually 1** — The integrity widget counted raw pairing misses (31) instead of the final classified count (1). The user saw an alarming number they couldn't resolve.

4. **User overrides had no effect** — `OverrideRegistry.load_from_db()` never loaded the `txn_type_overrides` table. "Confirm Transfer" was saved but never read back.

5. **Search removed from UI** — The v2 dashboard shipped without the search bar. Users couldn't find their own transactions.

These are not edge cases. They are systemic failures across 62 identified filter/exclusion/truncation points in the codebase. The app has filters at the query layer, business logic layer, and presentation layer — and none of them are visible to the user.

**Core principle:** A personal finance app that hides data is worse than no app at all.

---

## Design Principles

1. **Nothing is silently hidden.** Every filter, threshold, and exclusion must be discoverable.
2. **Show your work.** Every classification must be explainable. Every total must be decomposable.
3. **Err toward showing too much.** False negatives (hidden data) are worse than false positives (noisy data).
4. **The user is the final authority.** The system classifies; the user decides.

---

## User Stories

### Epic 1: Financial Coverage Report

> **US-1.1** As a user, I want to see a monthly "coverage report" that tells me what percentage of my transactions are confidently classified, so I can trust the dashboard numbers.

> **US-1.2** As a user, I want to see every transaction that doesn't appear in ANY dashboard section (bills, subscriptions, categories, income), so nothing falls through the cracks.

> **US-1.3** As a user, I want to see my top 10 merchants by spend and whether each one is tracked as a bill, subscription, or one-off, so I can verify the system knows about my major expenses.

### Epic 2: Classification Transparency

> **US-2.1** As a user, I want to click any transaction and see exactly why it was classified the way it was (rule, confidence, evidence), so I understand the system's reasoning.

> **US-2.2** As a user, I want to see all transactions classified with low confidence (<0.8), so I can review and correct the uncertain ones.

> **US-2.3** As a user, I want to see when a transaction was classified differently than expected (e.g., a recurring charge marked as one-off), with an easy way to override.

### Epic 3: Recurring Charge Audit

> **US-3.1** As a user, I want to see a list of all detected recurring patterns with their classification (bill/subscription/habitual/ignored), so I can verify nothing is miscategorized.

> **US-3.2** As a user, I want to be alerted when an expected recurring charge is missing from the current month (e.g., mortgage didn't post yet), so I catch missed payments.

> **US-3.3** As a user, I want to see charges that ALMOST qualify as recurring (2 occurrences, borderline cadence) so I can manually confirm them.

### Epic 4: Filter Transparency

> **US-4.1** As a user, I want every truncated list in the UI to tell me exactly how many items are hidden and provide a way to see all of them.

> **US-4.2** As a user, I want to see a breakdown of how the system arrived at each dashboard total (income, recurring, discretionary, net), including what was excluded and why.

> **US-4.3** As a user, I want to see the system's thresholds (minimum amounts, occurrence counts, variance limits) and understand why a charge didn't qualify for a category.

---

## Functional Requirements

### Phase 1: Coverage Report (P0 — Build First)

The Coverage Report is a new page (`/audit`) that gives the user a complete picture of data health.

#### FR-1.1: Transaction Coverage Summary

Display a summary card at the top:

```
Coverage Report — February 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Transactions:    33 total
  Classified:    29 expenses, 1 income, 3 transfers
  Confidence:    27 high (>0.8), 4 medium (0.5-0.8), 2 low (<0.5)

Spending categorized:    87% ($6,432 of $7,411)
  Uncategorized ("Other"): 13% ($979) — 4 transactions [Review →]

Income classified:       100% ($4,204 of $4,204)
  Unclassified credits:  0

Recurring detection:
  Known recurring:    12 merchants ($5,926 fixed obligations)
  Borderline:         3 merchants (2 occurrences, not yet recurring) [Review →]
  Expected but missing: 0 this month
```

**Data source:** Run `report_period()` for the selected month. Aggregate by `txn_type`, `spending_bucket`, `category_id`, and `reason.confidence`.

#### FR-1.2: Top Merchants Audit Table

Show the user's top 20 merchants by total spend (across all time), with classification status:

```
Merchant               Monthly Avg    Classification      Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Truist Mortgage        $4,188.53      Bill (debt_payment)     Tracked
USAA Insurance         $1,333.13      Bill (insurance)        Tracked
Truist Loans           $774.77        Bill (debt_payment)     Tracked
H-E-B                  $412.38        Habitual (groceries)    Tracked
Pedernales Electric    $505.31        Bill (utility)          Tracked
Amazon                 $287.42        Habitual (shopping)     Tracked
Costco                 $198.54        Habitual (groceries)    Tracked
Netflix                $22.99         Subscription            Tracked
Joe's Coffee           $67.20         2 occurrences           NOT TRACKED [Add →]
New Merchant           $150.00        1 occurrence            NOT TRACKED
```

**Key insight:** Any merchant in the top 20 by spend that is NOT tracked as recurring is a potential gap. Highlight these rows.

**Data source:** Query `transactions` table grouped by `merchant_norm`, ordered by `SUM(ABS(amount_cents)) DESC`. Cross-reference with `_detect_patterns()` results and `get_bills()` + `get_subscriptions()` output.

#### FR-1.3: Ghost Transactions (Nowhere in Dashboard)

List transactions that exist in the database but don't appear in any dashboard section. A transaction is a "ghost" if it:
- Is not in the category breakdown (excluded by type filter)
- Is not in bills or subscriptions
- Is not in the income section
- Is not shown as a transfer

```
Ghost Transactions — Not shown anywhere in dashboard
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Date        Merchant              Amount     Why Hidden
2026-02-01  Venmo Transfer        -$58.00    Classified as TRANSFER (unmatched)
2026-01-15  Mystery Credit        +$200.00   CREDIT_OTHER (unclassified)
```

**Data source:** Take all transactions from `report_period()`. Subtract: category breakdown transactions + income + matched transfers + bills + subscriptions. The remainder are ghosts.

#### FR-1.4: Missing Expected Charges

Cross-reference the recurring patterns detected over the last 12 months against the current month's transactions. Flag any expected charge that hasn't appeared:

```
Expected Charges — February 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Merchant              Expected    Last Seen      Status
Truist Mortgage       ~$4,189     Feb 4, 2026    Posted
USAA Insurance        ~$1,333     Feb 3, 2026    Posted
Google Fiber          ~$72        Jan 8, 2026    MISSING (due ~Feb 8) [!!]
One Gas               ~$62        Jan 15, 2026   MISSING (due ~Feb 15)
```

**Logic:**
1. Get all merchants from `_detect_patterns()` where `is_recurring = True`
2. For each, calculate expected next date: `last_seen + median_interval_days`
3. Check if a transaction exists in the current month for that merchant
4. Flag as MISSING if expected date is past and no transaction found
5. Show grace period: don't flag until 7 days past expected date

**Data source:** `_detect_patterns(conn, lookback_days=400)` cross-referenced with current month transactions.

---

### Phase 2: Classification Transparency (P0)

#### FR-2.1: Transaction Detail Modal

When the user clicks any transaction (in any view — drilldown, search, audit), show a detail modal:

```
Transaction Detail
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Merchant:     Truist Mortgage
Description:  TRUIST MORTG OLB MTGPMT 260202 XXXXXX8918
Date:         Feb 4, 2026
Amount:       -$4,188.53
Account:      USAA Checking

Classification:
  Type:       EXPENSE (confidence: 0.80)
  Category:   Debt Payments (confidence: 0.95)
  Bucket:     Fixed Obligations
  Evidence:   "Default expense classification"

Pattern (12-month):
  Occurrences: 10
  Cadence:     Monthly (median 30 days)
  Amount CV:   0.06 (stable)
  First seen:  Apr 3, 2025
  Bills list:  Yes (debt_payment)

[Override Type ▾]  [Override Category ▾]  [Ignore]
```

**Data source:** Combine `ClassifiedTransaction` fields with `_detect_patterns()` lookup and `categorize_merchant()` result.

#### FR-2.2: Low Confidence Review Queue

New section on the audit page showing all transactions with `confidence < 0.8`:

```
Needs Review — 4 transactions with uncertain classification
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Date        Merchant          Amount     Classification    Conf.   Action
Feb 1       Mystery Credit    +$200      CREDIT_OTHER      0.50    [Income] [Transfer] [Keep]
Feb 3       ATM Withdrawal    -$100      EXPENSE           0.80    [Transfer] [Keep]
```

**Data source:** Filter `report.transactions` where `txn.reason.confidence < 0.8`.

---

### Phase 3: Recurring Charge Audit (P1)

#### FR-3.1: Full Recurring Charges View

Dedicated page (`/recurring-audit`) showing every detected pattern and its disposition:

```
All Detected Patterns — 47 merchants
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Filter: [All] [Bills] [Subscriptions] [Habitual] [Unclassified] [Ignored]

Merchant              Cadence    Amount      CV     Type          Dashboard
Truist Mortgage       Monthly    $4,189      0.06   debt_payment  Bills
Netflix               Monthly    $22.99      0.00   subscription  Subscriptions
H-E-B                 Habitual   $89.42      0.45   habitual      (not shown)  [!]
Random Service        Monthly    $15.00      0.00   subscription  Subscriptions
Dismissed Thing       Monthly    $9.99       0.00   (ignored)     (hidden)
```

The "Dashboard" column is critical — it shows WHERE (if anywhere) this pattern surfaces in the UI. Anything showing "(not shown)" is a gap.

#### FR-3.2: Borderline Patterns

Show merchants with 2 occurrences that are close to qualifying as recurring:

```
Almost Recurring — 3 merchants (2 occurrences each)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Merchant          Amounts         Interval    Likely Cadence
Joe's Coffee      $67, $72        28 days     Monthly?       [Track as Bill] [Track as Sub]
New Gym           $49, $49        31 days     Monthly?       [Track as Bill] [Track as Sub]
```

**Data source:** From `_detect_patterns()`, filter where `occurrence_count == 2` and interval suggests monthly/quarterly/annual cadence.

---

### Phase 4: Dashboard Filter Transparency (P1)

#### FR-4.1: Decomposable Totals

Every dollar amount on the dashboard should be clickable and decompose into its components. The existing drilldown system handles this, but needs enhancement:

For each drilldown, add an "Excluded" section at the bottom:

```
Spending Drilldown — $7,411.32
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Transaction list...]

Excluded from this total:
  3 transfers         $1,234.56    [View →]
  0 unclassified credits
  0 refunds applied to expenses

Why excluded: Transfers are internal money movement, not spending.
```

This already partially exists (`excluded` dict in `_drilldown_filter`). Enhance it to cover all exclusion reasons and always render the section (even when counts are 0, to confirm nothing was hidden).

#### FR-4.2: Integrity Tasks — Show All

Remove the `[:3]` truncation on integrity tasks in `dashboard_v2.html` line 943, or add a "show more" indicator:

```python
# Before
{% for task in integrity.tasks[:3] %}

# After
{% for task in integrity.tasks %}
```

Integrity issues should never be hidden — they are the user's primary trust signal.

#### FR-4.3: Threshold Disclosure

Add a `/thresholds` API endpoint that returns all system thresholds:

```json
{
  "pattern_detection": {
    "min_occurrences_subscription": 3,
    "min_occurrences_habitual": 6,
    "lookback_days": 400,
    "interval_range": [5, 400],
    "interval_tolerance": "30% or 7 days"
  },
  "classification": {
    "subscription_max_amount": 30000,
    "subscription_max_cv": 0.05,
    "utility_amount_range": [3000, 100000],
    "utility_min_cv": 0.05,
    "debt_min_amount": 50000,
    "debt_max_cv": 0.10
  },
  "display": {
    "categories_shown": 6,
    "bills_shown": 3,
    "subscriptions_shown": 4,
    "alerts_shown": 5,
    "integrity_tasks_shown": 3,
    "search_results_per_page": 8,
    "search_max_results": 50
  }
}
```

This endpoint is informational — it helps the user understand why a charge didn't qualify. Link to it from the audit page with human-readable explanations.

---

## Data Model Changes

### New table: `audit_expectations`

Stores user-confirmed recurring charge expectations for missing charge detection:

```sql
CREATE TABLE IF NOT EXISTS audit_expectations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_norm TEXT NOT NULL,
    expected_cadence TEXT NOT NULL,      -- "monthly", "quarterly", "annual"
    expected_amount_cents INTEGER,        -- NULL = any amount
    amount_tolerance_pct REAL DEFAULT 20, -- 20% tolerance
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(merchant_norm)
);
```

This allows the system to auto-populate from `_detect_patterns()` and the user to add/edit/disable expectations.

### New table: `audit_log`

Tracks all classification overrides and audit actions for accountability:

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,          -- "override", "confirm", "dismiss", "flag"
    fingerprint TEXT,
    merchant_norm TEXT,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    created_at TEXT NOT NULL
);
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/audit` | Audit dashboard page |
| GET | `/api/audit/coverage` | Coverage summary (classified %, confidence distribution) |
| GET | `/api/audit/top-merchants` | Top N merchants with classification status |
| GET | `/api/audit/ghosts` | Transactions not shown in any dashboard section |
| GET | `/api/audit/missing-charges` | Expected recurring charges not yet posted |
| GET | `/api/audit/low-confidence` | Transactions with confidence < threshold |
| GET | `/api/audit/borderline-patterns` | Merchants close to recurring detection threshold |
| GET | `/api/audit/thresholds` | System threshold values |
| GET | `/recurring-audit` | Full recurring charges audit page |
| POST | `/api/audit/expectations` | Add/update expected recurring charge |
| DELETE | `/api/audit/expectations/{merchant}` | Remove expected charge |

---

## Wireframe: Audit Page (`/audit`)

```
┌─────────────────────────────────────────────────────────┐
│  Financial Audit — February 2026            [< Prev] [Next >] │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐   │
│  │ COVERAGE     │ │ CONFIDENCE   │ │ RECURRING    │   │
│  │ 87% categ.   │ │ 27 high      │ │ 12 tracked   │   │
│  │ 100% income  │ │ 4 medium     │ │ 0 missing    │   │
│  │ 0 ghosts     │ │ 2 low        │ │ 3 borderline │   │
│  └──────────────┘ └──────────────┘ └──────────────┘   │
│                                                         │
│  TOP MERCHANTS BY SPEND                                │
│  ┌─────────────────────────────────────────────────┐   │
│  │ Merchant          Monthly   Type        Status  │   │
│  │ Truist Mortgage   $4,189   debt_payment  [ok]   │   │
│  │ USAA Insurance    $1,333   insurance     [ok]   │   │
│  │ ...                                             │   │
│  │ Joe's Coffee      $67      (2x)         [!!]   │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  NEEDS REVIEW (low confidence)                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │ Feb 1  Mystery Credit  +$200  CREDIT_OTHER 0.50 │   │
│  │        [Income] [Transfer] [Refund] [Keep]      │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  EXPECTED CHARGES                                      │
│  ┌─────────────────────────────────────────────────┐   │
│  │ Google Fiber    ~$72   due ~Feb 8   MISSING     │   │
│  │ One Gas         ~$62   due ~Feb 15  (not yet)   │   │
│  │ All others: posted                              │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  GHOST TRANSACTIONS (exist but not shown anywhere)     │
│  ┌─────────────────────────────────────────────────┐   │
│  │ (none this month)                               │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## Acceptance Criteria

### Phase 1 — Coverage Report
- [ ] `/audit` page loads with coverage summary for current month
- [ ] Top 20 merchants table shows classification status; untracked merchants highlighted
- [ ] Ghost transactions section shows any transaction not visible in dashboard
- [ ] Missing charges section shows expected recurring charges not yet posted
- [ ] All numbers on audit page are clickable and link to transaction detail
- [ ] Audit page respects account filter from dashboard

### Phase 2 — Classification Transparency
- [ ] Clicking any transaction in any view opens detail modal with full classification reasoning
- [ ] Low confidence queue shows all transactions below 0.8 confidence with inline override actions
- [ ] Override actions take effect immediately (no page reload required to see change)

### Phase 3 — Recurring Charge Audit
- [ ] `/recurring-audit` shows all detected patterns with dashboard disposition
- [ ] Borderline patterns (2 occurrences) shown with "Track" action
- [ ] User can add/remove expected charges
- [ ] Missing charge alerts appear on dashboard Data Trust widget

### Phase 4 — Filter Transparency
- [ ] All integrity tasks shown (no truncation)
- [ ] Every drilldown shows "Excluded" section even when empty
- [ ] `/api/audit/thresholds` returns all system thresholds
- [ ] Threshold explanations linked from audit page

---

## Non-Functional Requirements

- **Performance:** Audit page must load in <3 seconds. Pattern detection can be cached.
- **No data loss:** Audit features are read-only except for overrides. No audit action should delete or modify raw transaction data.
- **Mobile:** Audit page must be usable on mobile (the user accesses via phone on local network).
- **Incremental:** Each phase is independently shippable and valuable.

---

## Priority & Phasing

| Phase | Scope | Effort | Value |
|-------|-------|--------|-------|
| **Phase 1** | Coverage Report + Top Merchants + Ghosts + Missing Charges | Medium | **Highest** — catches the mortgage-class bugs |
| **Phase 2** | Transaction detail modal + Low confidence queue | Medium | High — builds user understanding |
| **Phase 3** | Recurring audit + Borderline patterns | Small | Medium — proactive detection |
| **Phase 4** | Filter transparency + Threshold disclosure | Small | Medium — systemic trust |

**Recommendation:** Ship Phase 1 first. It would have caught the mortgage bug, the spending bucket bug, and the inflated transfer count — all three critical issues discovered this session.

---

## Appendix: Known Filter Points (62 total)

Discovered during the audit that prompted this spec. Full inventory:

- **24 filters** in `legacy_classify.py` (pattern detection, bills, subscriptions)
- **3 exclusions** in `view_models.py` (category breakdown)
- **5 filters** in `reporting.py` (pending, date range, account filter)
- **8 truncations** in `web.py` (API limits, search caps)
- **9 classification paths** in `classifier.py` (confidence 0.3-1.0)
- **10 template truncations** in `dashboard_v2.html` ([:3] to [:6] slices)
- **13 threshold values** across codebase (amounts, counts, variance limits)

Each of these is a potential data hiding point. The audit system should eventually surface all of them.
