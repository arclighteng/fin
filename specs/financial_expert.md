# Dashboard v3: Financial Planning Expert Specification

**Status:** Draft
**Author:** Financial Planning / Engineering
**Date:** 2026-02-10
**Premise:** Redesign the dashboard as though a Certified Financial Planner (CFP) is building a client-facing portal.

---

## 1. Diagnosis of v2: Why It Fails the "Financial Planner" Test

A financial planner reviewing the current dashboard would identify these problems immediately:

### 1.1 It answers the wrong question

The hero card shows **net savings** ($+1,247) and **savings rate** (18%). These are lagging indicators. A planner does not start a client meeting with "you saved $1,247." They start with: "Here is what is happening with your money right now, and here is what we should watch."

The dashboard today is a **report card**. It needs to be a **control panel**.

### 1.2 Too many CTAs, not enough insight

The user feedback is explicit: "Explain, Details, View All, Export, Excellent, Close Period" -- these are internal system operations exposed to the user. A financial planner never hands a client a to-do list of data management tasks. They hand them a clear picture and a short list of decisions.

### 1.3 Jargon without context

"Close Period," "Integrity Score," "Unmatched Transfers" -- these are developer/accountant concepts. A financial planner would never use this language with a client. The equivalent in planner-speak:
- "Close Period" = "This month is final" (and the user should not need to do this manually)
- "Integrity Score" = confidence level in the numbers (and should be invisible when healthy)
- "Unmatched Transfers" = moving money between your own accounts (and should auto-resolve)

### 1.4 No forward-looking guidance

The dashboard is 100% retrospective. A planner always includes: what is coming up, what patterns are forming, and what decisions the client should make. The current dashboard has zero prospective content.

### 1.5 Category breakdown is shallow

"Where Your Money Went" shows top-7 categories with month-over-month change percentages. This is better than nothing, but a planner would want to know: is this normal? Is this category trending up over 3+ months? Is the user aware of it? The +/- percentage against last month is noisy -- one bad month creates misleading swings.

---

## 2. The Financial Planner's Dashboard Framework

A CFP organizes a client review around these questions, in this order:

1. **Cash Flow Health** -- Are you spending less than you earn? By how much?
2. **Fixed Commitments** -- What are you locked into every month? Is that load sustainable?
3. **Spending Patterns** -- Where does the discretionary money go? What is changing?
4. **Risk Signals** -- Is anything unusual, unexpected, or escalating?
5. **Forward Look** -- What is coming next month? Are you on track?

The v3 dashboard should answer these five questions, in this order, with no extra noise.

---

## 3. Card-by-Card Specification

### 3.1 CARD 1: Cash Flow Meter (Hero, full-width)

**Purpose:** Answer "Am I spending less than I earn?" in under 2 seconds.

**What a planner shows:** A simple visual of income in, expenses out, and the gap between them. Not a number -- a *relationship*. The client needs to feel the ratio, not read it.

#### Layout

```
 CASH FLOW                                           January 2026
 ----------------------------------------------------------------
 Income          ====================================  $5,850
 Expenses        ========================              $4,603
                                          --------
 Kept                                     ========    +$1,247
                                                       21% of income
 ----------------------------------------------------------------
 3-month avg: +$1,102/mo  |  You kept $145 more than usual
```

#### Design Principles

- **Two horizontal bars** -- income (green) and expenses (combined recurring + discretionary, neutral color). The bars are proportional to each other, NOT to some abstract max. Income bar is always full-width when income > expenses; expenses bar is always full-width when expenses > income. The shorter bar makes the gap viscerally obvious.
- **The gap is the story.** The space between the bars is the savings. Color it green if positive, red if negative. Label it "Kept" (not "Net Savings" -- too clinical). If negative, label it "Over" (not "deficit" or "net loss").
- **Savings rate as a plain percentage** -- "21% of income" under the gap. No badge, no trophy, no gamification. The number should speak for itself.
- **Comparison line** -- "3-month avg: +$1,102/mo" and a plain-English delta: "You kept $145 more than usual" or "You kept $200 less than usual." This comparison is always vs. rolling 3-month average, never vs. a single prior month (too volatile).
- **Clickable bars** -- income bar opens income drilldown, expense bar opens full expense drilldown. No separate "details" or "explain" buttons.
- **Pending indicator** -- if there are pending transactions, show a small muted note: "4 pending transactions not included" -- no alarm, just transparency.
- **Mid-month pacing** -- If viewing the current month and it is not complete, show a pacing indicator: "15 days in, on track to keep ~$1,100 this month" based on daily run-rate extrapolation. This answers the question every client asks mid-month: "Am I doing okay so far?"

#### What this replaces from v2

- The hero card with its net savings number, savings rate badge, and three breakdown bars (income/recurring/other)
- The "vs 3-month avg" comparison text
- The pending count indicator

#### Data requirements (all available today)

- `current_period.income_cents`
- `current_period.recurring_cents + current_period.discretionary_cents` (total expenses)
- `current_period.net_cents`
- `savings_rate_pct`
- `avg_net_cents` (3-month rolling)
- `pending_count`
- Day-of-month for pacing calculation (trivial JS)

---

### 3.2 CARD 2: Monthly Commitments (half-width left)

**Purpose:** Answer "What am I locked into every month?"

**What a planner shows:** The "non-negotiable" costs -- subscriptions, utilities, loan payments, rent. A planner calls this the "nut" -- the minimum you must earn to break even. This number determines financial flexibility.

#### Layout

```
 MONTHLY COMMITMENTS                            $2,140/mo
 -------------------------------------------------------
 That is 37% of your income

 ProgressBar: [=========...........]  37%

 Rent/Mortgage           $1,450
 Auto Insurance            $185
 Spotify                   $10.99       was $9.99
 Netflix                   $22.99
 Electric Co               $142         avg $128
 ... (5 more)
 -------------------------------------------------------
 $412/mo in subscriptions  |  $1,728/mo in bills
```

#### Design Principles

- **The headline is the total, not the title.** "$2,140/mo" is the first thing the eye sees. This is the number a planner circles on a paper printout.
- **Percentage of income as a progress bar** -- a single horizontal bar showing what fraction of income is consumed by fixed obligations. Color-coded: green under 50%, yellow 50-70%, red over 70%. This is the core "financial flexibility" indicator. A planner uses this to assess whether the client is "house poor" or has room for goals.
- **Plain-English framing** -- "That is 37% of your income" appears above the bar. Not "Obligations Ratio" or "Fixed Cost Index." Plain English.
- **Itemized list sorted by amount** -- largest first. Each item shows the merchant name and monthly amount. Price change annotations inline ("was $9.99") -- no separate card, no separate alert. The context is right where the user can see it.
- **Variable-amount items show average** -- utilities that fluctuate show "avg $128" next to the actual charge. This teaches the user that some "fixed" costs vary and helps them understand their true baseline.
- **Footer split** -- "subscriptions" vs "bills" sub-totals at the bottom. Subscriptions are things you can cancel with a click. Bills are things you cannot easily cancel (utilities, insurance, rent). This distinction matters for financial planning: the first group is negotiable; the second is not.
- **Click any item** to drill down to its full transaction history across months.

#### What this replaces from v2

- The "Recurring" card (card 4) with its subscription/bill list
- Price change alerts from the "Needs Attention" card
- The "recurring" bar from the hero breakdown

#### Data requirements (all available today)

- `subscriptions` list (merchant, amount, cadence, display_name, etc.)
- `bills` list (merchant, amount)
- `total_recurring_cents`
- `price_changes_by_merchant`
- `current_period.income_cents` (for the ratio)

---

### 3.3 CARD 3: Spending Breakdown (half-width right)

**Purpose:** Answer "Where does my flexible spending go?"

**What a planner shows:** Discretionary spending broken down by category, compared to the client's own historical average -- not to last month, not to a budget they never set, but to what is normal *for them*.

#### Layout

```
 SPENDING                                         $2,463
 -------------------------------------------------------
 Groceries        ===============      $680     avg $640
 Dining           ========             $412     avg $310  ^^^
 Transport        ======               $295     avg $280
 Shopping         =====                $240     avg $190  ^^
 Health           ====                 $186     avg $200
 Entertainment    ===                  $165     avg $170
 Other            ===                  $148
 -------------------------------------------------------
 Total is $237 more than your 3-month average
```

#### Design Principles

- **Bar chart of categories** with proportional bars (horizontal, stacked against each other). Each bar is proportional to amount within this card -- the largest category gets the longest bar.
- **Rolling 3-month average next to each category** -- "avg $640." This is far more useful than "+8% vs last month" because it accounts for natural monthly variation. A single month of eating out more does not mean a trend; three months of eating out more does.
- **Upward arrow indicators for outliers** -- if a category is 20%+ above its 3-month average, show a subtle upward indicator (a single caret ^). If 40%+ above, two carets. If 60%+, three. These are soft nudges, not alarms. The visual language is: "This is higher than your normal." A planner would circle these items on paper and say, "Let us talk about dining."
- **No downward indicators.** Spending less than average is not noteworthy. A planner does not congratulate you for spending less on groceries this month -- you might have just not gone to the store yet. Only upward deviations are meaningful.
- **Footer summary** -- "Total is $237 more than your 3-month average" or "Total is $150 less than your 3-month average." One sentence. This answers: "Overall, am I spending more or less than normal?"
- **Click any category** to drill down to individual transactions in that category.
- **Budget targets shown only when set** -- if the user has set a budget target for a category, show a subtle dotted line on the bar indicating the target. Do not show budget UI by default. Do not prompt users to set budgets. A planner knows that most people abandon budgets; the 3-month-average approach works without requiring any setup.

#### What this replaces from v2

- The "Where Your Money Went" card (card 2)
- The month-over-month change percentages (replaced with rolling-average comparison)

#### Data requirements (all available today, with one new computation)

- `category_breakdown` (category, net_cents, count, gross_cents, refund_cents)
- `prev_category_map` (currently single-month; needs extension to 3-month average)
- Budget targets from `get_budget_targets()` (optional overlay)

**New backend work needed:** Compute 3-month rolling average per category. This requires calling `category_breakdown_from_report()` on the 3 most recent reports and averaging. The data is already loaded (`reports` list in the dashboard endpoint), so this is a ~15-line addition to the endpoint. Return as `avg_category_map: dict[str, int]`.

---

### 3.4 CARD 4: Heads Up (half-width left, conditional)

**Purpose:** Answer "Is anything unusual, unexpected, or escalating?"

**What a planner shows:** Only items that require the client's awareness or decision. Not system tasks, not data cleanup, not "close this period." A planner separates "things to know" from "things to do" -- and the dashboard should only show "things to know."

#### Layout

```
 HEADS UP
 -------------------------------------------------------
 New charge: AUDIBLE $14.99 on Jan 15
   [Looks fine]  [Flag it]

 Dining is up 33% over the last 3 months
   Your average was $310/mo, now $412.

 Your electric bill was $142 -- $14 above your $128 avg
 -------------------------------------------------------
```

#### Design Principles

- **This card only appears when there are items to show.** If everything looks normal, it does not render. Clean months earn a clean dashboard. The absence of this card IS the good news.
- **Three types of items, in priority order:**
  1. **Suspicious charges** -- new merchants with unusual amounts, first-time vendors at high dollar values. These get action buttons: "Looks fine" (dismisses permanently) and "Flag it" (marks for investigation). Auto-save on click, no save button.
  2. **Multi-month trends** -- categories trending up (or down) over 3+ consecutive months. Not single-month spikes (those are in Card 3). Example: "Dining has increased for 3 straight months: $280 -> $310 -> $412." This is the planner saying "I notice a pattern forming."
  3. **Unusual bill amounts** -- when a utility or bill deviates meaningfully from its rolling average. Example: "Electric was $142 vs avg $128." Not every $2 fluctuation -- only when the delta exceeds 15% of the average.
- **No system/integrity items here.** "Unmatched transfers," "unclassified credits," and "close period" are system housekeeping. They belong in a separate settings/maintenance view, not in the financial planning dashboard. If the data has integrity issues severe enough to affect the numbers, show a single banner at the top of the entire dashboard (see Section 4.1).
- **No "Needs Attention" count badge.** The current "(8)" count in the card header creates anxiety. A planner never says "you have 8 problems." They say "there are a couple of things I want you to see."
- **Maximum 4 items shown.** If there are more, show "2 more items" as a link that expands. Cognitive overload is the enemy of financial awareness.
- **Items are dismissible with a single click.** Dismissed items do not return. The dismiss action is an inline button, not a separate screen. Auto-saves immediately.

#### What this replaces from v2

- The "Needs Attention" card (card 3) -- stripped of system/integrity items
- Price change alerts (moved to Card 2, inline with the subscription)
- All "classify" and "resolve" action types (moved to settings)

#### Data requirements

- `alerts_with_keys` (sketchy charge detection -- already available)
- Category trend analysis (new: compare current month to each of the prior 3 months per category to detect consecutive increases)
- Bill amount deviation (already partially available via `price_changes_by_merchant`; extend to cover utility average comparison)

**New backend work needed:**
1. Multi-month category trend detection: iterate `reports[0:4]`, check if a category has increased 3 months in a row. ~20 lines.
2. Bill/utility deviation: compare current bill amount to rolling average from subscription/bill history. Much of this data is already in the subscription tuples.

---

### 3.5 CARD 5: Trend (full-width, bottom)

**Purpose:** Answer "How am I doing over time?"

**What a planner shows:** A 6-month (or available history) view of the cash flow gap -- how much the client kept (or went over) each month. This is the "trajectory" chart that tells the client whether they are improving, declining, or stable.

#### Layout

```
 YOUR TREND
 -------------------------------------------------------
 [bar chart: 6 months of "kept" amounts]

 Sep    Oct    Nov    Dec    Jan    Feb
 +980  +1,050  +1,200  +890  +1,247  (in progress)
 -------------------------------------------------------
 Average: +$1,073/mo kept over 6 months
```

#### Design Principles

- **Bar chart, not line chart.** Each month is a discrete bar. Green for positive months (kept money), red for negative months (went over). This is the same chart as v2 but with better labeling.
- **Current month marked as "in progress"** -- use a lighter shade or a dashed border to indicate that the current month is incomplete. This prevents the false impression that the current partial month represents a full month's result.
- **Footer: rolling average across visible months.** "Average: +$1,073/mo kept over 6 months." This anchors the user's sense of their baseline performance.
- **Click any bar** to navigate to that month's full dashboard view.
- **No y-axis labels needed** -- the values appear above or below each bar. The visual is about the *shape* of the trend (up, flat, down), not reading precise axis values.
- **Stacked bar variant (consider):** If the user has enough data (4+ months), consider a stacked bar where each bar shows fixed commitments (blue) + spending (purple) stacked, with the income line overlaid. This makes visible not just the gap but *what is driving changes.* However, start with the simple single-bar version; add stacking as a toggle later if the simple version feels insufficient.

#### What this replaces from v2

- The "Net Savings Trend" card (card 5) -- same concept, better labeling and interaction

#### Data requirements (all available today)

- `periods` (already passed as JSON to the chart)
- No new backend work needed

---

## 4. Cross-Cutting Concerns

### 4.1 Data Integrity Banner

When the integrity score is below the actionable threshold (< 0.8), display a non-dismissible banner at the very top of the dashboard, ABOVE all cards:

```
 Some transactions need your help to classify correctly.
 The numbers below may be incomplete. [Review items ->]
```

- Muted yellow background. Not red. Not alarming.
- Links to the resolution/integrity view (a separate page, NOT inline in the dashboard).
- This replaces all the "integrity tasks" that were cluttering the Needs Attention card.
- When integrity is healthy (>= 0.8), this banner does not render at all.

### 4.2 Period Navigation

Keep the month arrows from v2. They work. Make these refinements:

- **Remove the "Closed" badge.** The concept of closing a period is an accounting concept that does not belong on a personal finance dashboard. If the period is in the past and all transactions are settled, it is inherently "closed." Auto-close periods after 45 days (the typical bank settlement window) without user intervention.
- **Show the month name prominently** -- "January 2026" centered between the arrows.
- **Highlight the current month** -- if the user is viewing the current month, show a subtle "now" indicator so they always know they are looking at live data.

### 4.3 Search

Keep the search bar from v2. It works. Move it to a fixed position in the top navigation bar (outside the period controls) so it is always accessible regardless of which month is being viewed. Search is a utility, not a period-specific feature.

### 4.4 Account Filter

Keep the account chip toggle from v2 but move it to a filter icon/dropdown in the period controls bar. It takes up too much space as inline chips when you have 4+ accounts. The common case is "all accounts," and the filter should be tucked away until needed.

### 4.5 Drilldown System

The drilldown modal (fin_drilldown.js) is well-built and should be kept as-is with two changes:

1. **Remove the resolution/classification UI from drilldowns.** Transaction classification is a system maintenance task. When a user clicks a category or amount on the dashboard, they want to see their transactions -- not classify them. Move classification to the integrity review page.
2. **Simplify the footer.** "Filter | 47 transactions | 3 transfers excluded | Export CSV" -- this is good but "transfers excluded" is system noise. Show "47 transactions" and "Export CSV." If the user needs to understand exclusions, that belongs in the integrity/audit view.

### 4.6 Responsive Behavior

- **Desktop (>768px):** 2-column bento grid. Card 1 full-width. Cards 2+3 side by side. Card 4 side by side with empty space or spanning to fill. Card 5 full-width.
- **Mobile (<768px):** Single column, all cards stacked. Card 1 is first. Card 5 (trend) can be collapsed by default on mobile to reduce scrolling -- show just the footer average with a tap-to-expand.
- **The hero card must be fully visible without scrolling on mobile.** This means the Cash Flow Meter bars and the "kept" amount must fit within a typical mobile viewport (667px height minus nav).

---

## 5. What Gets Removed

These elements from v2 are explicitly cut:

| v2 Element | Disposition | Rationale |
|---|---|---|
| "Close Period" badge/button | Removed | Auto-close after 45 days. Not a user-facing concept. |
| "Integrity Score" | Removed from dashboard | Becomes a banner only when degraded. Healthy = invisible. |
| Integrity resolution tasks in "Needs Attention" | Moved to dedicated integrity page | System housekeeping is not financial insight. |
| Price change alerts in "Needs Attention" | Moved to inline in Card 2 | Context belongs with the item, not in a separate list. |
| "Explain" buttons | Removed | Drilldown on click is the explanation. No separate button needed. |
| "Export" button on dashboard | Removed from dashboard surface | Available inside drilldowns. Dashboard is for understanding, not data extraction. |
| "View All" links | Removed | Drilldowns handle this. The dashboard shows the useful summary. |
| Breakdown bars (income/recurring/other) in hero | Replaced with Cash Flow Meter | Two-bar visual is more intuitive than three separate bars. |
| Month-over-month % change per category | Replaced with 3-month rolling average comparison | Single-month comparisons are too volatile. |
| "Needs Attention" count badge | Removed | Anxiety-inducing. The card appears when needed; that is enough. |

---

## 6. What Gets Added

These elements are new in v3:

| v3 Element | Card | Purpose |
|---|---|---|
| Cash Flow Meter (two-bar visual) | Card 1 | Visceral income vs expenses relationship |
| "Kept" / "Over" framing | Card 1 | Plain-English net savings framing |
| Mid-month pacing indicator | Card 1 | Forward-looking: "on track to keep ~$X" |
| Fixed commitment % of income | Card 2 | Core financial flexibility metric |
| Subscription vs Bill sub-totals | Card 2 | Negotiable vs non-negotiable split |
| Bill average comparison inline | Card 2 | "avg $128" next to the actual charge |
| 3-month rolling average per category | Card 3 | Stable baseline for spending comparison |
| Outlier indicators (carets) | Card 3 | Soft nudge for above-average categories |
| Multi-month trend alerts | Card 4 | "Dining up 3 straight months" |
| Bill deviation alerts | Card 4 | "Electric $14 above average" |
| Data integrity banner (top-of-page) | Global | Non-intrusive data quality signal |
| Current-month "in progress" bar styling | Card 5 | Prevents misreading incomplete months |
| Trend footer average | Card 5 | Anchoring baseline performance number |

---

## 7. The Financial Planning Philosophy

### 7.1 Teach Without Preaching

The dashboard should make financial patterns **visible** without telling the user what to do. A planner presents facts and asks questions; they do not lecture.

- **Do:** "Dining is 33% above your average this month."
- **Do not:** "You should spend less on dining."
- **Do:** "Your fixed commitments are 37% of income."
- **Do not:** "Financial experts recommend keeping fixed costs below 50%."

The threshold-based color coding (green/yellow/red on the commitment bar) communicates the same thing without being preachy. Green under 50% means "you have flexibility." Red over 70% means "most of your income is spoken for." The user draws their own conclusions.

### 7.2 The 3-Month Average Principle

Throughout the dashboard, comparisons are against 3-month rolling averages, never against a single prior month. This is borrowed directly from financial planning practice:

- Single-month comparisons are noisy. You bought Christmas presents in December. You traveled in July. Comparing January to December is meaningless.
- 3-month averages smooth out one-time events while still being responsive to genuine behavior changes.
- If a category beats its 3-month average for 3+ months in a row, THAT is a real trend worth flagging (Card 4). A single month above average is noise.

### 7.3 The "Kept" Mental Model

The word "saved" implies money went into a savings account. "Net" is jargon. "Surplus" is clinical. "Kept" is the right word:

- "You kept $1,247 this month" = "This is money that didn't leave."
- "You went $300 over this month" = "You spent more than came in."

This framing is honest (it does not claim the money was "saved" in any goal-oriented sense) and actionable (it tells the user the direction of cash flow).

### 7.4 Separating Financial Insight from System Maintenance

The single biggest structural change from v2 to v3 is the hard separation of:

- **Financial insight** (what is happening with your money) -- belongs on the dashboard.
- **System maintenance** (classify this credit, match that transfer, close this period) -- belongs on a separate page.

The v2 dashboard conflates these. A planner never shows a client their filing system. They show the client a clean picture, with the filing done beforehand.

When the system cannot produce a clean picture (integrity < 0.8), the banner at the top says so and links to the resolution page. The dashboard is never cluttered with maintenance tasks.

---

## 8. Interaction Model

### 8.1 Click Behavior

Every number, bar, and category row on the dashboard is clickable and opens the drilldown modal showing the underlying transactions. This is already implemented in v2 and should be preserved exactly.

Clicking should feel like zooming in. The dashboard is the overview; the drilldown is the detail. No intermediate screens, no separate pages, no navigation away from the dashboard.

### 8.2 Auto-Save Everything

Per user feedback: "Who requires a Save button in 2026?" All actions are auto-saved:

- Dismissing a suspicious charge alert: immediate API call, fade out animation.
- Setting a note or tag on a transaction (in drilldown): save on blur.
- Filtering by account: URL parameter change, page reload.

There are zero save buttons anywhere in the v3 dashboard.

### 8.3 Minimal Action Vocabulary

The v3 dashboard exposes exactly three user actions:

1. **Dismiss** (for suspicious charge alerts) -- "Looks fine" or "Flag it"
2. **Navigate** (month arrows, click to drill down, click trend bar)
3. **Search** (the search bar)

Everything else (classify transactions, manage budgets, review integrity, export data) happens on dedicated pages, not on the dashboard.

---

## 9. Backend Changes Required

### 9.1 New: 3-Month Category Averages

**Endpoint change:** `dashboard()` in `web.py`

Compute average spending per category across the 3 most recent complete months. The data is already available in `reports[0:3]`. Implementation:

```python
# After existing category_breakdown computation
avg_category_map: dict[str, int] = {}
if len(reports) >= 2:
    # Use reports[0:3] (most recent 3 months including current)
    for i, r in enumerate(reports[:3]):
        for cat, net_cents, count, gross, refunds in category_breakdown_from_report(r):
            avg_category_map[cat.id] = avg_category_map.get(cat.id, 0) + net_cents
    for cat_id in avg_category_map:
        avg_category_map[cat_id] //= min(len(reports), 3)
```

Pass `avg_category_map` to the template.

### 9.2 New: Multi-Month Category Trend Detection

**Endpoint change:** `dashboard()` in `web.py`

Detect categories that have increased for 3+ consecutive months:

```python
category_trends: list[dict] = []
if len(reports) >= 4:
    for cat_id in set(avg_category_map.keys()):
        monthly_amounts = []
        for r in reports[:4]:  # Current + 3 prior
            breakdown = category_breakdown_from_report(r)
            amt = next((net for c, net, _, _, _ in breakdown if c.id == cat_id), 0)
            monthly_amounts.append(amt)
        # Check for 3+ consecutive increases (amounts are most-recent-first)
        consecutive_increases = 0
        for j in range(len(monthly_amounts) - 1):
            if monthly_amounts[j] > monthly_amounts[j + 1] * 1.10:  # 10%+ increase
                consecutive_increases += 1
            else:
                break
        if consecutive_increases >= 3:
            category_trends.append({
                "category_id": cat_id,
                "months_increasing": consecutive_increases,
                "current_cents": monthly_amounts[0],
                "baseline_cents": monthly_amounts[-1],
            })
```

Pass `category_trends` to the template for Card 4.

### 9.3 New: Bill Deviation Computation

**Endpoint change:** `dashboard()` in `web.py`

For each bill, compare the current amount to the historical average from subscription/bill data. The subscription tuples already contain `actual_cents` and the `monthly_cents` (averaged). Compute the delta and flag deviations > 15%.

This is a template-side computation -- no new backend code, just template logic comparing `bill[1]` to its average.

### 9.4 New: Mid-Month Pacing

**No backend change.** Compute in JavaScript:

```javascript
const today = new Date();
const dayOfMonth = today.getDate();
const daysInMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
const pace = (currentNet / dayOfMonth) * daysInMonth;
```

Display as "On track to keep ~$X this month" when viewing the current (incomplete) month.

### 9.5 Existing: Auto-Close Periods

Remove the "Close Period" UI entirely. If the feature is needed for internal bookkeeping, auto-close periods that are 45+ days in the past on each dashboard load. No user interaction required.

---

## 10. Visual Design Notes

### 10.1 Color Usage

| Color | Meaning | Usage |
|---|---|---|
| Green (--accent-green) | Positive cash flow / healthy | Kept amount, income bar, commitment ratio < 50% |
| Red (--accent-red) | Negative cash flow / concern | Over amount, negative months in trend, commitment ratio > 70% |
| Yellow (--accent-yellow) | Caution / watch | Commitment ratio 50-70%, suspicious charge alerts |
| Blue (--accent-blue) | Fixed / committed | Fixed commitment items, progress bar fill |
| Purple (--accent-purple) | Discretionary / variable | Spending category bars |
| Muted (--text-muted) | Supplementary info | Averages, counts, footer text |

### 10.2 Typography Hierarchy

1. **Cash Flow "Kept" amount** -- largest number on the page (3rem). This is the single most important number.
2. **Card headline numbers** -- $2,140/mo for commitments, $2,463 for spending (1.5rem). These are the card anchors.
3. **Category/item amounts** -- individual line items (14px, semibold). These are detail.
4. **Comparison/context text** -- averages, pacing, deltas (13-14px, muted). These are supporting.

### 10.3 Whitespace

The v2 dashboard is already well-spaced. Maintain the 16px card gap, 24px card padding. The only change: remove the visual clutter of multiple action buttons per card, which will naturally create more breathing room.

---

## 11. Success Metrics

How to evaluate whether v3 achieves its goals:

1. **The 5-second test.** Show the dashboard to someone for 5 seconds, then hide it. Ask: "Are you spending more or less than you earn?" If they can answer correctly, the Cash Flow Meter works.
2. **The "so what" test.** For every element on the dashboard, ask: "Does this help the user make a better financial decision?" If the answer is no, it does not belong.
3. **The information load test.** Count the number of distinct data points visible on first load (no scrolling, no clicking). v2 has approximately 35-40 (hero number, rate, comparison, 3 bars, 7 categories with amounts and percentages, 8 attention items, recurring list, trend chart). v3 should have 20-25. Less information, better understood.
4. **The action count test.** Count clickable buttons/links on the dashboard surface (not in drilldowns). v2 has 15-20+ (nav arrows, search, account filter, drilldown clicks, attention item buttons, export, etc.). v3 should have under 10: nav arrows, search, account filter dropdown, dismiss buttons on 0-4 alerts, trend bar clicks.

---

## 12. Implementation Sequence

### Phase 1: Core Layout (1-2 days)
1. New template `dashboard_v3.html` with the 5-card structure
2. Cash Flow Meter (Card 1) with two-bar visual
3. Trend chart (Card 5) with in-progress styling
4. Wire up existing drilldown system

### Phase 2: Commitment and Spending Cards (1-2 days)
1. Monthly Commitments card (Card 2) with income ratio bar
2. Spending Breakdown card (Card 3) with category bars
3. Backend: 3-month category average computation
4. Template: outlier caret indicators

### Phase 3: Intelligence Layer (1 day)
1. Heads Up card (Card 4) with alert types
2. Backend: multi-month trend detection
3. Data integrity banner (global)
4. Mid-month pacing (JS)

### Phase 4: Cleanup (1 day)
1. Move integrity/resolution tasks to dedicated page
2. Remove Close Period UI
3. Simplify drilldown footer
4. Move search to top nav
5. Account filter dropdown conversion

---

## 13. Open Questions

1. **Budget integration depth.** The app has a budget feature with per-category targets. Should Card 3 show budget progress bars alongside the averages, or should budgets remain a separate page? Recommendation: keep budgets on a separate page. The dashboard should work without the user ever setting a budget. If they have budgets set, show a subtle dotted line on the category bar -- but do not promote budget-setting from the dashboard.

2. **Multi-account view.** When viewing all accounts combined, the "income" bar includes transfers between accounts that look like income (e.g., transferring from savings to checking). The integrity system handles this, but the user may see inflated income numbers. The integrity banner (Section 4.1) covers this case, but should the Cash Flow Meter explicitly note "includes X in transfers"? Recommendation: yes, show a small footnote if transfers_in_cents > 0: "Includes $X in account transfers."

3. **Annual vs Monthly commitments.** Some subscriptions are annual (e.g., Amazon Prime). The v2 system prorates these to monthly. Should Card 2 show the prorated monthly amount, or the actual charge with a "(annual)" label? Recommendation: show the prorated monthly amount in the list, but annotate with the cadence: "$11.58/mo (billed $139 annually)." This gives the true monthly cost while being transparent about billing.

4. **The "things are fine" state.** When the user has no alerts, no outlier categories, and positive cash flow, the dashboard could feel sparse (Cards 1, 2, 3, 5 only -- Card 4 absent). Is this okay? Recommendation: yes. A clean dashboard is the reward for healthy finances. Do not fill space with congratulations or gamification. The data speaks for itself.
