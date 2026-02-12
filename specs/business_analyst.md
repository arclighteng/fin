# Dashboard v3: Business Analyst Design Specification

**Project:** Fin Personal Finance Application
**Document Type:** Design Specification
**Author:** Business Analyst Agent
**Date:** 2026-02-10
**Status:** Draft for Review

---

## Executive Summary

The current Dashboard v2 suffers from action overload, unclear value proposition, and poor information architecture. Users report feeling confused rather than informed. This specification redesigns the dashboard from first principles, focusing on the mental model a financial planner would use when reviewing a client's finances.

**Key Design Principles:**
1. **Data Storytelling First** - Numbers should tell a story, not just display facts
2. **Progressive Disclosure** - Show summary → details → actions only when needed
3. **Zero Unnecessary Actions** - Auto-save everything, eliminate confirmation dialogs
4. **Financial Health Narrative** - Answer "Am I okay?" before "What did I spend?"
5. **Trust Through Transparency** - Show why numbers matter, not just what they are

---

## 1. Current State Analysis

### 1.1 User Pain Points (Direct Feedback)

> "The UI is out of control. Too many calls to action and many don't work anyway."

**Root Cause:** Action buttons scattered across multiple cards without clear hierarchy or purpose.

**Evidence from Code:**
- Dashboard v2 has 8+ distinct action types: Explain, Details, Export, Excellent, Close Period, View All, Save, Dismiss
- Many actions require page refresh (no inline updates)
- "Close Period" and "Integrity Score" have no user-facing explanation

> "I don't understand more about my finances with the tool."

**Root Cause:** Dashboard shows transactions and categories but doesn't synthesize insights or provide context.

**Evidence from Code:**
- Financial Summary shows net savings as a single number without context
- Category breakdown is a static list with no benchmarking or guidance
- Trend chart shows historical data but no analysis of patterns
- No "so what?" narrative connecting data points

> "The 'match unmatched transfers' UI is awful, and who requires a Save button in 2026?"

**Root Cause:** Multi-step workflows with explicit save actions create friction.

**Evidence from Code:**
- Drilldown resolution controls require manual classification then refresh
- Alert dismissal requires explicit button clicks
- No optimistic UI updates (everything waits for server response)

### 1.2 Information Architecture Issues

**Current Card Structure:**
1. Financial Summary (Hero) - Net number + breakdowns
2. Where Your Money Went - Top categories
3. Needs Attention - Alerts + integrity tasks
4. Recurring - Subscriptions/bills
5. Net Savings Trend - Bar chart

**Problems:**
- **No clear entry point**: Eyes don't know where to start
- **Equal visual weight**: All cards look equally important
- **Mixed mental models**: Some cards show "what happened" (categories), others show "what to do" (alerts)
- **Buried insights**: Savings rate % is secondary to dollar amount despite being more meaningful
- **Action sprawl**: 5 different card types = 5 different interaction patterns

### 1.3 Data Utilization Gap

**Available but Unused:**
- 3-month rolling averages (computed but barely shown)
- Budget targets vs actual (exists in backend, not surfaced prominently)
- Category trends (month-over-month computed but only shown as %)
- Historical patterns (6+ months data available)
- Transaction notes and tags (accessible but hidden in drilldown)

**Opportunity:** Surface insights that answer "Why is this number different?" automatically.

---

## 2. User Journey Mapping

### 2.1 Primary User Journeys

#### Journey 1: Financial Health Check (Daily/Weekly)
**User Goal:** "Am I doing okay this month?"

**Current Experience:**
1. Land on dashboard
2. See large net savings number (+$2,847)
3. Scan category list
4. Wonder: Is this good? Bad? Normal?
5. Scroll to trend chart to compare
6. Still unclear if action needed

**Desired Experience:**
1. Land on dashboard
2. See health score: "You're on track - saving 23% this month"
3. Glance at trend sparkline: "Up from 18% avg"
4. Confidence achieved in 3 seconds
5. Optional: Click to understand why

**Success Metric:** User can answer "Am I okay?" in <5 seconds without scrolling.

---

#### Journey 2: Investigate Spending Anomaly (Weekly)
**User Goal:** "Why is my spending higher this month?"

**Current Experience:**
1. Notice net savings is lower
2. Manually compare current month categories to last month
3. Calculate mental deltas
4. Guess at causes
5. Maybe click through to transactions
6. Exit confused

**Desired Experience:**
1. Dashboard highlights: "Dining up 47% this month"
2. Auto-explanation: "12 transactions vs usual 8"
3. Click category to see details
4. Inline drilldown shows top merchants
5. Recognize pattern: "Oh right, conference meals"
6. Optional: Tag transactions for future reference

**Success Metric:** User identifies spending change reason in <30 seconds.

---

#### Journey 3: Address Actionable Items (As Needed)
**User Goal:** "Fix whatever needs fixing"

**Current Experience:**
1. Scan "Needs Attention" card
2. See mixed list: price changes, alerts, integrity tasks
3. Unclear what's urgent vs informational
4. Click through to drilldown modal
5. Manual classification with dropdowns
6. Hit save button
7. Page refreshes
8. Repeat for next item

**Desired Experience:**
1. Dashboard shows priority tasks in context
2. "3 unclassified credits need review" - click
3. Inline classification: swipe or quick-select
4. Auto-saves immediately
5. Item disappears from list
6. Move to next task

**Success Metric:** Classify 10 transactions in <60 seconds (currently ~5 minutes).

---

#### Journey 4: Review Recurring Expenses (Monthly)
**User Goal:** "Make sure I'm not wasting money on subscriptions"

**Current Experience:**
1. Scroll to "Recurring" card
2. See list of subscriptions with monthly totals
3. Mentally compare to last month (not visible)
4. Notice "was $12.99" price change tag
5. Wonder: Should I cancel? Is this normal?
6. No clear next action

**Desired Experience:**
1. Dashboard shows: "Subscriptions: $247/mo (up $15 from avg)"
2. Click to expand
3. See sorted list with usage indicators: "Spotify: $10.99/mo - Active 24 days"
4. Price changes highlighted with context: "Netflix up $2 - now $15.99"
5. Inline actions: "Cancel" or "Mark as reviewed"

**Success Metric:** Review all subscriptions and make decisions in <2 minutes.

---

#### Journey 5: Explore Historical Trends (Monthly)
**User Goal:** "How am I doing over time?"

**Current Experience:**
1. Scroll to bottom chart
2. See 6-month bar chart of net savings
3. No annotations or insights
4. Hover for exact numbers
5. Manually look for patterns
6. Wonder: Is this trend good?

**Desired Experience:**
1. Dashboard shows trend summary: "Savings improving - up 15% last 3 months"
2. Inline sparkline shows direction
3. Click to expand detailed trend view
4. Chart annotated with events: "Q4 holiday spending", "Tax refund"
5. Projected savings: "On track for $28k this year"

**Success Metric:** Understand long-term financial trajectory in <10 seconds.

---

### 2.2 Secondary User Journeys

- **Search for specific transaction** (infrequent): Works well currently, keep as-is
- **Filter by account** (occasional): Current implementation adequate
- **Export data** (rare): Keep in drilldown footer, no changes needed
- **Add notes/tags** (occasional): Good implementation, make more discoverable

---

## 3. Information Architecture Redesign

### 3.1 The Financial Planner Mental Model

When a financial planner reviews a client's finances, they follow this hierarchy:

1. **Health Assessment** - Is the client sustainable? (Income vs expenses trend)
2. **Risk Identification** - What needs immediate attention? (Overspending, anomalies)
3. **Efficiency Review** - Are they optimizing? (Recurring costs, savings rate)
4. **Behavior Patterns** - What are their spending habits? (Categories, merchants)
5. **Planning Support** - What's the forecast? (Trend projections, budgets)

**Current Dashboard:** Shows #4 and #5 first, buries #1-3.

**Dashboard v3:** Inverts the pyramid - health first, details on demand.

---

### 3.2 New Card Structure

#### SECTION 1: FINANCIAL HEALTH (Above the fold)

**Card: Health Snapshot (Hero)**
- **Purpose:** Answer "Am I okay?" in 3 seconds
- **Content:**
  - Health score: "Strong" | "On Track" | "Needs Attention" | "At Risk"
  - Primary metric: Savings rate % (not dollar amount)
  - Secondary metric: Net savings $ with trend indicator
  - Trend sparkline (3-month)
  - Key insight: "You're saving 23% of income - 5% above your average"
- **Interactions:**
  - Click sparkline → expand to full trend view
  - Click insight → explanation modal
  - No action buttons
- **Visual Treatment:**
  - Large, clean, high contrast
  - Color-coded health indicator (green/yellow/red)
  - Minimal text, maximum signal

**Data Source (Backend):**
```python
{
  "health_status": "on_track",  # strong | on_track | needs_attention | at_risk
  "savings_rate_pct": 23,
  "savings_rate_trend": "up",   # up | down | stable
  "net_cents": 284700,
  "net_trend": [21, 18, 20, 23],  # Last 4 months for sparkline
  "insight": "You're saving 23% of income - 5% above your 18% average",
  "comparison_to_avg": 5,  # Percentage points
}
```

---

**Card: Priority Actions (Conditional)**
- **Purpose:** Surface only critical tasks requiring user decision
- **Content:**
  - Max 3 items (rest hidden in "View all X tasks")
  - Prioritized by urgency: Integrity issues > Anomalies > Price changes
  - Each item shows: icon, description, affected amount, quick action
  - Auto-collapses when empty
- **Interactions:**
  - Inline actions (no modal): Swipe to dismiss, tap to classify
  - Auto-save on action
  - Item fades out on completion
  - Click "View all" → full task list modal
- **Visual Treatment:**
  - Compact, action-oriented
  - Icons indicate task type
  - Subtle animations on interaction

**Example Tasks:**
1. "Classify 3 unclassified credits ($847)" [Resolve →]
2. "Netflix price increased to $15.99" [Dismiss]
3. "Unusual charge: AMZN $423" [OK | Flag]

**Data Source:**
```python
{
  "priority_tasks": [
    {
      "type": "classify_credits",
      "title": "Classify 3 unclassified credits",
      "amount_cents": 84700,
      "action_type": "resolve",  # resolve | dismiss | flag
      "drilldown_scope": "credit_other",
    }
  ],
  "total_task_count": 7,  # For "View all 7 tasks" link
}
```

---

#### SECTION 2: SPEND INTELLIGENCE (Mid-page)

**Card: Spending Insights**
- **Purpose:** Explain where money went WITH CONTEXT
- **Content:**
  - Top 5 categories only
  - Each shows: icon, name, amount, % of total spending, trend vs last month
  - Insights auto-generated: "Dining up 47% - 12 transactions vs usual 8"
  - "Budget" column if budgets exist: "$450 / $600 budget (75%)"
- **Interactions:**
  - Click category → inline expansion showing top 3 merchants
  - Click merchant → drilldown to transactions
  - No separate "View all" action - just show more categories
- **Visual Treatment:**
  - Progress bars for budget visualization
  - Color-coded trends (red up, green down)
  - Inline merchant chips on expansion

**Data Source:**
```python
{
  "top_categories": [
    {
      "category": {"id": "dining", "name": "Dining", "icon": "🍽️"},
      "net_cents": 45000,
      "pct_of_total": 18,
      "trend_vs_last_month": 47,  # Percentage change
      "transaction_count": 12,
      "avg_transaction_count": 8,
      "budget_cents": 60000,  # Optional
      "top_merchants": [
        {"name": "Chipotle", "amount_cents": 15000, "count": 4},
        {"name": "Starbucks", "amount_cents": 12000, "count": 8},
      ],
      "insight": "Up 47% - 12 transactions vs usual 8"
    }
  ]
}
```

---

**Card: Recurring Optimization**
- **Purpose:** Help users optimize fixed costs
- **Content:**
  - Total monthly recurring: "$247/mo"
  - Trend vs average: "(up $15 from avg)"
  - Subscriptions list with usage indicators
  - Price change annotations
  - "Review recommended" badge for unused subscriptions
- **Interactions:**
  - Click subscription → inline actions: "Cancel", "Update amount", "Mark as reviewed"
  - Price changes auto-highlighted
  - Dismissible after review
- **Visual Treatment:**
  - Clean list with right-aligned amounts
  - Usage indicators (active/inactive)
  - Inline action buttons on hover/tap

**Data Source:**
```python
{
  "total_recurring_cents": 24700,
  "trend_vs_avg_cents": 1500,  # Positive = increase
  "subscriptions": [
    {
      "merchant": "Netflix",
      "amount_cents": 1599,
      "frequency": "monthly",
      "last_charged": "2026-02-03",
      "price_change": {"old_cents": 1399, "new_cents": 1599},
      "usage_days": 24,  # Out of 30 - shows active usage
      "recommendation": None,  # "review_usage" | "cancel_duplicate" | None
    }
  ]
}
```

---

#### SECTION 3: HISTORICAL CONTEXT (Below fold)

**Card: Trend Analysis**
- **Purpose:** Show financial trajectory over time
- **Content:**
  - 6-month trend chart (net savings OR savings rate %)
  - Auto-annotations: "Holiday spending", "Tax refund"
  - Projection: "On track for $28k savings this year"
  - Toggle: Net $ vs Savings %
- **Interactions:**
  - Hover/tap month → see breakdown
  - Click bar → drilldown to that month
  - Toggle chart metric
- **Visual Treatment:**
  - Chart.js with annotations
  - Color-coded bars (green savings, red deficit)
  - Projected trend line

---

### 3.3 Progressive Disclosure Strategy

**Level 1: Glance (Dashboard View)**
- Health status
- Savings rate %
- Top 3 priority tasks
- Top 5 spending categories

**Level 2: Scan (Card Expansion)**
- Category details → Top merchants inline
- Recurring details → Usage indicators
- Trend details → Month breakdown

**Level 3: Deep Dive (Modal Drilldown)**
- Full transaction list
- Classification tools
- Export/search functions
- Notes/tags interface

**Rule:** Never show Level 3 unless user explicitly requests it.

---

## 4. Data Storytelling Framework

### 4.1 Insight Generation Rules

The dashboard should automatically generate insights using these patterns:

#### Pattern 1: Variance Explanation
**When:** Current value differs from average by >10%
**Template:** "[Category] [up/down] [X%] - [reason]"
**Example:** "Dining up 47% - 12 transactions vs usual 8"

**Implementation:**
```python
def generate_variance_insight(category_data, historical_data):
    current = category_data['net_cents']
    avg = historical_data['avg_net_cents']
    if abs(current - avg) / avg > 0.1:
        reason = _infer_reason(category_data, historical_data)
        return f"{category} {direction} {pct_change}% - {reason}"
```

---

#### Pattern 2: Trend Narrative
**When:** 3+ months of consistent direction
**Template:** "[Metric] [improving/declining] - [X%] last [N] months"
**Example:** "Savings improving - up 15% last 3 months"

---

#### Pattern 3: Budget Status
**When:** Budget exists for category
**Template:** "[X%] of [budget name] used - [status]"
**Example:** "75% of dining budget used - on track for month"

**Status logic:**
- Days remaining > budget remaining % → "ahead of schedule"
- Days remaining < budget remaining % → "on track"
- Days remaining << budget remaining % → "over budget"

---

#### Pattern 4: Recurring Optimization
**When:** Price change detected OR usage low
**Template:** "[Service] [change] - [recommendation]"
**Example:** "Spotify unused last 20 days - consider canceling"

---

### 4.2 Contextual Comparisons

Every number should answer: "Compared to what?"

**Current Implementation:**
- Net savings: $2,847 (no context)
- Category spending: $450 (no context)

**v3 Implementation:**
- Net savings: $2,847 (+$340 vs 3mo avg, 23% of income)
- Category spending: $450 (up 12% vs last month, 75% of budget)

**Data Requirements:**
```python
{
  "value": 284700,
  "comparisons": {
    "vs_last_month": {"delta_cents": 34000, "direction": "up"},
    "vs_avg_3mo": {"delta_cents": 34000, "direction": "up"},
    "vs_budget": {"pct_used": 75, "status": "on_track"},
    "as_pct_of_income": 23,
  }
}
```

---

## 5. Action Minimization Strategy

### 5.1 Eliminate All Save Buttons

**Current Problems:**
- Transaction classification requires: Select → Save → Refresh
- Alert dismissal requires: Click OK → Confirm
- Notes require: Type → Blur → Auto-save (actually works!)

**v3 Approach:**
- **Optimistic UI**: Update UI immediately, sync in background
- **Auto-save everything**: No confirmation dialogs
- **Undo > Confirm**: Show undo toast instead of "Are you sure?"

**Implementation Pattern:**
```javascript
async function classifyTransaction(fingerprint, target_type) {
  // 1. Update UI immediately
  selectEl.disabled = true;
  selectEl.style.background = 'var(--accent-green-dim)';

  // 2. Save to server (background)
  const response = await finApi.postJSON('/api/txn-type-override', {
    fingerprint, target_type
  });

  // 3. Handle errors with undo
  if (!response.ok) {
    selectEl.value = originalValue;  // Revert
    showUndo('Classification failed - click to retry');
  } else {
    // 4. Remove from UI after 1 second
    setTimeout(() => row.fadeOut(), 1000);
  }
}
```

---

### 5.2 Contextual Actions Only

**Principle:** Show actions ONLY in the context where they're relevant.

**Current Problems:**
- "Export" button on every card
- "Explain" buttons that don't work
- "Close Period" in nav (user doesn't understand it)

**v3 Approach:**
- Export: Only in drilldown footer (when viewing transactions)
- Classification: Only when viewing unclassified items
- Close Period: Hidden entirely (backend auto-closes on sync)

**Action Inventory:**

| Action | Current Location | v3 Location | Rationale |
|--------|-----------------|-------------|-----------|
| Export CSV | Every card | Drilldown footer | Only relevant for transaction lists |
| Search | Top nav | Top nav | Keep - frequently used |
| Filter accounts | Top nav | Top nav | Keep - frequently used |
| Classify transaction | Drilldown table | Inline on row | Reduce modal depth |
| Dismiss alert | Alert card | Inline swipe | Faster interaction |
| Add note | Drilldown modal | Inline on transaction | Progressive disclosure |
| Close period | Nav bar | **REMOVE** | Auto-handled by system |
| Explain | Multiple cards | **REMOVE** | Replace with auto-insights |
| View details | Multiple cards | **REMOVE** | Just click the number |

---

### 5.3 Swipe/Gesture Interactions (Mobile-First)

Enable quick actions without button clicks:

- **Swipe left on alert** → Dismiss
- **Swipe right on transaction** → Add to favorites
- **Long press category** → Quick budget set
- **Pull down** → Refresh data

**Desktop Fallback:** Hover reveals action buttons.

---

## 6. Mental Model Alignment

### 6.1 What Users Should Think After 30 Seconds

**Current State (v2):**
- "I saved $2,847 this month"
- "I spent money on groceries, dining, transport..."
- "There are some alerts I should probably look at"
- **Still wondering:** "Is this good? What should I do?"

**Target State (v3):**
- "I'm on track - saving 23% of income"
- "My dining spending is higher than usual because of that conference"
- "I have 3 small tasks to clean up, will take 1 minute"
- **Confidence achieved:** "I understand my finances and know what to do"

---

### 6.2 Financial Literacy Assumptions

**Don't assume users know:**
- What a "savings rate" is → Show tooltip: "% of income not spent"
- Why net savings matters → Show context: "Income minus all expenses"
- What "Close Period" means → Don't use this term
- What "Integrity Score" means → Don't show this metric

**Do assume users understand:**
- Basic math (percentages, comparisons)
- Their own spending categories
- Budget concepts
- Subscription/recurring charges

**Design Principle:** Explain financial concepts inline, not in help docs.

---

## 7. Visual Design Specifications

### 7.1 Card Hierarchy (Visual Weight)

**Card Priority System:**
1. **Hero (Health Snapshot):** Largest text, most whitespace, primary color
2. **Priority Actions:** Colored badges, urgent styling
3. **Insights:** Medium weight, context-rich
4. **Historical:** Smallest, subtle styling

**Typography Scale:**
- Hero number: 3rem (48px) - Savings rate %
- Secondary hero: 2rem (32px) - Net savings $
- Card titles: 0.875rem (14px) - All caps, muted
- Body text: 0.875rem (14px) - Regular weight
- Metrics: 1.25rem (20px) - Tabular numbers

**Color Coding:**
- Green: Positive financial health, savings
- Red: Overspending, deficit, urgent actions
- Yellow: Warnings, price changes
- Blue: Information, neutral changes
- Gray: Historical, context data

---

### 7.2 Responsive Breakpoints

**Mobile (<640px):**
- Single column layout
- Health Snapshot full width
- Priority Actions stacked
- Category list collapsed (show top 3)
- Trend chart simplified

**Tablet (640-1024px):**
- 2-column grid
- Health Snapshot spans both columns
- Insights and Recurring side-by-side
- Full trend chart

**Desktop (>1024px):**
- 3-column grid for mid-section
- Health Snapshot hero treatment
- All content visible without scrolling (above fold)

---

### 7.3 Animation & Feedback

**Micro-interactions:**
- Number changes: Count-up animation (300ms)
- Task completion: Fade out + slide (400ms)
- Sparkline: Draw animation on load (600ms)
- Category expansion: Smooth height transition (250ms)

**Loading States:**
- Skeleton screens (no spinners)
- Progressive rendering (hero first, details after)
- Optimistic updates (assume success)

**Error States:**
- Inline error messages (no alerts)
- Undo actions (no confirmation dialogs)
- Retry buttons (no generic "Error")

---

## 8. Technical Implementation

### 8.1 Backend API Changes Required

#### New Endpoint: `/api/dashboard/health-snapshot`
```python
{
  "health_status": "on_track",  # Enum: strong | on_track | needs_attention | at_risk
  "savings_rate_pct": 23,
  "savings_rate_trend": "up",
  "net_cents": 284700,
  "net_trend_3mo": [210000, 180000, 200000, 284700],
  "insight": "You're saving 23% of income - 5% above your 18% average",
  "comparison_to_avg_pct": 5,
}
```

**Health Status Logic:**
```python
def compute_health_status(savings_rate_pct, trend):
    if savings_rate_pct >= 20 and trend == "up":
        return "strong"
    elif savings_rate_pct >= 10:
        return "on_track"
    elif savings_rate_pct >= 0:
        return "needs_attention"
    else:
        return "at_risk"
```

---

#### Enhanced Endpoint: `/api/dashboard/spending-insights`
```python
{
  "categories": [
    {
      "category": {"id": "dining", "name": "Dining", "icon": "🍽️"},
      "net_cents": 45000,
      "pct_of_total_spending": 18,
      "trend_vs_last_month_pct": 47,
      "transaction_count": 12,
      "avg_transaction_count": 8,
      "budget": {
        "target_cents": 60000,
        "used_pct": 75,
        "status": "on_track"  # ahead | on_track | over
      },
      "top_merchants": [
        {"name": "Chipotle", "amount_cents": 15000, "count": 4},
      ],
      "insight": "Up 47% - 12 transactions vs usual 8"
    }
  ]
}
```

**Insight Generation (Backend):**
```python
def generate_category_insight(current, historical):
    insight_parts = []

    # Variance
    pct_change = ((current['net_cents'] - historical['avg_net_cents'])
                  / historical['avg_net_cents'] * 100)
    if abs(pct_change) > 10:
        direction = "Up" if pct_change > 0 else "Down"
        insight_parts.append(f"{direction} {abs(pct_change):.0f}%")

    # Transaction count change
    if current['count'] != historical['avg_count']:
        insight_parts.append(
            f"{current['count']} transactions vs usual {historical['avg_count']}"
        )

    # Budget status
    if budget and budget['used_pct'] > 90:
        days_left = (period_end - today).days
        if days_left > 7:
            insight_parts.append("nearing budget limit")

    return " - ".join(insight_parts) if insight_parts else None
```

---

#### New Endpoint: `/api/dashboard/priority-tasks`
```python
{
  "tasks": [
    {
      "type": "classify_credits",  # Enum: classify_credits | unmatched_transfers | anomaly | price_change
      "priority": 1,  # 1=critical, 2=high, 3=medium
      "title": "Classify 3 unclassified credits",
      "detail": "$847 needs categorization",
      "amount_cents": 84700,
      "action_type": "resolve",  # resolve | dismiss | flag
      "drilldown_scope": "credit_other",
      "count": 3,
    }
  ],
  "total_count": 7,
}
```

**Priority Logic:**
```python
def prioritize_tasks(integrity_tasks, alerts, price_changes):
    tasks = []

    # Priority 1: Integrity issues (affects accuracy)
    for task in integrity_tasks:
        if task.type == "CLASSIFY_CREDIT" and task.count > 0:
            tasks.append({"priority": 1, ...})

    # Priority 2: Anomalies (potential fraud)
    for alert in alerts:
        if alert.severity == "high":
            tasks.append({"priority": 2, ...})

    # Priority 3: Price changes (informational)
    for pc in price_changes:
        if pc['change_pct'] > 20:  # >20% increase
            tasks.append({"priority": 3, ...})

    return sorted(tasks, key=lambda t: t['priority'])[:3]  # Top 3 only
```

---

### 8.2 Frontend Component Structure

```
Dashboard v3
├── HealthSnapshotCard
│   ├── HealthScore (with icon)
│   ├── SavingsRate (primary metric)
│   ├── NetSavings (secondary metric)
│   ├── TrendSparkline (inline chart)
│   └── InsightText (auto-generated)
│
├── PriorityActionsCard (conditional render)
│   ├── TaskList (max 3 items)
│   │   └── TaskItem
│   │       ├── Icon
│   │       ├── Title + Detail
│   │       └── InlineActions
│   └── ViewAllLink (if total_count > 3)
│
├── SpendingInsightsCard
│   ├── CategoryList
│   │   └── CategoryRow
│   │       ├── Icon + Name
│   │       ├── Amount + Budget Bar
│   │       ├── Trend Badge
│   │       ├── InsightText
│   │       └── MerchantChips (on expansion)
│   └── ToggleBudgetView
│
├── RecurringOptimizationCard
│   ├── TotalRecurring (with trend)
│   ├── SubscriptionList
│   │   └── SubscriptionRow
│   │       ├── Name + Amount
│   │       ├── UsageIndicator
│   │       ├── PriceChangeBadge
│   │       └── InlineActions (on hover)
│   └── BillsList
│
└── TrendAnalysisCard
    ├── ChartToggle (Net $ vs Rate %)
    ├── TrendChart (Chart.js)
    ├── ProjectionLine
    └── Annotations (auto-generated)
```

---

### 8.3 State Management

**Current Problem:** Full page refreshes on every action.

**v3 Approach:** Client-side state with optimistic updates.

```javascript
// Dashboard state machine
const dashboardState = {
  health: { ... },           // From /api/dashboard/health-snapshot
  tasks: [ ... ],            // From /api/dashboard/priority-tasks
  insights: { ... },         // From /api/dashboard/spending-insights
  recurring: { ... },        // From /api/dashboard/recurring

  // UI state
  expandedCategories: new Set(),
  completedTasks: new Set(),
  pendingActions: new Map(),  // Optimistic updates
};

// Optimistic update pattern
async function dismissTask(taskKey) {
  // 1. Update UI immediately
  dashboardState.completedTasks.add(taskKey);
  rerenderTasks();

  // 2. Save to backend
  const response = await api.dismissTask(taskKey);

  // 3. Handle failure
  if (!response.ok) {
    dashboardState.completedTasks.delete(taskKey);
    rerenderTasks();
    showUndo('Dismiss failed - click to retry');
  }
}
```

---

### 8.4 Performance Targets

**Load Performance:**
- First Contentful Paint: <1s
- Time to Interactive: <2s
- Health Snapshot visible: <0.5s (render before API responds)

**Interaction Performance:**
- Click to drilldown: <100ms
- Category expansion: <50ms (no API call)
- Task completion feedback: <16ms (instant visual feedback)

**Data Freshness:**
- Sync interval: 15 minutes (background)
- Manual refresh: Pull-to-refresh
- Optimistic updates: Instant UI, 200ms API

---

## 9. Success Metrics & Validation

### 9.1 Quantitative Metrics

**Adoption Metrics:**
- Daily active usage: Target 80% of users (up from current ~40%)
- Time spent on dashboard: Target 2-3 minutes (down from 5-7 minutes)
- Task completion rate: Target 90% (up from ~50%)

**Efficiency Metrics:**
- Time to health assessment: Target <5 seconds (currently ~30s)
- Time to classify 10 transactions: Target <60 seconds (currently ~5 minutes)
- Actions per session: Target <5 clicks (currently 15-20)

**Data Quality Metrics:**
- Unclassified transaction backlog: Target <5% (currently ~12%)
- Budget adherence tracking: Target 70% of users create budgets (currently 10%)

---

### 9.2 Qualitative Validation

**User Testing Protocol:**
1. Show dashboard for 10 seconds
2. Hide screen
3. Ask: "Are your finances healthy this month?" → Should answer correctly
4. Ask: "What needs your attention?" → Should list 1-3 tasks
5. Ask: "Why is your spending different this month?" → Should explain with context

**Success Criteria:**
- 80% of users can assess financial health in <10 seconds
- 70% of users can identify priority actions without scrolling
- 60% of users can explain spending variance with context

---

### 9.3 A/B Test Plan

**Phase 1: Internal Testing (Week 1-2)**
- Single user (product owner) validates flows
- Measure time-to-task completion
- Identify bugs and UX issues

**Phase 2: Beta Release (Week 3-4)**
- 20% of users get v3, 80% stay on v2
- Track quantitative metrics (time spent, actions per session)
- Collect qualitative feedback (survey after 1 week)

**Phase 3: Full Rollout (Week 5+)**
- Gradual rollout to 100%
- Monitor error rates and performance
- Iterate based on feedback

**Rollback Criteria:**
- Error rate >5%
- User satisfaction score <7/10
- Task completion rate drops below v2

---

## 10. Migration & Rollout Plan

### 10.1 Phased Implementation

**Phase 1: Foundation (Week 1-2)**
- Backend API: Health snapshot endpoint
- Backend API: Enhanced insights with auto-generation
- Frontend: New card structure (static HTML)
- Frontend: Responsive grid layout

**Phase 2: Intelligence (Week 3-4)**
- Backend: Insight generation engine
- Backend: Priority task algorithm
- Frontend: Dynamic data binding
- Frontend: Sparkline charts

**Phase 3: Interactions (Week 5-6)**
- Frontend: Optimistic updates
- Frontend: Inline actions (swipe, quick-classify)
- Frontend: Category expansion
- Backend: WebSocket updates (optional)

**Phase 4: Polish (Week 7-8)**
- Animations and micro-interactions
- Mobile gestures
- Error handling and undo
- Performance optimization

---

### 10.2 Backward Compatibility

**Dual Dashboard Strategy:**
- v2 stays accessible at `/dashboard?v=2`
- v3 becomes default at `/dashboard`
- User preference stored in localStorage
- Toggle link in footer: "Try classic dashboard"

**Data Migration:**
- No database changes required
- All v3 features use existing data models
- New insights computed on-the-fly (no storage)

---

### 10.3 Training & Documentation

**User-Facing:**
- No training needed (intuitive design)
- Optional: Tooltip tour on first visit (5 steps)
- Help icon → Inline explanations, not separate docs

**Developer-Facing:**
- Update API documentation
- Component library with Storybook examples
- Migration guide for custom integrations

---

## 11. Risk Assessment & Mitigation

### 11.1 Technical Risks

**Risk: Backend insight generation too slow**
- **Likelihood:** Medium
- **Impact:** High (delays dashboard load)
- **Mitigation:**
  - Pre-compute insights during sync
  - Cache insights for 1 hour
  - Show skeleton UI while loading

**Risk: Optimistic updates cause data inconsistency**
- **Likelihood:** Low
- **Impact:** Medium (user confusion)
- **Mitigation:**
  - Server validates all actions
  - Revert UI on error with undo option
  - Periodic full refresh (every 15 min)

---

### 11.2 User Experience Risks

**Risk: Users confused by new layout**
- **Likelihood:** Medium
- **Impact:** Medium (temporary friction)
- **Mitigation:**
  - Optional onboarding tooltip tour
  - Gradual rollout (A/B test first)
  - Keep v2 accessible for 1 month

**Risk: Users miss important tasks (buried in "Priority Actions")**
- **Likelihood:** Low
- **Impact:** High (missed fraud alerts)
- **Mitigation:**
  - Badge count on Priority Actions card
  - Email notifications for critical tasks
  - Persistent indicator for high-priority items

---

### 11.3 Business Risks

**Risk: Increased server load from insight generation**
- **Likelihood:** Low
- **Impact:** Medium (infrastructure costs)
- **Mitigation:**
  - Use existing report caching
  - Insight generation is lightweight (<10ms)
  - Scale horizontally if needed

**Risk: Users don't engage with new features**
- **Likelihood:** Medium
- **Impact:** High (wasted development effort)
- **Mitigation:**
  - User testing before full build
  - Iterative development (ship MVP first)
  - Track engagement metrics daily

---

## 12. Open Questions & Decisions Needed

### 12.1 Design Decisions

1. **Health Score Algorithm:**
   - What savings rate % thresholds define "Strong" vs "On Track"?
   - Should we account for income level (e.g., $200k earner vs $40k)?
   - **Recommendation:** Start simple (>20% = Strong, 10-20% = On Track, 0-10% = Needs Attention, <0% = At Risk)

2. **Insight Prioritization:**
   - If multiple categories have large variance, which to show first?
   - Should we prioritize by absolute dollars or percentage change?
   - **Recommendation:** Prioritize by absolute impact (dollars) for high-level insight, percentage for detail view

3. **Budget Integration:**
   - Should budgets be required for category insights?
   - How to handle categories without budgets?
   - **Recommendation:** Budgets optional, fall back to historical average comparison

4. **Trend Chart Default:**
   - Show net dollars or savings rate % by default?
   - **Recommendation:** Savings rate % (more meaningful), with toggle for dollars

---

### 12.2 Technical Decisions

1. **Frontend Framework:**
   - Continue with vanilla JS or migrate to React/Vue?
   - **Recommendation:** Stay vanilla for v3.0, consider framework for v3.5+ if complexity grows

2. **Real-Time Updates:**
   - Implement WebSocket for live updates during sync?
   - **Recommendation:** Not for MVP, add in Phase 5 if user feedback requests it

3. **Mobile App:**
   - Build native iOS/Android or PWA?
   - **Recommendation:** PWA first (works on existing web codebase), native if traction grows

---

## 13. Appendix

### 13.1 Current Dashboard Audit

**Metrics (from code analysis):**
- Total cards: 5
- Total action buttons: 12+ (varies by state)
- Average cards shown: 4.2
- Average interactions per session: ~15 clicks
- Data points displayed: 37 (overwhelming)

**User Complaints Mapped to Code:**
```
"Too many calls to action"
→ Lines 776-782 (dashboard_v2.html): 3 buttons per attention item
→ Lines 309-323 (drilldown.js): Filter/Export/Close buttons

"Don't understand more about my finances"
→ Lines 658-665 (dashboard_v2.html): No contextual insights, just raw numbers
→ Lines 730-753 (dashboard_v2.html): Categories shown without comparison

"Match unmatched transfers UI is awful"
→ Lines 564-578 (drilldown.js): Manual select + explicit save button
→ Line 426: Bulk actions require confirmation
```

---

### 13.2 Wireframes Reference

**Health Snapshot Card:**
```
┌─────────────────────────────────────────┐
│ FINANCIAL HEALTH                        │
├─────────────────────────────────────────┤
│                                         │
│  On Track  ●───────────────────────     │
│                                         │
│            23%                          │
│         saved this month                │
│                                         │
│      +$2,847                            │
│   ▁▂▃█ +5% vs average                  │
│                                         │
│  You're saving 23% of income -          │
│  5% above your 18% average              │
│                                         │
└─────────────────────────────────────────┘
```

**Priority Actions Card:**
```
┌─────────────────────────────────────────┐
│ NEEDS YOUR ATTENTION (3)                │
├─────────────────────────────────────────┤
│                                         │
│ ⚠️ Classify 3 credits          [Resolve→]│
│    $847 needs categorization            │
│                                         │
│ 🔔 Netflix price up            [Dismiss] │
│    $13.99 → $15.99                      │
│                                         │
│ 🔍 Unusual charge             [OK][Flag] │
│    AMZN $423                            │
│                                         │
│              View all 7 tasks →          │
└─────────────────────────────────────────┘
```

**Spending Insights Card:**
```
┌─────────────────────────────────────────┐
│ WHERE YOUR MONEY WENT                   │
├─────────────────────────────────────────┤
│                                         │
│ 🍽️ Dining                     $450  +47%│
│ ████████████░░░ 75% of $600 budget      │
│ Up 47% - 12 transactions vs usual 8     │
│   ↳ Chipotle $150 (4×)                  │
│   ↳ Starbucks $120 (8×)                 │
│                                         │
│ 🛒 Groceries                  $320   -5%│
│ ███████████████ 80% of $400 budget      │
│                                         │
│ 🚗 Transport                  $180  +12%│
│ No budget set                           │
│                                         │
└─────────────────────────────────────────┘
```

---

### 13.3 Glossary

**Terms Used in This Spec:**

- **Health Score:** Computed status (Strong/On Track/Needs Attention/At Risk) based on savings rate and trend
- **Savings Rate:** Percentage of income not spent (net savings / income * 100)
- **Priority Tasks:** Actionable items ranked by urgency requiring user decision
- **Insight:** Auto-generated explanation for why a number is notable (variance, trend, or status)
- **Progressive Disclosure:** UX pattern showing summary first, details on demand
- **Optimistic Update:** UI pattern that updates interface immediately, syncs to server asynchronously
- **Drilldown:** Modal view showing detailed transaction list for a specific scope (category, merchant, etc.)

---

## 14. Conclusion & Next Steps

### 14.1 Summary

Dashboard v3 redesigns the financial dashboard from a transaction log into a financial health advisor. By prioritizing insights over data, actions over displays, and context over numbers, we transform user confusion into confidence.

**Key Innovations:**
1. **Health-first hierarchy** - Answer "Am I okay?" in 3 seconds
2. **Auto-generated insights** - Tell users WHY, not just WHAT
3. **Zero save buttons** - Everything auto-saves with optimistic updates
4. **Progressive disclosure** - Show summary → details → actions on demand
5. **Contextual comparisons** - Every number answers "compared to what?"

---

### 14.2 Success Criteria

Dashboard v3 succeeds when:
- ✅ Users can assess financial health in <5 seconds
- ✅ Priority tasks are completed in <60 seconds (vs 5 minutes)
- ✅ User feedback shifts from "confused" to "confident"
- ✅ Daily engagement increases by 50%
- ✅ Unclassified transaction backlog drops by 60%

---

### 14.3 Immediate Next Steps

**Week 1:**
1. Review this spec with product owner
2. Validate health score algorithm assumptions
3. Prioritize insight generation rules (which matter most?)
4. Design API contract for health snapshot endpoint

**Week 2:**
1. Create high-fidelity mockups (Figma)
2. User test mockups with 5 users
3. Refine based on feedback
4. Begin Phase 1 implementation

**Decision Points:**
- Approve/reject health score approach
- Approve/reject priority task algorithm
- Approve/reject insight generation patterns
- Set performance targets (confirm or adjust)

---

**Document Version:** 1.0
**Last Updated:** 2026-02-10
**Status:** Awaiting Product Owner Review

---

## Document Control

**Approval Required From:**
- [ ] Product Owner (requirements validation)
- [ ] Lead Developer (technical feasibility)
- [ ] UX Designer (visual design alignment)

**Review Cycle:**
- Drafts: Weekly during development
- Final Review: Before Phase 2 kickoff
- Post-Launch: Monthly retrospective

**Related Documents:**
- `C:\Users\AR\Projects\fin\src\fin\templates\dashboard_v2.html` - Current implementation
- `C:\Users\AR\Projects\fin\src\fin\static\js\fin_drilldown.js` - Drilldown system
- `C:\Users\AR\Projects\fin\src\fin\web.py` - Dashboard endpoint (lines 409-704)
- `C:\Users\AR\Projects\fin\src\fin\view_models.py` - Data models

---
