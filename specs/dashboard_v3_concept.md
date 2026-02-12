# Dashboard v3: Unified Concept Design

**Synthesized from:** Business Analyst, Product Manager, Financial Expert specs
**Status:** Concept for review - no code written

---

## Design Philosophy

**One sentence:** A financial planner's client summary -- not a transaction report.

**Core principles (all three experts agree):**
- Answer "Am I okay?" in under 5 seconds
- 3-month rolling averages for all comparisons (single-month is too volatile)
- Auto-save everything, zero save buttons
- Show financial insight on dashboard, move system maintenance elsewhere
- Conditional rendering -- if there's nothing to show, don't show a card
- Progressive disclosure: glance > scan > drilldown

**Framing language:**
- "Kept" not "saved" or "net savings" (FE rationale: "saved" implies a savings account; "kept" means "didn't leave")
- "Over" not "deficit" or "negative"
- "Heads Up" not "Needs Attention" (no anxiety-inducing count badges)
- Plain English throughout, no jargon

---

## Layout: 5-Card Bento Grid

```
MOBILE (single column)          DESKTOP (2-col bento)
+-------------------------+     +--------------------------------------------------+
| [< January 2026 >]     |     | [< January 2026 >]  [filter v]  [search______]  |
| [filter v] [search____] |     +--------------------------------------------------+
+-------------------------+     |                                                  |
|                         |     |              CARD 1: CASH FLOW                   |
|   CARD 1: CASH FLOW    |     |              (full width hero)                   |
|   (hero, full width)    |     |                                                  |
|                         |     +------------------------+-------------------------+
+-------------------------+     |                        |                         |
|                         |     | CARD 2: COMMITMENTS    | CARD 3: SPENDING        |
| CARD 2: COMMITMENTS    |     | (half width)           | (half width)            |
|                         |     |                        |                         |
+-------------------------+     +------------------------+-------------------------+
|                         |     |                                                  |
| CARD 3: SPENDING        |     | CARD 4: HEADS UP (conditional, full or half)    |
|                         |     |                                                  |
+-------------------------+     +--------------------------------------------------+
|                         |     |                                                  |
| CARD 4: HEADS UP        |     |              CARD 5: YOUR TREND                 |
| (only if items exist)   |     |              (full width)                       |
|                         |     |                                                  |
+-------------------------+     +--------------------------------------------------+
|                         |
| CARD 5: YOUR TREND      |
|                         |
+-------------------------+
```

---

## Period Controls (top bar)

```
+--------------------------------------------------------------+
|  <  January 2026  >     [Accounts v]     [Search...        ] |
+--------------------------------------------------------------+
```

- Month name centered between arrow buttons
- "now" dot or label when viewing current month
- Account filter collapsed into dropdown (not inline chips)
- Search bar right-aligned, always visible
- Sticky on mobile scroll

---

## CARD 1: Cash Flow (Hero)

The Financial Expert's "Cash Flow Meter" -- two proportional bars showing the relationship between income and expenses. This is the most distinctive element of v3.

```
+--------------------------------------------------------------+
|  CASH FLOW                                    January 2026   |
|                                                              |
|  Income     ==========================================  $5,850|
|  Expenses   ==============================              $4,603|
|                                            ~~~~~~~~~~~~      |
|                                                              |
|              Kept $1,247                                     |
|              21% of income                                   |
|                                                              |
|  3-month avg: +$1,102/mo   You kept $145 more than usual    |
|                                                              |
|  4 pending transactions not included                         |
+--------------------------------------------------------------+
```

**Key design decisions:**
- Two horizontal bars, proportional to each other (income always full-width when > expenses)
- The GAP between bars is the story -- green if positive ("Kept"), red if negative ("Over")
- "Kept $1,247" is the largest number on the page (3rem)
- Savings rate as plain text: "21% of income"
- Comparison always vs 3-month rolling average (never single prior month)
- Pending count as muted footnote (not alarming)
- Click income bar -> income drilldown; click expense bar -> expense drilldown

**Mid-month pacing (current month only):**
```
  15 days in -- on track to keep ~$1,100 this month
```
Computed client-side from daily run-rate extrapolation.

**Negative month variant:**
```
  Income     ========================                     $3,200
  Expenses   ==========================================   $3,850
                                       ~~~~~~~~~~~~~~~~
             Over $650
             You spent more than you earned
```

---

## CARD 2: Monthly Commitments (half-width left)

Fixed obligations -- the "nut" a financial planner circles on paper.

```
+------------------------------------+
|  MONTHLY COMMITMENTS     $2,140/mo |
|                                    |
|  That's 37% of your income         |
|  [=========..........]  37%       |
|                                    |
|  Rent/Mortgage         $1,450     |
|  Auto Insurance          $185     |
|  Netflix             $22.99       |
|    was $19.99                     |
|  Spotify             $10.99       |
|  Electric Co           $142       |
|    avg $128                       |
|  ... (5 more)                     |
|                                    |
|  $413/mo subs  |  $1,727/mo bills |
+------------------------------------+
```

**Key design decisions:**
- Headline is the TOTAL, not the card title -- "$2,140/mo" is first thing the eye sees
- Progress bar: % of income committed (green <50%, yellow 50-70%, red >70%)
- Items sorted by amount, largest first
- Price changes INLINE ("was $19.99") -- not in a separate alerts card
- Variable bills show rolling average ("avg $128")
- Footer splits subscriptions (cancellable) vs bills (not easily cancelled)
- Click any item -> drilldown to that merchant's transaction history

---

## CARD 3: Spending Breakdown (half-width right)

Discretionary spending with 3-month rolling average comparison.

```
+------------------------------------+
|  SPENDING                   $2,463 |
|                                    |
|  Groceries   ========  $680       |
|              avg $640              |
|                                    |
|  Dining      =====    $412   ^^^ |
|              avg $310              |
|                                    |
|  Transport   ====     $295        |
|              avg $280              |
|                                    |
|  Shopping    ===      $240    ^^  |
|              avg $190              |
|                                    |
|  Health      ===      $186        |
|              avg $200              |
|                                    |
|  Entertain.  ==       $165        |
|              avg $170              |
|                                    |
|  Other       ==       $148        |
|                                    |
|  Total is $237 more than your     |
|  3-month average                  |
+------------------------------------+
```

**Key design decisions:**
- Horizontal bars proportional within card (largest = longest bar)
- 3-month rolling average shown next to each category ("avg $640")
- Outlier carets for categories ABOVE average:
  - `^` = 20-39% above average
  - `^^` = 40-59% above average
  - `^^^` = 60%+ above average
- NO downward indicators (spending less than average isn't noteworthy)
- Footer: one-sentence total comparison to 3-month average
- Click any category -> drilldown to transactions in that category
- Budget target shown as subtle dotted line on bar ONLY if user has set a budget

---

## CARD 4: Heads Up (conditional)

**This card only renders when there are items to show.** Empty months = clean dashboard.

```
+--------------------------------------------------------------+
|  HEADS UP                                                    |
|                                                              |
|  New charge: AUDIBLE $14.99 on Jan 15                        |
|  [Looks fine]  [Flag it]                                     |
|                                                              |
|  Dining is up 33% over the last 3 months                    |
|  Your average was $310/mo, now $412.                         |
|                                                              |
|  Your electric bill was $142 -- $14 above avg                |
|                                                              |
|                                          2 more items >      |
+--------------------------------------------------------------+
```

**Three item types (in priority order):**
1. **Suspicious charges** -- new/unusual merchants. Action: "Looks fine" / "Flag it" (auto-save, fade out)
2. **Multi-month trends** -- categories increasing 3+ consecutive months (not single-month spikes)
3. **Unusual bill amounts** -- bills deviating >15% from rolling average

**What is NOT in this card:**
- Unclassified credits (system maintenance -> separate page)
- Unmatched transfers (system maintenance -> separate page)
- Price changes (moved inline to Card 2)
- Any count badge in the header

**Max 4 items shown.** If more, "N more items" link expands.

**Data integrity banner (separate from this card):**
When integrity score < 0.8, show a banner ABOVE all cards:
```
+--------------------------------------------------------------+
|  Some transactions need classification.                       |
|  The numbers below may be incomplete.  [Review items ->]     |
+--------------------------------------------------------------+
```
Muted yellow, not red. Links to resolution page. Invisible when healthy.

---

## CARD 5: Your Trend (full-width bottom)

```
+--------------------------------------------------------------+
|  YOUR TREND                                                  |
|                                                              |
|     +$980   +$1,050  +$1,200   +$890   +$1,247  (+$600)    |
|     [====]  [=====]  [======]  [====]  [======]  [====]     |
|     Sep      Oct      Nov       Dec     Jan      Feb        |
|                                                   ^^^^      |
|                                                  in progress |
|                                                              |
|  Average: +$1,073/mo kept over 6 months                     |
+--------------------------------------------------------------+
```

**Key design decisions:**
- Bar chart, not line chart (each month is discrete)
- Green bars for positive ("kept"), red for negative ("over")
- Current month: lighter shade or dashed border with "in progress" label
- Values above each bar (no y-axis labels needed)
- Footer: rolling average across visible months
- Click any bar -> navigate to that month's dashboard
- Mobile: collapse to 3 months with expand option

---

## Interactions

**Three user actions on the dashboard:**
1. **Navigate** -- month arrows, click bars/categories/items to drilldown, click trend bars
2. **Dismiss** -- "Looks fine" / "Flag it" on suspicious charges (auto-save, fade out)
3. **Search** -- always-available search bar

**Everything else happens elsewhere:**
- Classification -> drilldown modal (auto-save on select change, no Save button)
- Transfer matching -> integrity review page
- Budget management -> budget page
- Export -> drilldown footer only
- Close Period -> removed entirely (auto-close after 45 days)

---

## What's Removed from v2

| Element | Disposition |
|---------|------------|
| Close Period button/badge | Removed entirely |
| Integrity Score / Data Trust card | Banner only when degraded (<0.8) |
| Audit card (tracked/pending/review) | Removed |
| "Explain" links | Removed (drilldown IS the explanation) |
| "View All" / "+ N more" links to /subs | Removed |
| Export buttons on cards | Removed from dashboard (kept in drilldown) |
| Income breakdown section | Removed |
| Cross-account duplicates warning | Removed |
| Duplicate subscription warnings | Removed |
| Save button in drilldown | Already removed (auto-save) |
| Month-over-month % per category | Replaced with 3-month avg comparison |
| Count badge on alerts | Removed (anxiety-inducing) |
| Integrity tasks in alerts card | Moved to separate page |
| Price change alerts in alerts card | Moved inline to Card 2 |

---

## What's New in v3

| Element | Card | Purpose |
|---------|------|---------|
| Cash Flow Meter (two-bar visual) | 1 | Visceral income vs expenses relationship |
| "Kept" / "Over" framing | 1 | Plain-English cash flow framing |
| Mid-month pacing | 1 | "On track to keep ~$X" |
| Commitment % of income bar | 2 | Financial flexibility indicator |
| Subs vs Bills split | 2 | Negotiable vs non-negotiable |
| Bill average inline | 2 | "avg $128" next to actual charge |
| 3-month rolling avg per category | 3 | Stable spending baseline |
| Outlier carets (^, ^^, ^^^) | 3 | Soft nudge for above-average categories |
| Multi-month trend alerts | 4 | "Dining up 3 straight months" |
| Bill deviation alerts | 4 | "Electric $14 above average" |
| Conditional card rendering | 4 | Clean dashboard when nothing unusual |
| Integrity banner (top-of-page) | Global | Non-intrusive data quality signal |
| "In progress" bar styling | 5 | Prevents misreading incomplete months |
| Trend footer average | 5 | Anchoring baseline performance |

---

## Backend Changes Required

1. **3-month category averages** (~15 lines in web.py)
   - Compute avg spending per category from `reports[0:3]`
   - Pass `avg_category_map: dict[str, int]` to template

2. **Multi-month category trend detection** (~20 lines in web.py)
   - Check if any category has increased 3+ consecutive months
   - Pass `category_trends: list[dict]` to template for Card 4

3. **Restructure attention_items** (modify existing code)
   - Remove integrity tasks from attention_items (separate page)
   - Remove price changes from attention_items (inline in Card 2)
   - Add bill deviation items
   - Add category trend items

4. **Mid-month pacing** (JavaScript only, no backend)

5. **No new API endpoints needed** -- all computed in existing dashboard endpoint

---

## Open Questions for Review

1. **Classification in drilldown**: FE wants to remove classification entirely from drilldowns and move it to a separate page. BA/PM want to keep it in drilldowns (with auto-save). **My recommendation:** Keep in drilldowns. The auto-save is already working and users need to be able to classify while exploring. The integrity banner handles the "go fix things" flow.

2. **Budget integration**: All three agree budgets should NOT be prominent. FE says show a subtle dotted line on category bars only if budget exists. BA wants budget % in category details. **My recommendation:** Subtle dotted marker on the bar if budget is set, nothing if not. No budget prompts.

3. **Health score label**: BA wants "Strong/On Track/Needs Attention/At Risk" labels. FE says the numbers speak for themselves -- no labels needed. **My recommendation:** No labels. The green/red color of "Kept/Over" and the comparison text communicate health without gamification.

4. **Account filter**: FE wants it in a dropdown. Current v2 has inline chips. **My recommendation:** Dropdown on mobile, inline chips on desktop (if 4 or fewer accounts). Dropdown always if 5+ accounts.

5. **Trend chart type**: BA suggests savings rate % toggle. FE says just show "kept" amounts as bars. **My recommendation:** Simple kept-amount bars. One chart, no toggles, no complexity.
