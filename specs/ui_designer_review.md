# Dashboard v3: UI Designer Review

**Reviewer:** UI Designer Agent
**Date:** 2026-02-10
**Status:** Feedback on concept mockup

---

## Ship-Blocking Issues

### 1. Color contrast failures — accessibility blocker

Green (#34a853) on dark bg is only 3.8:1 (needs 4.5:1). Yellow carets and muted text also fail WCAG AA.

**Fix:** Lighten green to #4aba6f (5.2:1 contrast). Increase yellow to #ffc933 (5.5:1). Lighten muted text from #5f6368 to #9aa0a6.

### 2. Mobile Cash Flow Meter layout breaks — UX blocker

The `padding-left: 82px` on `.cf-gap` is a desktop assumption. On mobile, the "Kept" amount loses its visual anchor to the bars above.

**Fix:** On mobile, stack the bars vertically (Income bar full-width, then Expenses bar, then the gap visualization). Show the "Kept" amount centered below with no left padding. The two-bar metaphor still works stacked.

### 3. Outlier carets (^^^) are confusing — comprehension blocker

No legend, three levels of escalation feel arbitrary, looks like a text artifact or encoding error in dark mode.

**Alternatives:**
- **Option A: Traffic light dots** — green (within 10%), yellow (10-30% above), red (30%+ above)
- **Option B: Percentage badges** — "+33%" in a small yellow badge next to category name. Precise and self-explanatory.
- **Option C: No indicator** — The "avg $640" text already communicates variance. Don't over-design.

**Recommendation:** Ship Option B (percentage badges) or Option C (no indicator). Do not ship carets.

---

## Design Problems

### 4. Card 2/3 headline numbers compete with hero

The `$2,140/mo` and `$2,463` totals (1.4rem, floated right) are nearly as prominent as the Cash Flow "Kept" amount. The eye treats them equally — violates hierarchy.

**Fix:** Reduce Card 2/3 headline size to 1.1-1.2rem and left-align them. Right-alignment makes them feel like the "punchline" when they're just context.

### 5. Period controls are too prominent

The controls bar has equal visual weight to cards. On mobile, this pushes the hero card down ~140px. First-time users see navigation before content.

**Fix:** Flatten the controls — remove background, reduce padding to 8px, make nearly invisible. Current month label can be 14px regular weight, not 16px semibold.

### 6. Clickable elements have no affordance

The bars, list items, and trend columns are all clickable but nothing signals it. No hover states, no pointer cursor on bars, no visual cue.

**Fix:**
- Add `cursor: pointer` to `.cf-bar-track` and hover state (slight brightness increase or border)
- Add subtle "→" icon on hover at right edge of list items
- Make entire row darken on hover with left border accent
- First-visit: show subtle pulse/glow animation on a category row with tooltip "Click any category to see transactions"

### 7. Heads Up action buttons are too small for mobile

`.hu-btn` padding is `4px 12px` = ~24px tall. Below the 44px touch target minimum.

**Fix:** Increase to `padding: 8px 16px` (min 44px tall). On mobile, stack buttons vertically if needed.

### 8. Spending card is too dense

7 categories × (name + amount + avg + caret) = 28 discrete data points. Visual weight crushes comprehension.

**Fix:** Show only top 5 categories by default. Add "+ 2 more categories" as an expandable inline toggle, not a drilldown.

### 9. Trend bar hover is counter-intuitive

`.trend-col:hover .trend-bar { opacity: 0.8; }` reduces opacity, making the bar feel like it's fading away, not being selected.

**Fix:** Increase opacity or brightness on hover. Add tooltip: "Click to view October 2025."

---

## Things the Specs Missed

### 10. No health score badge

The Cash Flow Meter shows *what* happened but not *is this good?* A first-time user with $1,247 saved doesn't know if that's strong or weak relative to their income.

**Fix:** Add a health badge to Card 1:
- "Strong" (green) if savings rate > 20%
- "On Track" (yellow-green) if 10-20%
- "Needs Attention" (orange) if 0-10%
- "At Risk" (red) if negative

### 11. No baseline callout

The "baseline" concept (income minus recurring commitments) doesn't appear in the mockup. Card 2 shows "37% of your income" but there's no explicit "Your baseline is $3,710/mo" callout.

**Fix:** Add a line to Card 1 in muted text below the savings rate: "Baseline: $3,710/mo (income - commitments)"

### 12. Pending transactions are too passive

"4 pending transactions not included" is muted and small. If there are $500 in pending transactions, the Cash Flow Meter is lying.

**Fix:** If pending amount > 5% of income, show a yellow warning above Card 1: "⚠ $500 pending — these numbers may change when transactions settle."

### 13. No global nav/menu in mockup

How do users reach Settings, Export, or the integrity review page?

**Fix:** Not actually an issue — the real app has a global nav bar in base.html with links to Dashboard, Recurring, Budget, Audit, Sync Log. The mockup is standalone HTML so it doesn't show this. Just ensure the integrity review page is accessible from the nav.

### 14. Purple spending bars are arbitrary

Financial convention is blue for spending. Purple has no meaning.

**Fix:** Change spending bars to --accent-blue (#4a90d9). Reserve purple for "other" or uncategorized.

### 15. "In progress" trend bar is ambiguous

The parenthesized value "(+$600)" could mean projected or estimated. The dashed border signals "incomplete" but isn't labeled.

**Fix:** Add label below the bar: "Projected (9 days in)" or "In progress" to clarify.

### 16. 3-month rolling average is under-explained

Every card references "3-month avg" but never explains why 3 months.

**Fix:** Add a small info icon next to first mention with tooltip: "We compare to your last 3 months to smooth out seasonal variation."

---

## Typography and Spacing Issues

### 17. Line-height inconsistency

Hero card `.cf-kept` uses `line-height: 1.1` (tight) while body text uses `1.5` (airy). The hero card feels cramped, supporting cards feel spacious.

**Fix:** Use `line-height: 1.2` for all large numbers (1.4rem+), `line-height: 1.4` for body text.

### 18. Category avg text too close to primary line

`.spend-avg` has `margin-top: 1px`. The two lines visually merge at small font sizes.

**Fix:** Increase to `margin-top: 4px`.

### 19. Card title margin too large on mobile

`.card-title` margin-bottom is fixed at 16px. With reduced mobile padding (18px), the title feels too far from content.

**Fix:** Reduce to 12px on mobile.

### 20. Trend chart labels overlap on narrow screens

With 6 bars on mobile, labels are cramped.

**Fix:** On mobile, hide `.trend-val` numbers and show only on tap. Keep month labels visible.

---

## Conditional Card 4 Layout Shift

When "Heads Up" disappears (clean month), the grid reflows and Card 5 jumps up. Disorienting.

**Fix:** Make Card 4 always full-width. When empty, show the "Nothing unusual this month" state as a true card with padding and borders — not a hidden element. The "clean state" should feel rewarding, not like a hole in the layout.

---

## Verdict

> "The v3 concept is a massive improvement over v2. The Cash Flow Meter is the right hero. The reduction from 19+ buttons to 3 core actions is correct. The removal of Close Period and Integrity Score jargon is correct. But don't ship the mockup as-is."

**75% of the way there.** Fix accessibility, simplify outlier indicators, add the health badge, and do one more mobile pass.
