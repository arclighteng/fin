# Fin Dashboard Redesign: Project Summary

**Date:** 2026-02-10
**Branch:** `dashboard-v3-redesign`
**Status:** Concept review phase — no v3 code written yet

---

## Background

### What happened before v3

1. **Fintech audit** identified 22 findings across the codebase. All 22 were fixed across 15+ files, including:
   - Float arithmetic replaced with integer cents
   - `date.today()` replaced with timezone-aware `dates_mod.today()`
   - OverrideRegistry.load_from_db fixed
   - Transfer pairing filter fixed in reporting.py
   - Audit timestamps converted to UTC
   - Planner refactored to use canonical ReportService

2. **Dashboard v2** was built as the first redesign attempt:
   - 5 cards: Financial Summary (hero), Where Your Money Went, Needs Attention, Recurring, Net Savings Trend
   - Month navigation with arrows
   - Auto-save classification in drilldown (Save button removed)
   - `detect_price_changes()` dict access bug found and fixed
   - Server restarted and verified — all cards render, removed elements confirmed gone

3. **User feedback on v2:** Still too many buttons, still doesn't help understand finances, needs a complete rethink.

---

## v3 Design Process

### Expert specs commissioned

Three agents produced independent design specs on the `dashboard-v3-redesign` branch:

| Spec | File | Key Contribution |
|------|------|-----------------|
| Business Analyst | `specs/business_analyst.md` (1329 lines) | Health-first hierarchy, auto-generated insights, progressive disclosure, health score labels |
| Product Manager | `specs/product_manager.md` (796 lines) | "Baseline" concept (income - recurring), mobile-first wireframes, plain language replacements, P0/P1/P2 prioritization |
| Financial Expert | `specs/financial_expert.md` (611 lines) | Cash Flow Meter (two-bar), "Kept/Over" framing, 3-month rolling averages, outlier carets, conditional Heads Up card, mid-month pacing, separate financial insight from system maintenance |

### Universal agreement across all three specs

- Remove Close Period, Integrity Score, Export buttons, Explain/View All from dashboard
- Auto-save everything — zero save buttons anywhere
- 3-month rolling averages for all comparisons (single-month is too volatile)
- Financial planner perspective, not transaction report
- Progressive disclosure: glance → scan → drilldown
- Mobile-first design
- Reduce action count dramatically
- Drilldown on click (keep existing system)

### Key disagreements resolved in synthesis

| Topic | BA | PM | FE | Decision |
|-------|----|----|----|----|
| Hero card | Health score label | "Baseline" concept | Cash Flow Meter (two bars) | **Cash Flow Meter** (most visceral) |
| Categories | Insights with merchant chips | Trend indicators (↑↓) | Bars with 3-month avg + carets | **Bars with 3-month avg** |
| Alert limits | Max 3 | Max 5 | Max 4, conditional | **Max 4, conditional rendering** |
| Classification | Keep in drilldown | Keep in drilldown | Move to separate page | **Keep in drilldown** (auto-save already works) |
| Outlier indicators | N/A | ↑↓ arrows | Carets (^, ^^, ^^^) | **Carets** (but UI designer flagged — see below) |

---

## v3 Unified Concept Design

### File: `specs/dashboard_v3_concept.md`

### 5-Card Layout

| Card | Position | Purpose |
|------|----------|---------|
| **1. Cash Flow** | Full-width hero | Two proportional bars (income vs expenses), gap = "Kept $X" or "Over $X", savings rate, 3-month avg comparison, mid-month pacing |
| **2. Monthly Commitments** | Half-width left | Total with % of income bar, items sorted by amount, price changes inline, subs vs bills split |
| **3. Spending Breakdown** | Half-width right | Category bars with 3-month rolling averages, outlier indicators, footer summary |
| **4. Heads Up** | Full-width, conditional | Suspicious charges, multi-month trends, bill deviations. Only renders when items exist. Max 4. |
| **5. Your Trend** | Full-width bottom | Bar chart of monthly "kept" amounts, current month "in progress" styling, rolling avg footer |

### What's removed from dashboard surface
- Close Period button/badge
- Integrity Score / Data Trust card
- Audit card
- "Explain" links
- "View All" / "+ N more" links
- Export buttons on cards
- Income breakdown section
- Cross-account duplicates warning
- Duplicate subscription warnings
- Save button in drilldown (already removed in v2)
- Count badge on alerts header

### What's new
- Cash Flow Meter (two-bar visual)
- "Kept" / "Over" framing
- Mid-month pacing indicator
- Commitment % of income progress bar
- Subs vs Bills split
- Bill average inline ("avg $128")
- 3-month rolling average per category
- Outlier indicators on above-average categories
- Multi-month trend alerts (3+ consecutive increases)
- Bill deviation alerts (>15% from rolling avg)
- Conditional card rendering
- Integrity banner (top-of-page, only when degraded)
- "In progress" trend bar styling
- Trend footer rolling average

### Backend changes required
1. **3-month category averages** (~15 lines in web.py) — compute from `reports[0:3]`
2. **Multi-month category trend detection** (~20 lines) — check 3+ consecutive increases
3. **Restructure attention_items** — remove integrity tasks and price changes, add bill deviations and category trends
4. **Mid-month pacing** — JavaScript only, no backend

### What stays unchanged
- Global nav bar (sync button, theme toggle, page links)
- Drilldown modal system (fin_drilldown.js with auto-save)
- All existing API endpoints
- Account filter functionality
- Search functionality
- Transaction notes and tags

---

## Visual Mockup

### File: `specs/v3_mockup.html`

Standalone HTML with 4 interactive state toggles:
- **Healthy month** — green "Kept $1,247", income bar wider
- **Overspent month** — red "Over $650", expenses bar wider
- **Low integrity** — yellow banner above cards
- **Nothing unusual** — Heads Up card shows empty state

Dark mode only (real app has both via base.html theme toggle).

---

## UI Designer Review

### File: `specs/ui_designer_review.md`

### Ship-Blocking Issues (must fix before implementation)

1. **Color contrast failures** — Green, yellow, and muted text all fail WCAG AA on dark backgrounds. Need lighter values.
2. **Mobile Cash Flow Meter breaks at 375px** — padding-left assumption doesn't work. Needs stacked layout on mobile.
3. **Outlier carets (^^^) are confusing** — No legend, looks like text artifact. Recommends percentage badges ("+33%") or dropping indicators entirely.

### Design Changes Recommended

4. Card 2/3 headline numbers too large — reduce from 1.4rem to 1.1rem, left-align
5. Period controls too prominent — flatten, remove background
6. No clickable affordances — need cursor:pointer, hover states, arrow icons
7. Alert buttons too small for mobile touch targets (24px, need 44px)
8. Spending card too dense — show top 5 categories, not 7
9. Trend bar hover reduces opacity (counterintuitive) — should increase brightness

### Missing Elements to Add

10. **Health score badge** on Card 1 — "Strong" / "On Track" / "Needs Attention" / "At Risk" based on savings rate
11. **Baseline callout** in Card 1 — "Baseline: $3,710/mo (income - commitments)"
12. **Pending transaction warning** when amount > 5% of income
13. **Purple spending bars** should be blue (financial convention)
14. **"In progress" trend bar** needs clearer label
15. **3-month avg tooltip** explaining why 3 months

### Verdict
> "75% of the way there. The Cash Flow Meter is the right hero. The reduction from 19+ buttons to 3 core actions is correct. Fix accessibility, simplify outlier indicators, add the health badge, and do one more mobile pass."

---

## Key Decisions Still Needed

1. **Outlier indicators:** Ship percentage badges (+33%), traffic light dots, or no indicator at all? (UI designer says don't ship carets)
2. **Health score badge:** Add "Strong/On Track/At Risk" label to Card 1? (UI designer says yes, FE spec says no — "numbers speak for themselves")
3. **Baseline callout:** Show "Baseline: $X/mo" explicitly in Card 1? (PM spec wants it, FE spec doesn't mention it)
4. **Spending bar color:** Purple (current mockup) or blue (financial convention)?
5. **Category count:** Show top 5 (UI designer) or top 7 (FE spec)?

---

## Next Steps

1. **Decide on open questions above** — user review
2. **Update mockup** with accessibility fixes, chosen indicator style, mobile layout fix, health badge decision
3. **Implement backend changes** in web.py (~50 lines: 3-month avgs, trend detection, restructured attention_items)
4. **Write new template** (dashboard_v2.html rewrite or new file)
5. **Update CSS** for new card styles, light mode, mobile breakpoints
6. **Restart server and verify** on mobile and desktop
7. **Test drilldown integration** — all click targets open correct drilldowns

---

## File Inventory

| File | Status | Purpose |
|------|--------|---------|
| `specs/business_analyst.md` | Complete | BA design spec |
| `specs/product_manager.md` | Complete | PM design spec |
| `specs/financial_expert.md` | Complete | FE design spec |
| `specs/dashboard_v3_concept.md` | Complete | Unified synthesis of all three specs |
| `specs/v3_mockup.html` | Complete | Visual HTML mockup (dark mode, 4 states) |
| `specs/ui_designer_review.md` | Complete | UI designer feedback |
| `specs/project_summary.md` | Complete | This file |
| `src/fin/web.py` | v2 changes done, v3 not started | Dashboard endpoint (lines 409-704) |
| `src/fin/templates/dashboard_v2.html` | v2 rewrite done, v3 not started | Dashboard template |
| `src/fin/static/js/fin_drilldown.js` | Auto-save done | Drilldown modal (keep as-is for v3) |
