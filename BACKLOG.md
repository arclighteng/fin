# fin Backlog

## UX/UI Improvements

### 12. Pro-rated budget projections
- **Issue**: Mid-month view shows misleading totals (e.g., only received 1 of 2 paychecks)
- **Solution**: Option to pro-rate recurring, discretionary, and net values based on:
  - Days elapsed in period vs total days
  - Expected vs actual income (if predictable pay schedule)

---

## Completed

### 1. "Review" badge unclear action ✓
- **Solution**: Changed "Review" badge to "⚠️ Dup?" with clear tooltip explaining click action
- Added proper Status column with status badges (Active, Known, Dup?)
- Renamed dismiss button to "Dismiss" with clear tooltip

### 2. Canceled subscription handling / Status column clarity ✓
- **Solution**: Added proper Status column with status badges:
  - "✓ Known" for recognized services
  - "⚠️ Dup?" for potential duplicates (clickable to dismiss)
  - "Active" for regular subscriptions/bills
- Separated Status column from Actions column for clarity
- "Dismiss" button clearly labeled

### 3. Non-income accounts showing "Over Budget" ✓
- **Solution**: Detect when viewing expense-only accounts (credit cards) and show different UI:
  - Shows "Card Spending" banner instead of "Cash Flow"
  - Shows spending breakdown (recurring vs one-time) as percentages
  - Shows "Total Spending" instead of "Net" / "Over budget"
  - Hides income-related sections

### 4. Transaction search with cross-account discovery ✓
- **Solution**: Added search bar at top of dashboard
  - Real-time search with debouncing
  - Shows results from selected accounts first
  - Shows "X matches in other accounts" below for discovery
  - Click to view all accounts if filtering

### 5. Sync button in UI ✓
- **Solution**: Added sync button to navigation bar
  - Shows spinner while syncing
  - Displays result count ("+X new")
  - Auto-reloads page after successful sync

### 6. Make Dashboard the landing page ✓
- **Solution**: Redirect `/` to `/dashboard`

### 7. Pending charges display ✓
- **Solution**: Added pending transaction tracking and display:
  - Added `pending` field to Transaction model and database
  - Capture pending status from SimpleFIN API
  - Show pending notice with count in dashboard
  - Style pending transactions differently in search results (yellow highlight + "(pending)" label)
  - Message: "Pending charges are included in the calculations shown."

### 8. Search filters dashboard sections ✓
- **Solution**: When searching, dashboard sections now filter to show related items:
  - Spending by Category: Shows only matching category names
  - Alerts: Shows only alerts matching the search term
  - Duplicate Subscriptions: Shows only matching merchants
  - Subscriptions & Bills: Shows only matching merchant rows
  - Financial Health and Historical Summary remain unfiltered

### 9. Document SimpleFIN setup process ✓
- **Solution**: Created `docs/SIMPLEFIN_SETUP.md` with setup guide

### 10. Code cleanup before commit ✓
- **Solution**: Removed unused files and build artifacts:
  - `nul` (accidental file)
  - `example-task.cmd`, `s.cmd`, `sd.cmd`, `se.cmd`, `t.cmd`, `tc.cmd`, `tq.cmd`, `w.cmd` (dev shortcuts)
  - `check_paramount.py` (debug script)
  - `src/fin/notifications.py` (unused email stub)
  - `src/fin/ml_classifier.py` (unused ML module)
  - `src/fin/templates/home.html` (unused template)
  - `src/finproj.egg-info/` (build artifact)
- Added `*.egg-info/` to `.gitignore`

### 11. Search results pagination ✓
- **Solution**: Added pagination to search results:
  - Limited to 8 results per page
  - Previous/Next navigation buttons
  - Page counter showing current/total pages
