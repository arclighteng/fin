# reporting.py
"""
Canonical report engine.

TRUTH CONTRACT:
- This is the ONLY source of truth for reports
- Web, CLI, and exports all call report_period()
- No parallel recomputation of totals

This module produces Report objects that contain:
- Period totals (income, expenses by bucket, credits, transfers)
- Transaction list with classifications
- Integrity assessment
- Version info for reproducibility
"""
import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import date, datetime
from typing import Optional

from .reporting_models import (
    TransactionType,
    TransferStatus,
    SpendingBucket,
    IntegrityFlag,
    PendingStatus,
    ClassificationReason,
    ClassifiedTransaction,
    PeriodTotals,
    IntegrityReport,
    Report,
)
from .dates import period_bounds, period_label, TimePeriod
from .classifier import (
    classify_transaction,
    OverrideRegistry,
    MerchantPattern,
)
from .transfer_pairing import detect_transfer_pairs
from .refund_matching import detect_refund_matches
from .categorize import categorize_merchant


# Version identifiers for reproducibility
CLASSIFIER_VERSION = "1.0.0"
REPORT_VERSION = "1.0.0"


def report_period(
    conn: sqlite3.Connection,
    start_date: date,
    end_date: date,
    include_pending: bool = False,
    account_filter: Optional[list[str]] = None,
) -> Report:
    """
    Generate a canonical report for a date range.

    This is the ONLY function that should be used to generate reports.
    Web, CLI, and exports must all call this function.

    Args:
        conn: Database connection
        start_date: Start of period (inclusive)
        end_date: End of period (exclusive)
        include_pending: Whether to include pending transactions
        account_filter: Optional list of account IDs to filter
            - None: All accounts
            - []: Empty list returns empty report with EMPTY_ACCOUNT_FILTER flag
            - list: Filter to specified accounts only

    Returns:
        Report object with totals, transactions, and integrity info
    """
    # Handle empty account filter explicitly
    if account_filter is not None and len(account_filter) == 0:
        return _empty_report(start_date, end_date)

    # Load overrides
    override_registry = OverrideRegistry()
    override_registry.load_from_db(conn)

    # Load category overrides (merchant_norm -> category_id)
    from . import db as dbmod
    category_overrides = dbmod.get_category_overrides(conn)

    # Detect transfer pairs
    transfer_result = detect_transfer_pairs(conn, start_date, end_date)
    paired_fps = transfer_result.get_paired_fingerprints()

    # Detect refund matches
    refund_result = detect_refund_matches(conn, start_date, end_date)
    refund_fps = refund_result.get_matched_fingerprints()

    # Get account types for CC detection
    cc_accounts = _get_cc_account_ids(conn)

    # First, count pending transactions (separate query for accurate count)
    pending_params: list = [start_date.isoformat(), end_date.isoformat()]
    account_clause = ""
    if account_filter:
        placeholders = ",".join("?" * len(account_filter))
        account_clause = f"AND t.account_id IN ({placeholders})"
        pending_params.extend(account_filter)

    pending_count_row = conn.execute(
        f"""
        SELECT COUNT(*) as cnt FROM transactions t
        WHERE t.posted_at >= ? AND t.posted_at < ?
          AND COALESCE(t.pending, 0) = 1
          {account_clause}
        """,
        pending_params,
    ).fetchone()
    pending_count = pending_count_row["cnt"] if pending_count_row else 0

    # Build main query
    pending_clause = "" if include_pending else "AND COALESCE(t.pending, 0) = 0"
    params: list = [start_date.isoformat(), end_date.isoformat()]
    if account_filter:
        params.extend(account_filter)

    rows = conn.execute(
        f"""
        SELECT
            t.fingerprint,
            t.account_id,
            t.posted_at,
            t.amount_cents,
            COALESCE(t.pending, 0) AS pending,
            TRIM(LOWER(COALESCE(NULLIF(t.merchant,''), NULLIF(t.description,''), ''))) AS merchant_norm,
            COALESCE(t.description, t.merchant, '') AS raw_description
        FROM transactions t
        WHERE t.posted_at >= ? AND t.posted_at < ?
          {pending_clause}
          {account_clause}
        ORDER BY t.posted_at DESC
        """,
        params,
    ).fetchall()

    # Classify and tally
    totals = PeriodTotals()
    transactions: list[ClassifiedTransaction] = []

    integrity_flags: list[IntegrityFlag] = []
    unclassified_credit_count = 0
    unclassified_credit_cents = 0

    for row in rows:
        fp = row["fingerprint"]
        amount = row["amount_cents"]
        merchant = row["merchant_norm"]
        is_pending = row["pending"] == 1
        posted = datetime.fromisoformat(row["posted_at"]).date()
        is_cc = row["account_id"] in cc_accounts

        if is_pending and not include_pending:
            continue

        # Check if paired/matched
        is_transfer_paired = fp in paired_fps
        matched_refund_of = refund_result.get_expense_for_refund(fp)

        # Classify
        result = classify_transaction(
            amount_cents=amount,
            merchant_norm=merchant,
            is_credit_card_account=is_cc,
            override_registry=override_registry,
            fingerprint=fp,
            is_transfer_paired=is_transfer_paired,
            matched_refund_of=matched_refund_of,
        )

        # Determine category for expenses
        # Manual overrides take precedence over rule-based categorization
        category_id = None
        if result.txn_type in (TransactionType.EXPENSE, TransactionType.REFUND):
            if merchant in category_overrides:
                category_id = category_overrides[merchant]
            else:
                cat_id, _ = categorize_merchant(merchant, row["raw_description"])
                category_id = cat_id

        # Build classified transaction
        txn = ClassifiedTransaction(
            fingerprint=fp,
            account_id=row["account_id"],
            posted_at=posted,
            amount_cents=amount,
            merchant_norm=merchant,
            raw_description=row["raw_description"],
            txn_type=result.txn_type,
            spending_bucket=result.spending_bucket,
            category_id=category_id,
            pending_status=PendingStatus.PENDING if is_pending else PendingStatus.POSTED,
            reason=result.reason,
        )

        # Set transfer metadata properly with enum types and group IDs
        if is_transfer_paired:
            txn.transfer_group_id = transfer_result.get_pair_id(fp)
            txn.transfer_status = TransferStatus.MATCHED
        elif result.txn_type == TransactionType.TRANSFER:
            # Unmatched transfer leg
            txn.transfer_status = TransferStatus.UNMATCHED

        transactions.append(txn)

        # Tally by type
        if result.txn_type == TransactionType.INCOME:
            totals.income_cents += amount

        elif result.txn_type == TransactionType.EXPENSE:
            abs_amount = abs(amount)
            if result.spending_bucket == SpendingBucket.FIXED_OBLIGATIONS:
                totals.fixed_obligations_cents += abs_amount
            elif result.spending_bucket == SpendingBucket.VARIABLE_ESSENTIALS:
                totals.variable_essentials_cents += abs_amount
            elif result.spending_bucket == SpendingBucket.ONE_OFFS:
                totals.one_offs_cents += abs_amount
            else:  # DISCRETIONARY default
                totals.discretionary_cents += abs_amount

        elif result.txn_type == TransactionType.TRANSFER:
            if amount > 0:
                totals.transfers_in_cents += amount
            else:
                totals.transfers_out_cents += abs(amount)

        elif result.txn_type == TransactionType.REFUND:
            totals.refunds_cents += amount

        elif result.txn_type == TransactionType.CREDIT_OTHER:
            totals.credits_other_cents += amount
            unclassified_credit_count += 1
            unclassified_credit_cents += amount

    # Build integrity report
    if unclassified_credit_count > 0:
        integrity_flags.append(IntegrityFlag.UNCLASSIFIED_CREDIT)

    if transfer_result.has_unmatched:
        integrity_flags.append(IntegrityFlag.UNMATCHED_TRANSFER)

    integrity = IntegrityReport(
        flags=integrity_flags,
        unmatched_transfer_count=len(transfer_result.unmatched_outflows) + len(transfer_result.unmatched_inflows),
        unclassified_credit_count=unclassified_credit_count,
        unclassified_credit_cents=unclassified_credit_cents,
    )

    # Generate period label
    label = f"{start_date.isoformat()} to {end_date.isoformat()}"

    # Build report
    report = Report(
        period_label=label,
        start_date=start_date,
        end_date=end_date,
        totals=totals,
        transactions=transactions,
        integrity=integrity,
        classifier_version=CLASSIFIER_VERSION,
        report_version=REPORT_VERSION,
        transaction_count=len(transactions),
        pending_count=pending_count,
    )

    # Generate report hash for reproducibility
    report.report_hash = _compute_report_hash(report)

    return report


def report_month(
    conn: sqlite3.Connection,
    year: int,
    month: int,
    include_pending: bool = False,
    account_filter: Optional[list[str]] = None,
) -> Report:
    """Generate report for a specific month."""
    ref = date(year, month, 15)
    start, end = period_bounds(TimePeriod.MONTH, ref)

    report = report_period(conn, start, end, include_pending, account_filter)
    report.period_label = period_label(TimePeriod.MONTH, start)

    return report


def report_this_month(
    conn: sqlite3.Connection,
    include_pending: bool = False,
    account_filter: Optional[list[str]] = None,
) -> Report:
    """Generate report for current month."""
    today = date.today()
    return report_month(conn, today.year, today.month, include_pending, account_filter)


def _get_cc_account_ids(conn: sqlite3.Connection) -> set[str]:
    """Get account IDs that are credit cards."""
    rows = conn.execute(
        "SELECT account_id FROM accounts WHERE LOWER(type) LIKE '%credit%'"
    ).fetchall()
    return {r["account_id"] for r in rows}


def _empty_report(start_date: date, end_date: date) -> Report:
    """Create an empty report for empty account filter."""
    return Report(
        period_label=f"{start_date.isoformat()} to {end_date.isoformat()}",
        start_date=start_date,
        end_date=end_date,
        totals=PeriodTotals(),
        transactions=[],
        integrity=IntegrityReport(
            flags=[IntegrityFlag.EMPTY_ACCOUNT_FILTER],
            unmatched_transfer_count=0,
            unclassified_credit_count=0,
            unclassified_credit_cents=0,
        ),
        classifier_version=CLASSIFIER_VERSION,
        report_version=REPORT_VERSION,
        transaction_count=0,
        pending_count=0,
        report_hash="",
    )


def _compute_report_hash(report: Report) -> str:
    """Compute deterministic hash of report for reproducibility."""
    # Create a canonical JSON representation
    data = {
        "start_date": report.start_date.isoformat(),
        "end_date": report.end_date.isoformat(),
        "totals": {
            "income_cents": report.totals.income_cents,
            "fixed_obligations_cents": report.totals.fixed_obligations_cents,
            "variable_essentials_cents": report.totals.variable_essentials_cents,
            "discretionary_cents": report.totals.discretionary_cents,
            "one_offs_cents": report.totals.one_offs_cents,
            "refunds_cents": report.totals.refunds_cents,
            "credits_other_cents": report.totals.credits_other_cents,
            "transfers_in_cents": report.totals.transfers_in_cents,
            "transfers_out_cents": report.totals.transfers_out_cents,
        },
        "transaction_count": report.transaction_count,
        "classifier_version": report.classifier_version,
        "report_version": report.report_version,
    }

    canonical = json.dumps(data, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
