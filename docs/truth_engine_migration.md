# Truth Engine Migration Checklist

## Goal
Eliminate all legacy truth paths. ALL user-visible numbers must come from `report_period()`
(or a thin wrapper). No exceptions.

## Current State

### Canonical Engine (USE THIS)
| File | Function | Status |
|------|----------|--------|
| `reporting.py` | `report_period()` | Canonical source of truth |
| `reporting.py` | `report_month()` | Wrapper around report_period |
| `reporting.py` | `report_this_month()` | Wrapper around report_month |

### Legacy Engines (TO ELIMINATE)
| File | Function | Used By |
|------|----------|---------|
| `analysis.py` | `analyze_periods()` | web.py, cli.py |
| `analysis.py` | `_analyze_single_period()` | Internal |
| `analysis.py` | `analyze_custom_range()` | web.py |
| `analysis.py` | `get_current_period()` | web.py |
| `classify.py` | `classify_month()` | Internal |
| `classify.py` | `summarize_month()` | status_commands.py |
| `classify.py` | `detect_alerts()` | status_commands.py, web.py |
| `categorize.py` | `categorize_transactions()` | refund_matching.py |
| `categorize.py` | `get_category_breakdown()` | web.py, refund_matching.py |

---

## Web Entrypoints

### Dashboard & Main Views

| Route | Function | Current Engine | Target | Status |
|-------|----------|----------------|--------|--------|
| `GET /` | `root()` | Redirect | N/A | OK |
| `GET /dashboard` | `dashboard()` | `analyze_periods()` LEGACY | ReportService | [ ] TODO |
| `GET /subs` | `subs_page()` | `get_subscriptions()`, `get_bills()` | Keep (detection only) | [ ] REVIEW |
| `GET /watchlist` | `watchlist_page()` | Template only | N/A | OK |
| `GET /anomalies` | `anomalies_page()` | `detect_duplicates()`, `detect_sketchy()` | Keep (detection only) | [ ] REVIEW |
| `GET /sync-log` | `sync_log_page()` | Template only | N/A | OK |

### API Endpoints - Numbers/Totals

| Route | Function | Current Engine | Target | Status |
|-------|----------|----------------|--------|--------|
| `GET /api/categories` | `api_categories()` | `CATEGORIES` from categorize | ReportService | [ ] TODO |
| `GET /api/category/{id}` | `api_category()` | `get_category_breakdown()` LEGACY | ReportService | [ ] TODO |
| `GET /api/transactions-by-type` | `api_transactions_by_type()` | analysis/classify imports LEGACY | ReportService | [ ] TODO |
| `GET /api/search` | `api_search()` | Direct SQL | Keep (search only) | OK |
| `GET /api/payee/{payee}` | `api_payee()` | `_match_known_subscription()` | Keep (lookup only) | OK |

### API Endpoints - Planner/Projections (Already use new modules)

| Route | Function | Current Engine | Status |
|-------|----------|----------------|--------|
| `GET /api/planner/budget` | `api_planner_budget()` | planner.py | OK |
| `GET /api/planner/bucket/{name}` | `api_planner_bucket()` | planner.py | OK |
| `GET /api/planner/projection` | `api_planner_projection()` | planner.py | OK |
| `GET /api/cashflow/projection` | `api_cashflow_projection()` | projections.py | OK |
| `GET /api/cashflow/alerts` | `api_cashflow_alerts()` | projections.py | OK |
| `GET /api/report/snapshot` | `api_report_snapshot()` | reporting.py | OK |

### Export Endpoints

| Route | Function | Current Engine | Target | Status |
|-------|----------|----------------|--------|--------|
| `GET /export/sketchy` | `export_sketchy()` | `detect_sketchy()` | Keep (detection) | OK |
| `GET /export/duplicates` | `export_duplicates()` | `detect_duplicates()` | Keep (detection) | OK |
| `GET /export/subscriptions` | `export_subscriptions()` | `get_subscriptions()` | Keep (detection) | OK |
| `GET /export/summary` | `export_summary()` | `analyze_periods()` LEGACY | ReportService | [ ] TODO |

---

## CLI Entrypoints

### Status Commands (status_commands.py)

| Command | Function | Current Engine | Target | Status |
|---------|----------|----------------|--------|--------|
| `fin status` | `status_command()` | `summarize_month()` LEGACY | ReportService | [ ] TODO |
| `fin drill` | `drill_command()` | `summarize_month()`, `detect_alerts()` LEGACY | ReportService | [ ] TODO |
| `fin trend` | `trend_command()` | `summarize_month()` LEGACY | ReportService | [ ] TODO |

### CLI Commands (cli.py)

| Command | Function | Current Engine | Target | Status |
|---------|----------|----------------|--------|--------|
| `fin report` | `report()` | `analyze_periods()` LEGACY | ReportService | [ ] TODO |
| `fin month` | `month_report()` | `analyze_periods()` LEGACY | ReportService | [ ] TODO |
| `fin export-csv` | `export_csv()` | Direct SQL | Review | [ ] REVIEW |
| `fin export-summary` | `export_summary_cmd()` | `analyze_periods()` LEGACY | ReportService | [ ] TODO |

---

## Internal Dependencies

| File | Imports From | Impact |
|------|--------------|--------|
| `projections.py` | `classify.py` (`_detect_patterns`, `get_subscriptions`, `get_bills`) | Pattern detection - keep |
| `planner.py` | `classifier.py` (`classify_transaction`, `_detect_patterns`) | Uses new classifier - OK |
| `refund_matching.py` | `categorize.py` (`get_category_breakdown`, `categorize_transaction`) | Needs migration |
| `web.py` | `analysis.py`, `categorize.py`, `classify.py` | Primary migration target |
| `cli.py` | `analysis.py`, `classify.py` | Primary migration target |
| `status_commands.py` | `classify.py` (`summarize_month`, `detect_alerts`) | Primary migration target |

---

## Migration Order

### Commit 1: This document (Step 0)
- [x] Map all entrypoints
- [x] Document current engine usage

### Commit 2: ReportService API (Step 1)
- [ ] Create `src/fin/report_service.py`
- [ ] Add `versioning.py` with snapshot_id computation
- [ ] Add `as_of` parameter for historical anchoring
- [ ] Export: `report_period()`, `report_month()`, `report_periods()`

### Commit 3: Legacy Quarantine (Step 2)
- [ ] Rename `analysis.py` → `legacy_analysis.py`
- [ ] Rename `classify.py` → `legacy_classify.py`
- [ ] Add deprecation warnings
- [ ] Update internal imports

### Commits 4-6: Web Migration (Step 3)
- [ ] `GET /dashboard` → ReportService
- [ ] `GET /api/category/{id}` → ReportService
- [ ] `GET /api/transactions-by-type` → ReportService
- [ ] `GET /export/summary` → ReportService
- [ ] Custom range → ReportService

### Commits 7-8: CLI Migration (Step 4)
**DEFERRED**: CLI commands use MonthSummary which includes cadence data not in Report model.
Full migration requires adding cadence to ClassifiedTransaction or querying patterns.

Current state (acceptable for now):
- [x] CLI still imports legacy_classify (with deprecation warning)
- [x] CLI totals are consistent with legacy logic
- [ ] FUTURE: `fin status` → ReportService
- [ ] FUTURE: `fin drill` → ReportService
- [ ] FUTURE: `fin trend` → ReportService
- [ ] FUTURE: `fin export-summary` → ReportService

### Commits 9-11: Truth Leaks (Step 5)
- [ ] Pending filter consistency
- [ ] account_filter semantics (None/[]/list)
- [ ] Transfer pairing without keywords
- [ ] Historical anchoring with as_of

### Commit 12: Enforcement Test (Step 6)
- [ ] Test that web/cli don't import legacy modules
- [ ] Test that forbidden functions aren't referenced

### Commit 13: Cleanup (Step 7)
- [ ] Delete unused legacy code
- [ ] Update docs/accuracy.md
- [ ] Mark this document complete

---

## Verification Commands

After each migration step:

```bash
# Run all tests
pytest tests/ -v

# Verify web totals match CLI
fin status --month 2026-01
# Compare to web dashboard for same month

# Check for legacy imports
grep -r "from .analysis import" src/fin/web.py src/fin/cli.py src/fin/status_commands.py
grep -r "from .classify import.*summarize_month\|classify_month\|detect_alerts" src/fin/

# Run the app
fin web
# Visit http://127.0.0.1:8000/dashboard
```
