# Accuracy & Truth Contract

This document defines the non-negotiable rules for financial accuracy in this system.

## Global Truth Contract

### 1. Transaction Types Are Mutually Exclusive

Every transaction is classified as exactly ONE of:

| Type | Description | Sign |
|------|-------------|------|
| `INCOME` | Proven income (payroll, user-marked) | Positive |
| `EXPENSE` | Spending (purchases, bills) | Negative |
| `TRANSFER` | Internal account movement (matched pair) | Either |
| `REFUND` | Matched refund of prior expense | Positive |
| `CREDIT_OTHER` | Unclassified positive (NOT assumed income) | Positive |

### 2. Positive Amount ≠ Income

A positive amount is a **CREDIT** until proven to be income:

1. **INCOME** - Only via:
   - User-marked income rule
   - Strong payroll evidence (payroll, direct deposit, employer keywords)

2. **REFUND** - Only via:
   - Match to a prior expense (same merchant, similar amount, reasonable time window)

3. **TRANSFER** - Only via:
   - Transfer pairing (matching outflow in another account)
   - Very strong transfer evidence (bank name patterns)

4. **CREDIT_OTHER** - Default for unclassified positives:
   - NOT counted as income
   - Surfaces as a resolution task for user

### 3. Transfers Do Not Affect Net Spend/Income

- Matched transfers (both legs identified) net to $0
- Unmatched transfers are flagged for resolution
- Transfer_in and transfer_out are tracked separately

### 4. Pending Excluded from Posted Totals

- Default: Posted transactions only
- Pending transactions shown separately when enabled
- Never mix pending into posted totals without explicit flag

### 5. Date Ranges Are End-Exclusive

All internal date handling uses:
```
start <= posted_at < end_exclusive
```

- January 2026: `2026-01-01` to `2026-02-01` (exclusive)
- User-facing inputs converted at boundary
- SQL queries always use `< end_exclusive`

### 6. Money Uses Decimal with ROUND_HALF_UP

```python
from decimal import Decimal, ROUND_HALF_UP

cents = (Decimal(str(amount)) * 100).quantize(
    Decimal("1"), rounding=ROUND_HALF_UP
)
```

- No float arithmetic for money
- Standard financial rounding (0.5 rounds up)
- All storage in integer cents

### 7. One Canonical Report Engine

- `ReportService.report_period()` is the ONLY source of truth
- Web dashboard uses `ReportService` + `PeriodViewModel` adapter
- CLI currently uses legacy (migration deferred - see docs/truth_engine_migration.md)
- No parallel recomputation of totals in new code

**Report Reproducibility:**
- Every report includes `report_hash` (SHA256 of canonical JSON)
- Every report includes `snapshot_id` (hash of DB state)
- Same snapshot_id + inputs → same report_hash (reproducible)

### 8. Recommendations Gated by Integrity

- Integrity score must be >= 0.8 for recommendations
- Below threshold: show resolution tasks instead
- Tasks: classify credits, match transfers, reconcile statements

## Spending Buckets (Not "Recurring")

| Bucket | Definition | Examples |
|--------|------------|----------|
| `FIXED_OBLIGATIONS` | Predictable cadence subscriptions only | Netflix, rent, insurance |
| `VARIABLE_ESSENTIALS` | Habitual but irregular necessities | Groceries, gas, medicine |
| `DISCRETIONARY` | Optional/lifestyle spending | Dining, entertainment |
| `ONE_OFFS` | Truly one-time purchases | Annual fees, large items |

**Important**: Habitual spending (groceries 6x/month) is NOT fixed obligations.

## Integrity Flags

| Flag | Severity | Resolution |
|------|----------|------------|
| `UNMATCHED_TRANSFER` | Medium | Match or confirm as income/expense |
| `UNCLASSIFIED_CREDIT` | High | Classify as income/refund/transfer |
| `DUPLICATE_SUSPECTED` | Low | Confirm or dismiss |
| `RECONCILIATION_FAILED` | Critical | Match statement totals |
| `FUTURE_DATA_LEAK` | Critical | Fix anchor_date in patterns |
| `PENDING_IN_TOTALS` | Medium | Separate pending properly |
| `EMPTY_ACCOUNT_FILTER` | Info | Empty filter explicitly returns empty report |

## Findings Log

Issues discovered and fixed during implementation:

| Severity | Symptom | Root Cause | Fix | Tests |
|----------|---------|------------|-----|-------|
| Critical | Credits classified as income | Default positive → income | Default to CREDIT_OTHER | test_credit_not_income |
| Critical | Habitual = recurring | is_recurring included habitual | Separate is_recurring from is_habitual | test_habitual_discretionary |
| Critical | Future data in historical reports | _detect_patterns no upper bound | Added anchor_date cap | test_pattern_anchoring |
| High | Pending in totals | No pending filter | Added COALESCE(pending, 0) = 0 | test_pending_excluded |
| High | Float rounding errors | Using float for money | Decimal with ROUND_HALF_UP | test_money_rounding |
| Medium | Empty account filter = all | `if account_filter:` | Check for empty list explicitly | test_empty_filter |
| Medium | Transfer requires keywords | Must look like transfer | Keywords are bonus, not prereq | test_transfer_pairing |
| Medium | "purchase" matched "chase" | Substring match for banks | Use word-boundary regex | test_bank_keywords |
| Medium | "amazon.com" didn't match "amazon" | Split only on spaces | Split on spaces and punctuation | test_first_word_match |
| Low | Refund keyword in merchant name | "unknown credit" → REFUND | Correct behavior, test updated | test_resolution_tasks |

## Security Baseline

| Control | Implementation | Notes |
|---------|---------------|-------|
| .env excluded | .gitignore has `.env` | Secrets never committed |
| HTML escaping | `html.escape()` on user data | Prevents XSS |
| CSV sanitization | `sanitize_csv_field()` | Prevents formula injection |
| localhost-only | Default `--host 127.0.0.1` | Warning if 0.0.0.0 used |
| Mutation auth | Bearer token on POST/DELETE | FIN_API_TOKEN or auto-generated |

### API Authentication

- **Enabled by default**: Auto-generates session token on startup
- **Custom token**: Set `FIN_API_TOKEN` env var
- **Disable**: Set `FIN_AUTH_DISABLED=1` (not recommended for LAN)
- **Read-only endpoints**: Always public for local dashboard use
- **Mutation endpoints**: Require `Authorization: Bearer <token>` header

## Invariants (Checked at Runtime)

1. `sum(all_txn_amounts) == income + credits - expenses - transfers_out + transfers_in`
2. `transfer_in - transfer_out == 0` for matched transfers
3. No transaction has multiple types
4. All positive amounts are classified (no silent income assumption)
5. Historical reports are reproducible (anchored patterns, no date.today())
