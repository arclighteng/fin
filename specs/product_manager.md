# Dashboard v3: Product Specification

**Status**: In Development
**Audience**: Product, Engineering, Design
**Created**: February 2026
**Version**: 1.0

---

## Executive Summary

The current dashboard (v2) provides an overview of financial activity but fails to deliver the deep insight a financial planner would give to their clients. User feedback reveals three critical pain points:

1. **UI Overwhelm**: Too many calls-to-action ("Explain", "Details View All", "Export", "Excellent", "Close Period") that don't work or confuse users.
2. **Shallow Insights**: The dashboard shows numbers and charts but doesn't answer "what should I do about this?"
3. **Unclear Metrics**: Users don't understand "Integrity Score" or why they should "Close Period."

Dashboard v3 reimagines the experience as a **financial advisor dashboard**—focused on actionable insights, clear priorities, and progressive disclosure to avoid cognitive overload.

---

## Part 1: User Problems & Jobs to Be Done

### 1.1 Specific User Problems

| Problem | Evidence | Impact |
|---------|----------|--------|
| **UI Confusion** | "Explain, details view all, export, Excellent, Close Period. Too many calls to action and many of them don't work anyway." | Users click things that break expectations; abandon features. |
| **Match Unmatched Transfers** | "The UI is awful, and don't save changes—who requires a Save button in 2026?" | Transfer matching is painful; users give up on data integrity. |
| **Lack of Insight** | "The overall dashboard looks neat but doesn't give a good picture. I don't understand more about my finances with the tool." | Users leave the app without actionable understanding of their finances. |
| **Unclear Metrics** | Don't know what "Close Period" means or what "Integrity Score" measures. | Low confidence in data quality and period boundaries. |
| **No Planner Perspective** | Want to know "what a financial planner would want to see." | Dashboard optimizes for data display, not financial health. |

### 1.2 Jobs to Be Done

Users hire fin for these jobs:

1. **"Help me understand where my money goes and why"**
   - Current: Shows categories and amounts. Missing: Trends, anomalies, patterns over time.
   - v3: Add trend indicators, comparative analysis, and actionable insights.

2. **"Tell me if anything is wrong (and what to do about it)"**
   - Current: Alerts are scattered ("Needs Attention" card). Missing: Prioritization, resolution paths.
   - v3: Unified, prioritized alerts with clear next steps.

3. **"Help me track progress toward my financial goals"**
   - Current: No goal-based view. Just income/expenses.
   - v3: Budget targets vs. actual spending, savings rate trends, sustainability indicator.

4. **"Give me confidence that my financial data is accurate"**
   - Current: "Integrity Score" unexplained. "Close Period" function unclear.
   - v3: Data health summary with clear, jargon-free explanations.

5. **"Let me drill down and take action without leaving the page"**
   - Current: Drilldown modal works but return path unclear. Too many action buttons.
   - v3: Seamless drill-down with inline actions, clear navigation back.

---

## Part 2: Feature Prioritization

### 2.1 P0: Must Have (Core User Value)

**P0.1: Financial Health Overview (Simplified Hero)**
- Single, clear headline metric: **Savings vs. Baseline**
  - What you saved vs. your recurring baseline
  - Trend vs. 3-month average (up/down/stable)
- Secondary: Savings rate % (only if income > 0)
- Removes jargon: Replace "net savings" with actionable language
  - "Baseline: Your income minus fixed obligations ($X/mo)"
  - "This month: You saved $X above baseline" or "You overspent baseline by $X"

**P0.2: Actionable Alerts (Needs Attention v2)**
- Unified, **prioritized** alert list with clear severity
- Each alert shows:
  - What (price increase, unusual charge, unclassified transaction)
  - Why (anomaly detected, matches pattern, requires action)
  - Action (dismiss, classify, investigate) with single tap
- **No action buttons that don't work**
- Hide dismissed alerts by default (searchable)
- Max 5 alerts shown, expandable for "show more"

**P0.3: Spending by Category**
- Show top 5-7 categories (by amount)
- For each: Amount, trend vs. last period, trend vs. average
- Clickable to drill-down into transactions
- Remove confusing "+" and "%" symbols; use clear language
  - "Groceries +12% vs. last month" (green/red for guidance)
- Mobile-first: Vertical stack on narrow screens

**P0.4: Recurring Charges (Predictable)**
- Total monthly recurring amount
- List subscriptions/bills with current prices
- **Highlight price changes** (red for increases, green for decreases)
- Clickable to drill-down, no modal
- No "Save button" UI—auto-save any classification or note

**P0.5: Historical Trend (Last 6 Months)**
- Simple bar chart: Monthly savings (income - recurring - other)
- Color coding: Green (positive), Red (negative)
- No trend lines; clear month labels
- Hover/tap for exact amount
- Mobile: Collapse to 3-month view if space-constrained

**P0.6: Period Navigation (Simplified)**
- Left/right arrows to navigate months
- Current month labeled clearly
- Search for transactions (with results)
- Account filter toggle (if multi-account)
- Move controls to **top of dashboard** for mobile (fixed header?)

### 2.2 P1: Should Have (Enhance Value)

**P1.1: Budget Targets vs. Actual**
- Show category-level budgets (if user has set targets)
- Visual: Stacked bar or progress bar for each category
- Text: "Groceries: $500 budgeted, $487 spent (97%)"
- Red highlight if over budget

**P1.2: Sustainability Indicator**
- One-line metric: "Baseline is sustainable / at risk / unsustainable"
- Calculation: Is (income - recurring) positive? Compare to last 3 months?
- Replaces vague "Integrity Score"

**P1.3: Savings Rate Mini-Chart**
- Last 12 months savings rate % (stacked, trends)
- One sentence interpretation: "Your savings rate has been stable at ~15%"

**P1.4: Income Stability**
- Simple trend: Is income consistent month-to-month?
- Flag if income dropped >10% vs. average
- Actionable: "Income dropped in December" for planning

**P1.5: Drilldown Improvements**
- Keep modal for detailed transactions
- Add **inline classification** (auto-save, no Save button)
- Add bulk actions: "Classify all as [type]"
- Show filter/search results inline
- Escape key closes modal

### 2.3 P2: Nice to Have (Deferred)

**P2.1: Financial Planner Notes**
- Free-form text area for "what I'm working on" this period
- Shows on dashboard, editable
- Helps track progress toward goals

**P2.2: Anomaly Detection**
- ML-based: Flag transactions that are unusual
- "You usually spend $X on groceries; this month $Y (unusual)"

**P2.3: Category Benchmarks**
- Compare spending to national average or user's own average
- "Dining: $380 this month (you usually spend $320)"

**P2.4: Export & Sharing**
- Export dashboard as PDF
- Share dashboard snapshot with accountant (read-only)
- Email summary (weekly/monthly)

**P2.5: Custom Period Selection**
- Date picker to select custom range
- Show data for any date range

---

## Part 3: Interaction Design

### 3.1 Dashboard Layout (Mobile-First)

**320-375px (Mobile)**
```
┌─────────────────────────┐
│ fin | Menu              │  ← Fixed header
├─────────────────────────┤
│ < Jan 2025 >            │  ← Month nav
│ 🔍 Search transactions  │  ← Search
│ All accounts            │  ← Account filter (collapsible)
├─────────────────────────┤
│ FINANCIAL HEALTH        │
│                         │
│ You saved this month    │
│ $847                    │
│ (above your baseline)   │
│                         │
│ Baseline: $2,100/mo     │
│ (income - recurring)    │
│ vs 3-month avg: +$120   │
│                         │
│ 27% savings rate        │
└─────────────────────────┘
│ NEEDS ATTENTION (3)     │
│                         │
│ 🔴 Price increase       │
│    Netflix $15→$18      │
│    [Dismiss] [Details]  │
│                         │
│ 🟡 Unusual charge       │
│    AMAZON $847          │
│    [OK] [Investigate]   │
│                         │
│ + 1 more                │
└─────────────────────────┘
│ WHERE YOUR MONEY WENT   │
│                         │
│ 🏠 Housing        $1,800│
│    (same as avg)        │
│                         │
│ 🛒 Groceries      $480  │
│    +8% vs last month    │
│                         │
│ 🍔 Dining         $120  │
│    -15% vs last month   │
│ (more results)          │
└─────────────────────────┘
│ RECURRING CHARGES       │
│ $2,100 /month           │
│                         │
│ 🏦 Rent          $1,800 │
│ 💪 Gym             $15  │
│ 📺 Netflix         $15↑ │
│    (was $12.99)         │
│ (more results)          │
└─────────────────────────┘
│ SAVINGS TREND           │
│                         │
│ [6-month bar chart]     │
│                         │
└─────────────────────────┘
```

**768px+ (Tablet/Desktop)**
```
┌────────────────────────────────────────────────────────┐
│ fin | Menu                  < Jan 2025 >               │
│                             🔍 Search | All accounts    │
├────────────────────────────────────────────────────────┤
│ FINANCIAL HEALTH           │ WHERE YOUR MONEY WENT      │
│ You saved $847             │ 🏠 Housing        $1,800   │
│ (above baseline)           │    (same as avg)           │
│ 27% savings rate           │ 🛒 Groceries      $480     │
│ vs 3-month avg: +$120      │    +8% vs last month       │
│                            │ 🍔 Dining         $120     │
│ Baseline: $2,100/mo        │    -15% vs last month      │
│                            │ (+ 4 more)                 │
├────────────────────────────┼────────────────────────────┤
│ NEEDS ATTENTION (3)        │ RECURRING CHARGES          │
│                            │ $2,100 /month              │
│ 🔴 Price increase          │ 🏦 Rent          $1,800    │
│    Netflix $15→$18         │ 💪 Gym             $15     │
│    [Dismiss] [Details]     │ 📺 Netflix         $15↑    │
│                            │ (more)                     │
│ 🟡 Unusual charge          │                            │
│    AMAZON $847             │                            │
│    [OK] [Investigate]      │                            │
│                            │                            │
│ + 1 more                   │                            │
├────────────────────────────┴────────────────────────────┤
│ SAVINGS TREND (Last 6 Months)                          │
│                                                         │
│ [6-month bar chart spanning full width]                │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 3.2 Interaction Flows

#### Flow 1: User Wants to Understand Spending Anomaly
```
Dashboard → See "Unusual charge: AMAZON $847"
         → Click "Investigate"
         → Drilldown modal opens (full screen on mobile, modal on desktop)
         → See all AMAZON transactions in this period
         → Click one transaction → Inline annotation panel expands
         → Add note: "Birthday gift"
         → Close annotation → Back to drilldown list (no refresh)
         → Close modal (Escape or X)
         → Back to dashboard (scroll position preserved)
```

#### Flow 2: User Wants to Dismiss an Alert
```
Dashboard → See "Price increase: Netflix"
         → Click "Dismiss"
         → Toast: "Dismissed"
         → Alert item fades out
         → Button to "show dismissed" in filters
```

#### Flow 3: User Wants to See Budget Progress
```
Dashboard → Click category (e.g., "Groceries $480")
         → Drilldown: Show all grocery transactions
         → Header shows: "Groceries: $500 budgeted, $480 spent (96%)"
         → List transactions with merchant names
         → Can filter by merchant or date
         → Close drilldown → Back to dashboard
```

#### Flow 4: User Wants to Investigate Recurring Charges
```
Dashboard → Click recurring item (e.g., "Netflix $15↑")
         → Drilldown: Show all Netflix transactions
         → Highlight price increase dates
         → See payment history (Jan: $12.99, Feb: $15.00)
         → Option: "Add note" or "Investigate"
         → Close → Back to dashboard
```

### 3.3 Progressive Disclosure (What's Hidden)

**Shown by default:**
- Financial health headline + baseline
- Top 5 categories by amount
- Active alerts (severity-ordered, max 5)
- Recurring charges with monthly total
- 6-month trend chart
- Month navigation + search

**Hidden/Expandable:**
- Dismissed alerts (toggle in filter)
- "Show more" for categories/recurring/alerts
- Savings rate % (shown if income > 0, always available in details)
- Budget targets (only if user has set them)
- Rolling averages (shown in tooltip/details)
- Category breakdown by merchant (drilldown-only)

**Drilldown-only (Modal):**
- Individual transaction details
- Transaction search + filter
- Classification UI for unmatched transfers
- Bulk classification actions
- Export to CSV
- Annotation (notes + tags)

### 3.4 Mobile-First Details

**Navigation**
- Month nav stays at top (sticky on scroll)
- Tap left/right arrows to navigate
- Current month label centered

**Search**
- Input bar below month nav
- Placeholder: "Search transactions..."
- Results show in dropdown below input
- Tap result → Drilldown opens at that transaction
- Close with Escape or X

**Cards**
- Full width on mobile (16px padding)
- Stack vertically
- No 2-column layout until 768px+

**Buttons & Actions**
- Touch-friendly: 44px min height
- Max 2 buttons per row
- Stacked on mobile for space

**Drilldown Modal**
- Full screen on mobile (no padding from edges)
- Header sticky while scrolling
- Close button (X) top-right
- Transactions in scrollable list
- Expand transaction → Inline annotation panel

---

## Part 4: Technical Design

### 4.1 Data Requirements

**Backend must provide (existing):**
- Period summary: income, recurring, discretionary, net, savings_rate_pct
- 3-month rolling averages for each category
- Top 7 categories by amount with MoM change %
- Subscriptions/bills with price changes
- Alerts (sketchy charges) with severity
- Historical period data (last 6-12 months)
- Transaction count & pending count
- Account names + types

**New backend features needed:**
- Budget targets per category (if implementing P1.1)
- Sustainability calculation: (income - recurring) > 0?
- Trend computation: up/down/stable for income, recurring, discretionary
- Period "close" metadata (date closed, adjustments pending)
- Dismissal tracking for alerts (already exists in `alert_actions`)

### 4.2 API Endpoints

**Existing (keep):**
- `GET /api/drilldown?scope={scope}&start_date={date}&end_date={date}`
- `POST /api/alert-action` (dismiss/flag alerts)
- `GET /api/search?q={query}&days={days}`
- `POST /api/transaction/{fp}/note` (save transaction note)
- `POST /api/transaction/{fp}/tag` (add tag)
- `DELETE /api/transaction/{fp}/tag/{tag}` (remove tag)
- `POST /api/txn-type-override` (classify transaction)
- `POST /api/txn-type-override/bulk` (bulk classify)

**New:**
- `GET /api/dashboard/health?period={period}` (sustainability + trending)
- `GET /api/dashboard/budget-status?period={period}` (vs. targets, if P1.1)
- Optionally: `PUT /api/period/{period}/close` (close-the-books, if needed)

### 4.3 Template Changes

**Current Template:**
- `dashboard_v2.html` (1,117 lines)
- Cards: Hero, Categories, Alerts, Recurring, Trend
- Drilldown modal in separate module
- Lots of utility functions

**v3 Template:**
- Simplify hero section (remove breakdown bars)
- Merge "Needs Attention" alerts with better styling
- Keep categories/recurring/trend structure
- Enhance mobile responsiveness
- Consolidate period controls (left sticky nav)
- Add sustainability indicator
- Improve alert action UX (no modals, inline dismiss)

**Estimated size:** ~1,200-1,400 lines (slightly larger due to trend indicators, sustainability badge)

### 4.4 Frontend Changes

**fin_drilldown.js:**
- Keep existing modal flow
- Add auto-save for classification (no Save button)
- Add transaction filtering in drilldown
- Add bulk classification (already partially implemented)
- Improve keyboard navigation (Escape to close)

**New CSS needed:**
- Trend badges (up/down/stable indicators)
- Sustainability indicator styling
- Better alert severity colors
- Mobile breakpoint refinements
- Loading states for async drilldown

**JavaScript enhancements:**
- Debounce search input (existing)
- Add sticky header logic for mobile
- Improve modal close behavior
- Analytics tracking for alerts (optional, P2)

---

## Part 5: Success Metrics

### 5.1 Quantitative Metrics

| Metric | Current (v2) | Target (v3) | How Measured |
|--------|--------------|-------------|--------------|
| **Average session time on dashboard** | ? | +25% | Analytics: time_on_page |
| **Drill-down conversion rate** | ? | +40% | Clicks on cards / total views |
| **Alert action rate** | ? | +50% | Actions taken / alerts shown |
| **Return visits (weekly)** | ? | +30% | Weekly active users |
| **Dashboard load time (p95)** | ? | <2s | Performance monitoring |
| **Mobile usability score** | ? | 80+ | Lighthouse/usability testing |

### 5.2 Qualitative Metrics

| Metric | How Measured | Target |
|--------|--------------|--------|
| **User satisfaction (NPS)** | Post-launch survey | 50+ (from baseline) |
| **"Dashboard helps me understand finances"** | In-app survey (agree/disagree) | 80%+ |
| **"I know what to do when I see an alert"** | User testing (comprehension) | 90%+ |
| **"Dashboard is less confusing than v2"** | A/B testing or qualitative feedback | 75%+ prefer v3 |
| **Jargon comprehension** | User testing (can explain terms) | 85%+ |

### 5.3 Behavior Changes Desired

1. **Users act on alerts** → Dismiss or investigate with clear intent
2. **Users drill-down regularly** → Understand spending patterns
3. **Users compare periods** → Use trend chart to spot changes
4. **Users revisit dashboard** → Weekly or more often
5. **Users annotate transactions** → Add context for future reference

---

## Part 6: Open Questions & Assumptions

### Assumptions Made

1. **Users want simplicity over completeness.** → v3 defaults to essential metrics, hides complexity.
2. **Drilldown is powerful, just hidden.** → Keep modal drilldown; improve navigation.
3. **Alert fatigue is real.** → Limit to 5 alerts on dashboard; allow filtering.
4. **Mobile usage is 30-50% of traffic.** → Mobile-first design.
5. **Users don't understand "Integrity Score."** → Replace with "Data Health" and plain language.
6. **Price changes matter.** → Highlight in recurring charges card.
7. **Savings rate matters more than net savings.** → Show % when income > 0.

### Open Questions

1. **Should we show budget targets on the dashboard, or only in drill-down?**
   - P0: No. P1: Yes (if user has set them).

2. **How should we handle the "Close Period" UI?**
   - Remove from v3 if unused. Or: Add a subtle "Period closed on [date]" badge.

3. **Should we support custom date ranges (not just month navigation)?**
   - P1 or P2: Add date picker if user requests.

4. **How do we handle users with no income (expense-only accounts)?**
   - Current: Shows expense total. v3: Adjust UI to "Total spent: $X" without baseline.

5. **What's the source of budget targets?** (If implementing P1.1)
   - Manual entry? Sync from YNAB? Detect patterns?

6. **Should drilldown be modal or in-page?**
   - Keep modal for now; consider in-page slide-out in v4.

7. **How do we measure alert accuracy?**
   - Track false positive rate (dismissed as "not suspicious") vs. true positives.

---

## Part 7: Rollout & Migration Strategy

### Phase 1: Internal Testing (Week 1-2)
- Build v3 as new template alongside v2
- Feature flag: `dashboard_version=v3` in query params
- Run through user testing (5-10 users)
- Collect feedback on:
  - Is the headline metric clear?
  - Do alerts make sense?
  - Can users navigate drilldown?
  - Mobile usability OK?

### Phase 2: Beta Rollout (Week 3)
- 20% of users on v3 by default
- v2 still available via `/dashboard?version=v2`
- Collect analytics on:
  - Session time, drill-down rate, error rate
  - Crash reports (if any)
  - User feedback widget

### Phase 3: Full Rollout (Week 4)
- 100% of users on v3
- Deprecate v2 (keep available for 2 weeks if emergency needed)
- Send announcement: "Improved Dashboard"
- Include short tutorial/help text

### Phase 4: Optimization (Week 5+)
- Monitor metrics for 2 weeks
- Adjust based on usage patterns
- Plan P1/P2 features if adoption is strong

---

## Part 8: Wireframes & Visual Design

### 8.1 Financial Health Card (Hero)

**Desktop:**
```
┌─ Financial Health ────────────────────────────────────┐
│                                                        │
│ You saved this month                                  │
│ $847 above baseline                                  │
│                                                        │
│ This is +$120 compared to your 3-month average        │
│                                                        │
│ Breakdown:                                             │
│ • Baseline (income - recurring): $2,100/mo            │
│ • Savings rate this month: 27%                        │
│                                                        │
│ [Click for details →]                                 │
└────────────────────────────────────────────────────────┘
```

**Mobile:**
```
┌─ Financial Health ──────────┐
│                              │
│ You saved this month         │
│ $847                         │
│ (above baseline)             │
│                              │
│ Baseline: $2,100/mo          │
│ (income - recurring)         │
│                              │
│ vs 3-month avg: +$120        │
│ Savings rate: 27%            │
└──────────────────────────────┘
```

**Color Coding:**
- Baseline: Neutral (text-secondary)
- Savings amount: Green (if positive), Red (if negative)
- Comparison: Green (better), Red (worse)

### 8.2 Alerts Card (Needs Attention)

**Desktop:**
```
┌─ Needs Attention (3) ──────────────────────────────────┐
│                                                         │
│ 🔴 Price increase: Netflix                             │
│    Was $12.99, now $15.00 /month                       │
│    [Dismiss] [View billing history]                    │
│                                                         │
│ 🟡 Unusual charge: AMAZON                              │
│    $847.00 on 2025-01-15 (2x your typical)             │
│    [Not suspicious] [Investigate]                      │
│                                                         │
│ 🟢 [+1 more]                                           │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Mobile:**
```
┌─ Needs Attention (3) ────────┐
│                               │
│ 🔴 Price increase: Netflix    │
│    $12.99 → $15.00            │
│    [Dismiss] [View]           │
│                               │
│ 🟡 Unusual charge: AMAZON     │
│    $847 on 2025-01-15         │
│    [OK] [Investigate]         │
│                               │
│ [+1 more]                    │
└───────────────────────────────┘
```

**Severity Colors:**
- 🔴 High (bright red): Unclassified transfers, integrity issues
- 🟡 Warning (yellow): Price increases, unusual amounts
- 🟢 Info (muted): Other alerts

### 8.3 Categories Card

**Desktop:**
```
┌─ Where Your Money Went ──────────────────────────────┐
│                                                      │
│ 🏠 Housing          $1,800    (same as average)     │
│ 🛒 Groceries        $480      ↑ +8% vs last month   │
│ 🍔 Dining           $120      ↓ -15% vs last month  │
│ 🚗 Transportation   $240      (stable)              │
│ 💪 Fitness          $45       (new)                 │
│ 📱 Subscriptions    $35       (same as average)     │
│ 🎬 Entertainment    $60       ↑ +25% vs last month  │
│                                                      │
│ [+ View all categories]                             │
└──────────────────────────────────────────────────────┘
```

**Mobile:**
```
┌─ Where Your Money Went ─────────┐
│                                  │
│ 🏠 Housing      $1,800  same    │
│ 🛒 Groceries    $480    ↑ +8%   │
│ 🍔 Dining       $120    ↓ -15%  │
│ 🚗 Transport    $240    stable  │
│ 💪 Fitness      $45     new     │
│                                  │
│ [+ View all categories]         │
└──────────────────────────────────┘
```

**Interaction:**
- Click any category → Drilldown to transactions
- Hover: Show tooltip "Budgeted: $500, Actual: $480" (if P1.1)

---

## Part 9: Content & Microcopy

### 9.1 Key Phrases (Plain Language)

Replace vague jargon with clear intent:

| Current (v2) | v3 (Plain Language) |
|--------------|-------------------|
| "Net savings" | "Amount saved above baseline" |
| "Recurring" | "Monthly bills (subscriptions, rent, etc.)" |
| "Discretionary" | "Variable spending (groceries, dining, etc.)" |
| "Integrity Score" | "Data completeness: X% of transactions classified" |
| "Close Period" | "Period closed on [date]—no changes allowed" |
| "Match unmatched transfers" | "Complete your data: [N] transfers need review" |
| "Baseline" | "Your baseline: income minus recurring bills" |
| "Anomaly" | "Unusual charge: [amount] is 2x your typical spending" |

### 9.2 Explanation Text

Add inline help text for each metric:

**Financial Health:**
"Baseline = your income minus your monthly bills. This shows if you saved money above your baseline."

**Savings Rate:**
"What % of your income did you save this month? Higher is better, but depends on your goals."

**Trend Indicators:**
"↑ means spending increased vs. last month. ↓ means it decreased. = means no significant change."

**Alerts:**
"These need your attention. High priority (red) require action. Others you can dismiss if not relevant."

---

## Part 10: Appendix - Current Issues in v2

### Known Problems

1. **"Explain" button** → Does nothing visible; confusing
2. **"Details View All Export Excellent Close Period"** → Multiple action buttons that don't work or are unclear
3. **Drilldown modal** → Hard to find; unclear how to close; return to dashboard position lost
4. **"Match Unmatched Transfers" UI** → No Save button, but changes don't persist
5. **"Integrity Score" metric** → Unexplained; users don't know what it measures
6. **Mobile layout** → Period controls stack poorly; search input too small
7. **Alert fatigue** → Too many alerts; no filtering/dismiss option
8. **Jargon overload** → "Recurring", "Discretionary", "Baseline" undefined

### Root Causes

1. **Too many features crammed into dashboard** → Simplify to core insights
2. **No clear user model** → Design for "financial advisor client", not power user
3. **UI doesn't match intent** → Don't add buttons that don't work
4. **Missing progressive disclosure** → Show essentials; hide complexity
5. **Unclear information architecture** → "Needs Attention" mixes multiple concerns

---

## Part 11: Success Criteria for v3 Launch

**Dashboard v3 is successful if:**

1. Session time on dashboard increases 25% within first 2 weeks
2. Drill-down conversion rate > 40% (users click on cards to explore)
3. Alert action rate > 50% (users dismiss or investigate)
4. User feedback: 80%+ agree "Dashboard helps me understand my finances"
5. Mobile usability score (Lighthouse) > 80
6. No critical bugs reported in first week
7. NPS increases by 10+ points from v2
8. Retention (weekly active users) increases 15%+ after v3 launch

---

## Part 12: Implementation Roadmap

### Week 1: Design & Specification
- Finalize wireframes (this document)
- Get sign-off from team
- Identify dependencies (budget API, sustainability calculation, etc.)

### Week 2: Backend Preparation
- Add budget targets API (if P1.1)
- Add sustainability calculation endpoint
- Verify all existing endpoints work correctly
- Set up feature flag for v3

### Week 3: Frontend Development
- Build v3 template (simplified hero, better alerts, etc.)
- Update styles for mobile-first
- Integrate existing drilldown module
- Add trend indicators

### Week 4: Testing & Refinement
- Internal testing (5-10 users)
- Bug fixes
- Performance optimization
- Mobile testing on real devices

### Week 5: Beta Rollout
- Feature flag to 20% of users
- Monitor analytics
- Collect feedback

### Week 6: General Availability
- 100% rollout
- Post-launch support
- Begin tracking success metrics

---

## Conclusion

Dashboard v3 reimagines the financial dashboard as a **financial advisor's toolkit**—simple enough for daily use, yet powerful enough to spot problems and opportunities. By removing jargon, prioritizing alerts, and enabling drill-down exploration, we help users answer the question they really came to answer: **"Where does my money go, and is that OK?"**

The key shifts:
- **Simplify by default** → Show essentials, hide complexity
- **Prioritize action** → Clear alerts with next steps
- **Enable exploration** → Drill-down drill-down without friction
- **Plain language** → Explain jargon, not repeat it
- **Mobile-first** → Works great on 375px screens

Success is measured by increased engagement, higher confidence in financial data, and clearer understanding of financial health.

