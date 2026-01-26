# web.py
import csv
import html
import io
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Generator

import uvicorn
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import db as dbmod
from .dates import TimePeriod  # Use canonical TimePeriod from dates module
from .categorize import CATEGORIES
from .legacy_classify import detect_alerts, detect_duplicates, detect_sketchy, get_subscriptions, get_bills, detect_cross_account_duplicates, detect_price_changes
# Detection utilities allowed; totals functions (analyze_periods) have been migrated to ReportService
from .report_service import ReportService
from .view_models import PeriodViewModel, reports_to_json, compute_period_trends, category_breakdown_from_report
from .config import Config, load_config
from .models import Account
from .normalize import normalize_simplefin_txn
from .reconciliation import (
    reconcile_account,
    save_reconciliation,
    resolve_reconciliation,
    get_reconciliation_history,
    get_pending_reconciliations,
    analyze_reconciliation_patterns,
    get_missing_transaction_candidates,
)
from .audit import (
    get_audit_log,
    get_entity_history,
    export_report_snapshot,
    get_version_info,
    AuditEventType,
)
from .planner import (
    analyze_spending_buckets,
    get_bucket_detail,
    project_monthly_budget,
)
from .projections import (
    project_cash_flow,
    detect_cash_flow_alerts,
)
from .cache import get_cache_stats, invalidate_pattern_cache, invalidate_report_cache
from .reporting_models import SpendingBucket
from .security import verify_auth_token
from .close_books import (
    close_period,
    get_closed_period,
    get_all_closed_periods,
    get_pending_adjustments,
    acknowledge_adjustment,
    get_adjustment_summary,
    check_for_adjustments_on_ingest,
    save_statement_match,
)
from .simplefin_client import SimpleFinClient


class AlertActionRequest(BaseModel):
    alert_key: str
    action: str  # "ack", "not_suspicious", "confirmed", "canceled"


class IncomeSourceRequest(BaseModel):
    merchant: str
    is_income: bool  # True to mark as income, False to unmark


class TypeOverrideRequest(BaseModel):
    merchant: str
    override_type: str  # "subscription", "bill", or "auto"


class CategoryOverrideRequest(BaseModel):
    merchant: str
    category_id: str  # Category ID like "healthcare", "shopping", or "auto" to reset


class ClosePeriodRequest(BaseModel):
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD
    account_filter: list[str] | None = None
    notes: str | None = None


class AcknowledgeAdjustmentRequest(BaseModel):
    notes: str | None = None


class StatementMatchRequest(BaseModel):
    fingerprint: str
    statement_date: str | None = None  # YYYY-MM-DD
    statement_amount_cents: int | None = None
    statement_description: str | None = None


class DuplicateDismissRequest(BaseModel):
    merchant: str
    dismiss: bool  # True to dismiss, False to restore warning


class TxnTypeOverrideRequest(BaseModel):
    """Request to override transaction classification type."""
    fingerprint: str | None = None  # Specific transaction
    merchant_pattern: str | None = None  # Merchant pattern match
    target_type: str  # INCOME, EXPENSE, TRANSFER, REFUND, CREDIT_OTHER
    reason: str | None = None  # Optional user note


class ReconcileRequest(BaseModel):
    """Request to reconcile an account against a statement."""
    account_id: str
    statement_date: str  # YYYY-MM-DD
    statement_balance: str  # Dollar amount (can be negative)


class ResolveReconciliationRequest(BaseModel):
    """Request to mark a reconciliation as resolved."""
    account_id: str
    statement_date: str  # YYYY-MM-DD
    notes: str | None = None


def parse_account_filter(accounts: str | None) -> tuple[list[str] | None, bool]:
    """
    Parse accounts query parameter into account_filter.

    Returns:
        (account_filter, show_no_data)
        - account_filter=None: all accounts
        - account_filter=[...]: filter to these accounts
        - show_no_data=True: explicit "no accounts" mode (accounts="none")

    CONTRACT:
        - accounts=None, "", "," → None (all accounts)
        - accounts="none" → None + show_no_data=True
        - accounts="acc1,acc2" → ["acc1", "acc2"]
        - Empty list after parsing → None (not [])
    """
    if not accounts:
        return None, False

    if accounts.lower() == "none":
        return None, True  # Explicit no-data mode

    # Parse comma-separated, strip whitespace, filter empty
    parsed = [a.strip() for a in accounts.split(",") if a.strip()]

    # Empty list after parsing → treat as None (all accounts)
    if not parsed:
        return None, False

    return parsed, False


app = FastAPI()

# Setup Jinja2 templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# ---------------------------------------------------------------------------
# Startup: initialize config and database once
# ---------------------------------------------------------------------------
_config: Config | None = None
_db_initialized: bool = False


def _get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _ensure_db_initialized(conn: sqlite3.Connection) -> None:
    global _db_initialized
    if not _db_initialized:
        dbmod.init_db(conn)
        _db_initialized = True


# ---------------------------------------------------------------------------
# Dependency: database connection with automatic cleanup
# ---------------------------------------------------------------------------
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    FastAPI dependency that provides a database connection.
    Automatically closes the connection when the request completes.

    Uses check_same_thread=False because FastAPI/uvicorn uses thread pools
    for sync endpoint functions.
    """
    cfg = _get_config()
    conn = dbmod.connect(cfg.db_path, check_same_thread=False)
    _ensure_db_initialized(conn)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_credit_card_account(account_name: str) -> bool:
    """Detect if an account is a credit card based on name."""
    name_lower = account_name.lower()
    cc_keywords = [
        "visa", "mastercard", "amex", "american express", "discover",
        "credit card", "rewards", "freedom", "sapphire", "platinum",
        "signature", "rapid rewards", "prime rewards"
    ]
    # Exclude checking/savings even if they have "rewards"
    if any(kw in name_lower for kw in ["checking", "savings", "deposit"]):
        return False
    return any(kw in name_lower for kw in cc_keywords)


def _are_expense_only_accounts(accounts: list, account_filter: list[str] | None) -> bool:
    """
    Check if the selected accounts are all expense-only (credit cards).
    Returns True if ALL selected accounts are credit cards with no income potential.
    """
    if not accounts:
        return False

    # If no filter, we're viewing all accounts - not expense-only
    if account_filter is None:
        return False

    # Get the selected accounts
    selected = [a for a in accounts if a["account_id"] in account_filter]

    if not selected:
        return False

    # Check if all selected accounts are credit cards
    return all(_is_credit_card_account(a["name"]) for a in selected)


def _rows_to_table(rows, cols) -> str:
    th = "".join([f"<th>{html.escape(str(c))}</th>" for c in cols])
    trs = []
    for r in rows:
        tds = "".join([f"<td>{html.escape(str(r[c])) if r[c] is not None else ''}</td>" for c in cols])
        trs.append(f"<tr>{tds}</tr>")
    return f"<table border='1' cellspacing='0' cellpadding='6'><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"


def _period_type_from_str(s: str) -> TimePeriod:
    """Convert string to TimePeriod enum."""
    mapping = {
        "month": TimePeriod.MONTH,
        "quarter": TimePeriod.QUARTER,
        "year": TimePeriod.YEAR,
    }
    return mapping.get(s.lower(), TimePeriod.MONTH)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/")
def home():
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    period: str = Query("this_month", description="Period type: this_month, last_month"),
    start_date: str | None = Query(None, description="Custom start date (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="Custom end date (YYYY-MM-DD)"),
    accounts: str | None = Query(None, description="Comma-separated account IDs to filter"),
    show_dismissed: bool = Query(False, description="Show dismissed alerts"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Main dashboard with financial health overview."""
    from calendar import monthrange

    # Parse account filter using normalized helper
    account_filter, show_no_data = parse_account_filter(accounts)

    # Get all accounts for the filter UI
    all_accounts = conn.execute(
        """
        SELECT account_id, name, institution, type
        FROM accounts
        ORDER BY institution, name
        """
    ).fetchall()

    # If no data mode, return empty state
    if show_no_data:
        return templates.TemplateResponse("dashboard_v2.html", {
            "request": request,
            "period_type": period,
            "current_period": None,
            "periods": [],
            "alerts": [],
            "total_alerts": 0,
            "duplicates": [],
            "cross_account_dups": [],
            "subscriptions": [],
            "bills": [],
            "price_changes": [],
            "show_dismissed": show_dismissed,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "income_breakdown": [],
            "income_sources": set(),
            "excluded_sources": set(),
            "category_breakdown": [],
            "categories": CATEGORIES,
            "all_accounts": all_accounts,
            "selected_accounts": [],
            "show_no_data": True,
            "expense_only_view": False,
            "pending_count": 0,
            "today": date.today(),
            "timedelta": timedelta,
            "closed_period": None,
            "pending_adjustments_count": 0,
        })

    # Initialize ReportService - THE canonical source of truth
    report_service = ReportService(conn)

    # Compute date range based on period type
    today = date.today()
    custom_start = None
    custom_end = None
    current_period = None
    current_report = None  # Track the Report for category breakdown

    if start_date and end_date:
        # Explicit date range provided
        try:
            custom_start = date.fromisoformat(start_date)
            custom_end = date.fromisoformat(end_date)
            # end_date is inclusive in UI, convert to exclusive for report_period
            current_report = report_service.report_period(
                custom_start, custom_end + timedelta(days=1),
                account_filter=account_filter
            )
            current_period = PeriodViewModel.from_report(current_report)
        except ValueError:
            pass  # Invalid dates, ignore
    elif period == "this_month":
        # Current calendar month (1st of month through today)
        custom_start = date(today.year, today.month, 1)
        custom_end = today
        # end_date is inclusive, add 1 day for exclusive
        current_report = report_service.report_period(
            custom_start, custom_end + timedelta(days=1),
            account_filter=account_filter
        )
        current_period = PeriodViewModel.from_report(current_report)
    elif period == "last_month":
        # Previous complete calendar month
        if today.month == 1:
            last_month_year = today.year - 1
            last_month = 12
        else:
            last_month_year = today.year
            last_month = today.month - 1
        custom_start = date(last_month_year, last_month, 1)
        _, last_day = monthrange(last_month_year, last_month)
        custom_end = date(last_month_year, last_month, last_day)
        current_report = report_service.report_period(
            custom_start, custom_end + timedelta(days=1),
            account_filter=account_filter
        )
        current_period = PeriodViewModel.from_report(current_report)

    # Get period analysis for historical comparison (use MONTH for trend data)
    # Using ReportService for canonical numbers
    reports = report_service.report_periods(
        TimePeriod.MONTH, num_periods=6, account_filter=account_filter
    )
    periods_json = reports_to_json(reports)

    # Use current_period from custom range, or first historical period
    if current_period is None and reports:
        current_report = reports[0]
        current_period = PeriodViewModel.from_report(current_report)

    # Get alerts (sketchy charges) - fetch enough history to cover any selected range
    all_alerts = detect_sketchy(conn, days=400, account_filter=account_filter)

    # Get alert actions to filter dismissed ones
    alert_actions = dbmod.get_alert_actions(conn)

    # Determine date range for filtering alerts
    if current_period:
        alert_start = current_period.start_date
        alert_end = current_period.end_date
    else:
        # Default to last 60 days if no period
        alert_end = date.today()
        alert_start = alert_end - timedelta(days=60)

    # Generate alert keys and filter by date range
    alerts_with_keys = []
    for alert in all_alerts:
        # Filter by date range
        if alert.posted_at < alert_start or alert.posted_at > alert_end:
            continue
        # Create unique key: pattern_type|merchant|amount|date
        # Using | as delimiter because merchant names may contain colons (e.g., "BEST:BUY")
        alert_key = f"{alert.pattern_type}|{alert.merchant_norm}|{alert.amount_cents}|{alert.posted_at.isoformat()}"
        action = alert_actions.get(alert_key)
        # Skip any actioned alerts unless show_dismissed is True
        # Actions include: ack, not_suspicious, confirmed, canceled
        if action and not show_dismissed:
            continue
        alerts_with_keys.append({
            "alert": alert,
            "key": alert_key,
            "action": action,
        })

    # Get duplicate subscriptions
    duplicates = detect_duplicates(conn, days=400, account_filter=account_filter)

    # Get cross-account duplicates (potential double-counted transactions)
    cross_account_dups = detect_cross_account_duplicates(conn, days=60, account_filter=account_filter)

    # Get all subscriptions with duplicate flags
    subscriptions = get_subscriptions(conn, days=400, account_filter=account_filter)

    # Get utility bills (separate from subscriptions)
    bills = get_bills(conn, days=400, account_filter=account_filter)

    # Get income breakdown for the current period
    income_breakdown = []
    if current_period and hasattr(current_period, 'income_items'):
        income_breakdown = current_period.income_items or []

    # Get income rules for UI state
    income_sources, excluded_sources = dbmod.get_income_rules(conn)

    # Get category breakdown from canonical Report (not separate DB query)
    category_breakdown = []
    if current_report:
        category_breakdown = category_breakdown_from_report(current_report)

    # Detect if viewing expense-only accounts (all credit cards)
    expense_only_view = _are_expense_only_accounts(all_accounts, account_filter)

    # Count pending transactions in current period
    # Note: current_period.end_date is already EXCLUSIVE (from Report), no +1 needed
    pending_count = 0
    if current_period:
        query = """
            SELECT COUNT(*) FROM transactions
            WHERE posted_at >= ? AND posted_at < ? AND pending = 1
        """
        params = [current_period.start_date.isoformat(), current_period.end_date.isoformat()]
        if account_filter:
            placeholders = ",".join("?" * len(account_filter))
            query += f" AND account_id IN ({placeholders})"
            params.extend(account_filter)
        pending_count = conn.execute(query, params).fetchone()[0]

    # Detect subscription price changes
    price_changes = detect_price_changes(conn, days=180, account_filter=account_filter)

    # periods_json already computed above using ReportService

    # Check if current period is closed (for close-the-books UI)
    closed_period = None
    pending_adjustments_count = 0
    if current_period:
        # current_period.end_date is already exclusive (from Report)
        closed_period = get_closed_period(
            conn,
            current_period.start_date,
            current_period.end_date,  # Already exclusive
            account_filter=account_filter,
        )
        # Get all pending adjustments (across all closed periods)
        all_pending = get_pending_adjustments(conn)
        pending_adjustments_count = len(all_pending)

    return templates.TemplateResponse("dashboard_v2.html", {
        "request": request,
        "period_type": period,
        "current_period": current_period,
        "periods": periods_json,
        "alerts": alerts_with_keys,
        "total_alerts": len(alerts_with_keys),
        "duplicates": duplicates,
        "cross_account_dups": cross_account_dups,
        "subscriptions": subscriptions,
        "bills": bills,
        "price_changes": price_changes,
        "show_dismissed": show_dismissed,
        "start_date": start_date or "",
        "end_date": end_date or "",
        "income_breakdown": income_breakdown,
        "income_sources": income_sources,
        "excluded_sources": excluded_sources,
        "category_breakdown": category_breakdown,
        "categories": CATEGORIES,
        "all_accounts": all_accounts,
        "selected_accounts": account_filter or [],
        "show_no_data": False,
        "expense_only_view": expense_only_view,
        "pending_count": pending_count,
        "today": date.today(),
        "timedelta": timedelta,
        # Close-the-books context
        "closed_period": closed_period,
        "pending_adjustments_count": pending_adjustments_count,
    })


@app.post("/api/alert-action")
def alert_action(
    req: AlertActionRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Record an action on an alert and learn from it."""
    valid_actions = {"ack", "not_suspicious", "confirmed", "canceled"}
    if req.action not in valid_actions:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid action. Must be one of: {', '.join(valid_actions)}"},
        )

    # Parse alert key to extract merchant and pattern type
    # Format: pattern_type|merchant|amount|date (using | because merchant may contain colons)
    parts = req.alert_key.split("|", 3)
    pattern_type = parts[0] if len(parts) > 0 else None
    merchant_norm = parts[1] if len(parts) > 1 else None

    dbmod.save_alert_action(
        conn,
        alert_key=req.alert_key,
        action=req.action,
        merchant_norm=merchant_norm,
        pattern_type=pattern_type,
    )

    # Learn from the action to suppress similar future alerts
    if merchant_norm and pattern_type and req.action in ("not_suspicious", "confirmed"):
        dbmod.learn_from_alert_action(conn, merchant_norm, pattern_type, req.action)

    return JSONResponse(content={"status": "ok", "alert_key": req.alert_key, "action": req.action})


@app.post("/api/income-source")
def income_source(
    req: IncomeSourceRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Mark a merchant as income or not-income."""
    dbmod.mark_income_source(conn, req.merchant, req.is_income)
    return JSONResponse(content={"status": "ok", "merchant": req.merchant, "is_income": req.is_income})


@app.get("/api/income-sources")
def get_income_sources(conn: sqlite3.Connection = Depends(get_db)):
    """Get list of merchants marked as income sources."""
    sources = dbmod.get_income_sources(conn)
    return JSONResponse(content={"sources": list(sources)})


@app.post("/api/type-override")
def set_type_override(
    req: TypeOverrideRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Set manual type override for a recurring charge (subscription vs bill vs ignore)."""
    valid_types = {"subscription", "bill", "ignore", "auto"}
    if req.override_type not in valid_types:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid type. Must be one of: {', '.join(valid_types)}"},
        )

    dbmod.set_recurring_type_override(conn, req.merchant, req.override_type)
    return JSONResponse(content={
        "status": "ok",
        "merchant": req.merchant,
        "override_type": req.override_type,
    })


@app.post("/api/category-override")
def set_category_override(
    req: CategoryOverrideRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Set manual category override for a merchant."""
    # Validate category_id (allow "auto" to reset)
    if req.category_id != "auto" and req.category_id not in CATEGORIES:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid category. Must be one of: {', '.join(CATEGORIES.keys())}, or 'auto'"},
        )

    dbmod.set_category_override(conn, req.merchant, req.category_id)
    return JSONResponse(content={
        "status": "ok",
        "merchant": req.merchant,
        "category_id": req.category_id,
    })


@app.post("/api/dismiss-duplicate")
def dismiss_duplicate(
    req: DuplicateDismissRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Dismiss or restore duplicate warning for a merchant."""
    if req.dismiss:
        dbmod.dismiss_duplicate(conn, req.merchant)
    else:
        dbmod.undismiss_duplicate(conn, req.merchant)
    return JSONResponse(content={
        "status": "ok",
        "merchant": req.merchant,
        "dismissed": req.dismiss,
    })


@app.post("/api/txn-type-override")
def set_txn_type_override(
    req: TxnTypeOverrideRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """
    Set transaction type override.

    Override classification for a specific transaction (by fingerprint) or
    all transactions matching a merchant pattern.

    target_type: INCOME, EXPENSE, TRANSFER, REFUND, CREDIT_OTHER
    """
    valid_types = {"INCOME", "EXPENSE", "TRANSFER", "REFUND", "CREDIT_OTHER"}
    if req.target_type not in valid_types:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid target_type. Must be one of: {', '.join(valid_types)}"},
        )

    if not req.fingerprint and not req.merchant_pattern:
        return JSONResponse(
            status_code=400,
            content={"error": "Must specify either fingerprint or merchant_pattern"},
        )

    if req.fingerprint and req.merchant_pattern:
        return JSONResponse(
            status_code=400,
            content={"error": "Specify either fingerprint or merchant_pattern, not both"},
        )

    if req.fingerprint:
        dbmod.set_txn_type_override_fingerprint(
            conn, req.fingerprint, req.target_type, req.reason
        )
        return JSONResponse(content={
            "status": "ok",
            "fingerprint": req.fingerprint,
            "target_type": req.target_type,
        })
    else:
        dbmod.set_txn_type_override_merchant(
            conn, req.merchant_pattern, req.target_type, req.reason
        )
        return JSONResponse(content={
            "status": "ok",
            "merchant_pattern": req.merchant_pattern,
            "target_type": req.target_type,
        })


@app.delete("/api/txn-type-override")
def remove_txn_type_override_endpoint(
    fingerprint: str | None = None,
    merchant_pattern: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Remove a transaction type override."""
    if not fingerprint and not merchant_pattern:
        return JSONResponse(
            status_code=400,
            content={"error": "Must specify either fingerprint or merchant_pattern"},
        )

    dbmod.remove_txn_type_override(conn, fingerprint, merchant_pattern)
    return JSONResponse(content={"status": "ok"})


@app.get("/api/txn-type-overrides")
def get_txn_type_overrides_endpoint(conn: sqlite3.Connection = Depends(get_db)):
    """Get all transaction type overrides."""
    fp_overrides, merchant_overrides = dbmod.get_txn_type_overrides(conn)
    return JSONResponse(content={
        "fingerprint_overrides": [
            {"fingerprint": fp, "target_type": t}
            for fp, t in fp_overrides.items()
        ],
        "merchant_overrides": [
            {"merchant_pattern": p, "target_type": t}
            for p, t in merchant_overrides.items()
        ],
    })


@app.get("/api/categories")
def list_categories():
    """Get list of available categories."""
    return JSONResponse(content={
        "categories": [
            {"id": cat_id, "name": cat.name, "icon": cat.icon}
            for cat_id, cat in CATEGORIES.items()
        ]
    })


@app.get("/api/accounts")
def list_accounts(conn: sqlite3.Connection = Depends(get_db)):
    """Get list of all accounts with their names."""
    rows = conn.execute(
        """
        SELECT account_id, name, institution, type
        FROM accounts
        ORDER BY institution, name
        """
    ).fetchall()
    return JSONResponse(content={
        "accounts": [
            {
                "account_id": r["account_id"],
                "name": r["name"],
                "institution": r["institution"],
                "type": r["type"],
            }
            for r in rows
        ]
    })


@app.get("/api/search")
def search_transactions(
    q: str = Query(..., description="Search query"),
    accounts: str | None = Query(None, description="Comma-separated account IDs for primary results"),
    days: int = Query(365, description="Days to search back"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Search transactions by merchant or description.

    Returns:
    - matches: Transactions matching the query in selected accounts
    - other_matches_count: Number of matches in other accounts (for discovery)
    - other_matches_preview: Sample of matches in other accounts
    """
    search_term = q.strip().lower()
    if len(search_term) < 2:
        return JSONResponse(content={
            "matches": [],
            "other_matches_count": 0,
            "other_matches_preview": [],
        })

    # Parse account filter
    account_filter, _ = parse_account_filter(accounts)

    # Get all accounts for name lookup
    all_accounts = {
        r["account_id"]: r["name"]
        for r in conn.execute("SELECT account_id, name FROM accounts").fetchall()
    }
    all_account_ids = set(all_accounts.keys())

    # Search query
    cutoff_date = (date.today() - timedelta(days=days)).isoformat()
    query = """
        SELECT
            t.id,
            t.account_id,
            t.posted_at,
            t.amount_cents,
            COALESCE(t.merchant, '') AS merchant,
            COALESCE(t.description, '') AS description,
            COALESCE(t.pending, 0) AS pending,
            a.name AS account_name
        FROM transactions t
        JOIN accounts a ON t.account_id = a.account_id
        WHERE t.posted_at >= ?
          AND (LOWER(t.merchant) LIKE ? OR LOWER(t.description) LIKE ?)
        ORDER BY t.posted_at DESC
        LIMIT 200
    """
    like_pattern = f"%{search_term}%"
    rows = conn.execute(query, (cutoff_date, like_pattern, like_pattern)).fetchall()

    # Split results into selected accounts vs other accounts
    selected_matches = []
    other_matches = []

    for r in rows:
        txn = {
            "id": r["id"],
            "date": r["posted_at"][:10],
            "amount_cents": r["amount_cents"],
            "merchant": r["merchant"],
            "description": r["description"],
            "account_id": r["account_id"],
            "account_name": r["account_name"],
            "pending": bool(r["pending"]),
        }

        # If no filter, all are "selected"
        if account_filter is None:
            selected_matches.append(txn)
        elif r["account_id"] in account_filter:
            selected_matches.append(txn)
        else:
            other_matches.append(txn)

    # Check if results were truncated by the database LIMIT
    db_limit = 200
    is_truncated = len(rows) >= db_limit

    return JSONResponse(content={
        "query": q,
        "matches": selected_matches[:50],  # Limit primary results
        "matches_count": len(selected_matches),
        "other_matches_count": len(other_matches),
        "other_matches_preview": other_matches[:5],  # Preview of other matches
        "is_truncated": is_truncated,  # True if there may be more results beyond the limit
        "result_limit": db_limit,
    })


@app.get("/api/category/{category_id}")
def get_category_transactions(
    category_id: str,
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
    accounts: str | None = Query(None, description="Comma-separated account IDs to filter"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Get transactions for a specific category with full drill-down including account names."""
    from .categorize import CATEGORIES

    if category_id not in CATEGORIES:
        return JSONResponse(status_code=404, content={"error": "Category not found"})

    # Parse account filter
    account_filter, _ = parse_account_filter(accounts)

    # Get category overrides to respect manual user choices
    category_overrides = dbmod.get_category_overrides(conn)

    # Build query with optional account filter
    sql = """
        SELECT
            t.posted_at,
            t.amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(t.merchant,''), NULLIF(t.description,''), ''))) AS merchant_norm,
            COALESCE(t.description, t.merchant, '') AS raw_description,
            COALESCE(a.name, t.account_id, 'Unknown') AS account_name
        FROM transactions t
        LEFT JOIN accounts a ON t.account_id = a.account_id
        WHERE t.posted_at >= ? AND t.posted_at < ?
          AND t.amount_cents < 0
    """
    params: list = [start_date, end_date]

    if account_filter:
        placeholders = ",".join("?" * len(account_filter))
        sql += f" AND t.account_id IN ({placeholders})"
        params.extend(account_filter)

    sql += " ORDER BY t.posted_at DESC"
    rows = conn.execute(sql, params).fetchall()

    # Categorize and filter (respecting manual overrides)
    from .categorize import categorize_merchant

    by_merchant: dict[str, list] = {}
    for row in rows:
        merchant = row["merchant_norm"]

        # Check for manual override first, then fall back to auto-categorization
        override = category_overrides.get(merchant.lower())
        if override and override in CATEGORIES:
            cat_id = override
        else:
            cat_id, _ = categorize_merchant(merchant)

        if cat_id != category_id:
            continue

        if merchant not in by_merchant:
            by_merchant[merchant] = []
        by_merchant[merchant].append({
            "date": row["posted_at"],
            "amount_cents": abs(row["amount_cents"]),
            "description": row["raw_description"],
            "account": row["account_name"],
        })

    # Build response with ALL transactions visible
    merchants = []
    for merchant, txns in by_merchant.items():
        total = sum(t["amount_cents"] for t in txns)
        merchants.append({
            "merchant": merchant,
            "total_cents": total,
            "count": len(txns),
            "transactions": sorted(txns, key=lambda x: x["date"], reverse=True),  # ALL txns
        })

    merchants.sort(key=lambda x: -x["total_cents"])

    category = CATEGORIES[category_id]
    return JSONResponse(content={
        "category_id": category_id,
        "category_name": category.name,
        "category_icon": category.icon,
        "total_cents": sum(m["total_cents"] for m in merchants),
        "transaction_count": sum(m["count"] for m in merchants),
        "merchants": merchants,  # ALL merchants
    })


@app.get("/api/transactions-by-type")
def get_transactions_by_type(
    txn_type: str = Query(..., description="Type: income, recurring, discretionary, transfer"),
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
    accounts: str | None = Query(None, description="Comma-separated account IDs to filter"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Get transactions classified as a specific type for drill-down.

    Uses canonical ReportService to ensure drill-down matches totals exactly.
    """
    from .reporting_models import TransactionType, SpendingBucket

    valid_types = {"income", "recurring", "discretionary", "transfer"}
    if txn_type not in valid_types:
        return JSONResponse(status_code=400, content={"error": f"Invalid type. Must be one of: {', '.join(valid_types)}"})

    # Parse account filter
    account_filter, _ = parse_account_filter(accounts)

    # Get account names for display
    account_names: dict[str, str] = {}
    for acc in conn.execute("SELECT account_id, name FROM accounts").fetchall():
        account_names[acc["account_id"]] = acc["name"]

    # Use canonical ReportService for transactions
    # end_date is inclusive in UI, add 1 day for exclusive
    end_exclusive = date.fromisoformat(end_date) + timedelta(days=1)
    report = ReportService(conn).report_period(
        date.fromisoformat(start_date),
        end_exclusive,
        include_pending=False,
        account_filter=account_filter,
    )

    # Filter transactions by requested type using canonical classification
    results = []
    for txn in report.transactions:
        # Map UI type names to canonical TransactionType/SpendingBucket
        matches = False
        if txn_type == "income":
            matches = txn.txn_type == TransactionType.INCOME
        elif txn_type == "recurring":
            matches = (
                txn.txn_type == TransactionType.EXPENSE and
                txn.spending_bucket == SpendingBucket.FIXED_OBLIGATIONS
            )
        elif txn_type == "discretionary":
            matches = (
                txn.txn_type == TransactionType.EXPENSE and
                txn.spending_bucket in (
                    SpendingBucket.VARIABLE_ESSENTIALS,
                    SpendingBucket.DISCRETIONARY,
                    SpendingBucket.ONE_OFFS,
                )
            )
        elif txn_type == "transfer":
            matches = txn.txn_type == TransactionType.TRANSFER

        if matches:
            results.append({
                "date": txn.posted_at.isoformat(),
                "amount_cents": txn.amount_cents,
                "merchant": txn.merchant_norm,
                "description": txn.raw_description,
                "account_name": account_names.get(txn.account_id, "Unknown"),
                "pending": txn.pending_status.value == "pending",
                "bucket": txn.spending_bucket.value if txn.spending_bucket else None,
                "category_id": txn.category_id,
            })

    # Sort by date descending
    results.sort(key=lambda x: x["date"], reverse=True)
    total_cents = sum(r["amount_cents"] for r in results)

    return JSONResponse(content={
        "type": txn_type,
        "start_date": start_date,
        "end_date": end_date,
        "total_cents": total_cents,
        "transaction_count": len(results),
        "transactions": results,
    })


@app.get("/subs", response_class=HTMLResponse)
def subs(
    request: Request,
    days: int = 400,
    accounts: str | None = Query(None, description="Comma-separated account IDs to filter"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Recurring charges page with styled layout and drill-down."""
    # Parse account filter using normalized helper
    account_filter, show_no_data = parse_account_filter(accounts)

    # Get all accounts for the filter UI
    all_accounts = conn.execute(
        """
        SELECT account_id, name, institution, type
        FROM accounts
        ORDER BY institution, name
        """
    ).fetchall()

    # If no data mode, return empty state
    if show_no_data:
        return templates.TemplateResponse("subs.html", {
            "request": request,
            "subscriptions": [],
            "bills": [],
            "duplicates": [],
            "days": days,
            "all_accounts": all_accounts,
            "selected_accounts": [],
            "show_no_data": True,
        })

    # Get subscriptions and bills
    subscriptions = get_subscriptions(conn, days=days, account_filter=account_filter)
    bills = get_bills(conn, days=days, account_filter=account_filter)

    # Get duplicates for flagging
    duplicates = detect_duplicates(conn, days=days, account_filter=account_filter)

    return templates.TemplateResponse("subs.html", {
        "request": request,
        "subscriptions": subscriptions,
        "bills": bills,
        "duplicates": duplicates,
        "days": days,
        "all_accounts": all_accounts,
        "selected_accounts": account_filter or [],
        "show_no_data": False,
    })


@app.get("/api/payee/{payee_norm:path}")
def get_payee_transactions(
    payee_norm: str,
    days: int = Query(400, description="Lookback days"),
    accounts: str | None = Query(None, description="Comma-separated account IDs to filter"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Get transactions for a specific payee (drill-down) with enhanced details."""
    from .legacy_classify import _match_known_subscription

    since = (date.today() - timedelta(days=days)).isoformat()

    # Parse account filter
    account_filter, _ = parse_account_filter(accounts)

    # Build query with optional account filter - includes provenance fields
    sql = """
        SELECT
            t.posted_at,
            t.amount_cents,
            COALESCE(t.merchant, '') AS bank_merchant,
            COALESCE(t.description, '') AS bank_description,
            COALESCE(a.name, t.account_id, 'Unknown') AS account_name,
            t.created_at,
            t.updated_at
        FROM transactions t
        LEFT JOIN accounts a ON t.account_id = a.account_id
        WHERE TRIM(LOWER(COALESCE(NULLIF(t.merchant,''), NULLIF(t.description,''), ''))) = ?
          AND t.posted_at >= ?
    """
    params: list = [payee_norm.lower(), since]

    if account_filter:
        placeholders = ",".join("?" * len(account_filter))
        sql += f" AND t.account_id IN ({placeholders})"
        params.extend(account_filter)

    sql += " ORDER BY t.posted_at DESC"
    rows = conn.execute(sql, params).fetchall()

    transactions = []
    for row in rows:
        # Check if transaction was updated after creation
        was_updated = row["updated_at"] and row["created_at"] and row["updated_at"] > row["created_at"]
        transactions.append({
            "date": row["posted_at"],
            "amount_cents": row["amount_cents"],
            "bank_merchant": row["bank_merchant"],
            "bank_description": row["bank_description"],
            "account": row["account_name"],
            "first_seen": row["created_at"][:10] if row["created_at"] else None,
            "last_updated": row["updated_at"][:10] if was_updated else None,
        })

    total_cents = sum(abs(t["amount_cents"]) for t in transactions if t["amount_cents"] < 0)

    # Check if this is a known subscription service
    known_match = _match_known_subscription(payee_norm)
    is_known_service = known_match is not None
    display_name = known_match[0] if known_match else None

    return JSONResponse(content={
        "payee": payee_norm,
        "display_name": display_name,
        "is_known_service": is_known_service,
        "transaction_count": len(transactions),
        "total_cents": total_cents,
        "transactions": transactions,
    })


@app.get("/watchlist", response_class=HTMLResponse)
def watchlist():
    path = Path("/app/exports/watchlist.csv")
    if not path.exists():
        return HTMLResponse("<h3>Watchlist</h3><p>No watchlist.csv found.</p><p><a href='/'>home</a></p>")

    rows = []
    with path.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)

    if not rows:
        return HTMLResponse("<h3>Watchlist</h3><p>Empty.</p><p><a href='/'>home</a></p>")

    cols = list(rows[0].keys())
    return HTMLResponse("<h3>Watchlist</h3>" + _rows_to_table(rows, cols) + "<p><a href='/'>home</a></p>")


@app.get("/anomalies", response_class=HTMLResponse)
def anomalies(days: int = 60, limit: int = 50, conn: sqlite3.Connection = Depends(get_db)):
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT
          posted_at,
          TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), ''))) AS payee_norm,
          amount_cents
        FROM transactions
        WHERE posted_at >= ?
        ORDER BY ABS(amount_cents) DESC
        LIMIT ?
        """,
        (since, limit),
    ).fetchall()
    cols = ["posted_at", "payee_norm", "amount_cents"]
    return HTMLResponse("<h3>Largest transactions (proxy anomalies)</h3>" + _rows_to_table(rows, cols) + "<p><a href='/'>home</a></p>")


# ---------------------------------------------------------------------------
# CSV Export Routes
# ---------------------------------------------------------------------------
from .normalize import sanitize_csv_field as _sanitize_csv_field


@app.get("/export/sketchy")
def export_sketchy(conn: sqlite3.Connection = Depends(get_db)):
    """Export sketchy charges as CSV."""
    alerts = detect_sketchy(conn, days=60)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["posted_at", "merchant", "amount_usd", "pattern_type", "severity", "detail"])

    for alert in alerts:
        writer.writerow([
            alert.posted_at.isoformat(),
            _sanitize_csv_field(alert.merchant_norm),
            f"{alert.amount_cents / 100:.2f}",
            alert.pattern_type,
            alert.severity,
            _sanitize_csv_field(alert.detail),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sketchy_charges.csv"},
    )


@app.get("/export/duplicates")
def export_duplicates(conn: sqlite3.Connection = Depends(get_db)):
    """Export duplicate subscriptions as CSV."""
    duplicates = detect_duplicates(conn, days=400)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["group_type", "merchants", "monthly_total_usd", "severity", "detail"])

    for dup in duplicates:
        writer.writerow([
            dup.group_type,
            _sanitize_csv_field("; ".join(dup.merchants)),
            f"{dup.total_monthly_cents / 100:.2f}",
            dup.severity,
            _sanitize_csv_field(dup.detail),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=duplicates.csv"},
    )


@app.get("/export/subscriptions")
def export_subscriptions(conn: sqlite3.Connection = Depends(get_db)):
    """Export all subscriptions as CSV."""
    subscriptions = get_subscriptions(conn, days=400)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["merchant", "monthly_usd", "cadence", "first_seen", "last_seen", "is_duplicate"])

    for sub in subscriptions:
        writer.writerow([
            _sanitize_csv_field(sub[0]),  # merchant
            f"{sub[1] / 100:.2f}",  # monthly_cents
            sub[2],  # cadence
            sub[3].isoformat(),  # first_seen
            sub[4].isoformat(),  # last_seen
            "yes" if sub[5] else "no",  # is_duplicate
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=subscriptions.csv"},
    )


@app.get("/export/summary")
def export_summary(
    period: str = Query("month", description="Period type: month, quarter, year"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Export period summary as CSV - using canonical ReportService."""
    period_type = _period_type_from_str(period)

    # Use canonical ReportService for all totals
    service = ReportService(conn)
    reports = service.report_periods(period_type, num_periods=12)
    periods = compute_period_trends(reports, avg_window=3)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "period", "start_date", "end_date",
        "income_usd", "recurring_usd", "discretionary_usd", "net_usd",
        "avg_income_usd", "avg_recurring_usd", "avg_discretionary_usd",
        "income_trend", "recurring_trend", "discretionary_trend",
        "transaction_count",
    ])

    for p in periods:
        writer.writerow([
            _sanitize_csv_field(p.period_label),
            p.start_date.isoformat(),
            p.end_date.isoformat(),
            f"{p.income_cents / 100:.2f}",
            f"{p.recurring_cents / 100:.2f}",
            f"{p.discretionary_cents / 100:.2f}",
            f"{p.net_cents / 100:.2f}",
            f"{p.avg_income_cents / 100:.2f}",
            f"{p.avg_recurring_cents / 100:.2f}",
            f"{p.avg_discretionary_cents / 100:.2f}",
            p.income_trend,
            p.recurring_trend,
            p.discretionary_trend,
            p.transaction_count,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={period}_summary.csv"},
    )


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------
@app.post("/api/sync")
def api_sync(_auth: bool = Depends(verify_auth_token)):
    """
    Sync all available data from SimpleFIN.

    Simple, trustworthy sync:
    - Always fetches ALL available data (banks typically provide 90-180 days)
    - Upserts everything: new transactions added, existing updated
    - Append-only by design: sync operations never delete records
    - Idempotent: run it 100 times, same result

    No date ranges to think about. No modes. Just sync.
    """
    cfg = load_config()

    if not cfg.simplefin_access_url:
        return JSONResponse(
            status_code=400,
            content={"error": "SimpleFIN not configured. Set SIMPLEFIN_ACCESS_URL environment variable."},
        )

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    try:
        client = SimpleFinClient(cfg)
        try:
            # Fetch accounts (metadata only)
            raw = client.fetch_accounts()
            raw_accounts = raw.get("accounts", [])

            accounts = []
            for ra in raw_accounts:
                accounts.append(
                    Account(
                        account_id=str(ra.get("id")),
                        institution=str(ra.get("org", {}).get("name", "UNKNOWN")),
                        name=str(ra.get("name", "UNKNOWN")),
                        type=ra.get("type"),
                        currency=ra.get("currency", "USD"),
                    )
                )
            dbmod.upsert_accounts(conn, accounts)

            # Fetch ALL available transactions (SimpleFIN returns what the bank has)
            acctset = client.fetch_all_available()
            acct_list = acctset.get("accounts", [])
            tx_by_account = {str(a.get("id")): (a.get("transactions") or []) for a in acct_list}

            fetched = 0
            normalized = []
            for a in accounts:
                raw_txns = tx_by_account.get(a.account_id, [])
                fetched += len(raw_txns)
                for rt in raw_txns:
                    normalized.append(normalize_simplefin_txn(rt, a.account_id))

            inserted, updated = dbmod.upsert_transactions(conn, normalized)

            # Record sync run for audit log
            dbmod.record_run(conn, 730, fetched, inserted, updated)  # 730 = "all available"

            # Check for post-close adjustments (transactions that landed in closed periods)
            adjustment_results = check_for_adjustments_on_ingest(conn)
            adjustment_count = sum(len(adj) for adj in adjustment_results.values())

            return JSONResponse(content={
                "status": "ok",
                "accounts": len(accounts),
                "fetched": fetched,
                "inserted": inserted,
                "updated": updated,
                "post_close_adjustments": adjustment_count,
                "affected_periods": len(adjustment_results),
            })
        finally:
            client.close()
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )
    finally:
        conn.close()


@app.get("/api/sync-status")
def get_sync_status(conn: sqlite3.Connection = Depends(get_db)):
    """Get the last sync status and data range."""
    # Get last sync info
    last_run = conn.execute(
        "SELECT ran_at, lookback_days, txns_fetched, txns_inserted, txns_updated FROM runs ORDER BY ran_at DESC LIMIT 1"
    ).fetchone()

    # Get transaction date range
    date_range = conn.execute(
        "SELECT MIN(posted_at) as earliest, MAX(posted_at) as latest, COUNT(*) as total FROM transactions"
    ).fetchone()

    if not last_run:
        return JSONResponse(content={
            "has_synced": False,
            "last_sync": None,
            "data_range": None,
        })

    return JSONResponse(content={
        "has_synced": True,
        "last_sync": {
            "timestamp": last_run["ran_at"],
            "lookback_days": last_run["lookback_days"],
            "fetched": last_run["txns_fetched"],
            "inserted": last_run["txns_inserted"],
            "updated": last_run["txns_updated"],
        },
        "data_range": {
            "earliest": date_range["earliest"],
            "latest": date_range["latest"],
            "total_transactions": date_range["total"],
        } if date_range["earliest"] else None,
    })


@app.get("/api/sync-history")
def get_sync_history(
    limit: int = Query(20, description="Number of recent syncs to return"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Get sync audit log - history of all sync runs."""
    runs = conn.execute(
        """
        SELECT id, ran_at, lookback_days, txns_fetched, txns_inserted, txns_updated
        FROM runs
        ORDER BY ran_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    history = []
    for run in runs:
        history.append({
            "id": run["id"],
            "ran_at": run["ran_at"],
            "fetched": run["txns_fetched"],
            "inserted": run["txns_inserted"],
            "updated": run["txns_updated"],
        })

    return JSONResponse(content={"syncs": history})


# ---------------------------------------------------------------------------
# Reconciliation API
# ---------------------------------------------------------------------------
@app.post("/api/reconcile")
def api_reconcile(
    req: ReconcileRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """
    Reconcile an account against a statement balance.

    Compare the statement ending balance against calculated balance from
    stored transactions. Saves the reconciliation event for audit.
    """
    from .money import parse_to_cents

    try:
        stmt_date = date.fromisoformat(req.statement_date)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid date format. Use YYYY-MM-DD."},
        )

    try:
        stmt_balance_cents = parse_to_cents(req.statement_balance)
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid balance: {e}"},
        )

    # Verify account exists
    acct = conn.execute(
        "SELECT account_id, name FROM accounts WHERE account_id = ?",
        (req.account_id,),
    ).fetchone()
    if not acct:
        return JSONResponse(
            status_code=404,
            content={"error": f"Account not found: {req.account_id}"},
        )

    # Compute reconciliation
    result = reconcile_account(conn, req.account_id, stmt_date, stmt_balance_cents)

    # Save to database
    event = save_reconciliation(conn, result)

    return JSONResponse(content={
        "status": "ok",
        "account_name": result.account_name,
        "statement_date": result.statement_date.isoformat(),
        "statement_balance_cents": result.statement_balance_cents,
        "calculated_balance_cents": result.calculated_balance_cents,
        "delta_cents": result.delta_cents,
        "delta_direction": result.delta_direction,
        "is_matched": result.is_matched,
        "transaction_count": result.transaction_count,
        "reconciliation_status": event.status.value,
    })


@app.post("/api/reconcile/resolve")
def api_resolve_reconciliation(
    req: ResolveReconciliationRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Mark a reconciliation discrepancy as resolved."""
    try:
        stmt_date = date.fromisoformat(req.statement_date)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid date format. Use YYYY-MM-DD."},
        )

    resolve_reconciliation(conn, req.account_id, stmt_date, req.notes)

    return JSONResponse(content={
        "status": "ok",
        "account_id": req.account_id,
        "statement_date": req.statement_date,
    })


@app.get("/api/reconcile/history")
def api_reconciliation_history(
    account_id: str | None = Query(None, description="Filter by account"),
    limit: int = Query(50, description="Number of records to return"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Get reconciliation history."""
    events = get_reconciliation_history(conn, account_id, limit)

    return JSONResponse(content={
        "reconciliations": [
            {
                "id": e.id,
                "account_id": e.account_id,
                "statement_date": e.statement_date.isoformat(),
                "statement_balance_cents": e.statement_balance_cents,
                "calculated_balance_cents": e.calculated_balance_cents,
                "delta_cents": e.delta_cents,
                "status": e.status.value,
                "notes": e.notes,
                "created_at": e.created_at.isoformat(),
                "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
            }
            for e in events
        ]
    })


@app.get("/api/reconcile/pending")
def api_pending_reconciliations(conn: sqlite3.Connection = Depends(get_db)):
    """Get all unresolved reconciliation discrepancies."""
    events = get_pending_reconciliations(conn)

    return JSONResponse(content={
        "pending_count": len(events),
        "total_delta_cents": sum(abs(e.delta_cents) for e in events),
        "discrepancies": [
            {
                "id": e.id,
                "account_id": e.account_id,
                "statement_date": e.statement_date.isoformat(),
                "delta_cents": e.delta_cents,
                "notes": e.notes,
            }
            for e in events
        ]
    })


@app.get("/api/reconcile/insights")
def api_reconciliation_insights(conn: sqlite3.Connection = Depends(get_db)):
    """
    Analyze reconciliation history for patterns and improvement suggestions.

    Returns detected patterns (consistent deltas, growing deltas) and
    actionable suggestions to improve data accuracy.
    """
    insights = analyze_reconciliation_patterns(conn)

    return JSONResponse(content={
        "accounts_with_issues": insights.accounts_with_issues,
        "total_unresolved_delta_cents": insights.total_unresolved_delta_cents,
        "suggestions": insights.suggestions,
        "patterns": [
            {
                "account_id": p.account_id,
                "account_name": p.account_name,
                "pattern_type": p.pattern_type,
                "avg_delta_cents": p.avg_delta_cents,
                "delta_count": p.delta_count,
                "confidence": p.confidence,
                "suggestion": p.suggestion,
            }
            for p in insights.patterns
        ],
    })


@app.get("/api/reconcile/candidates")
def api_reconciliation_candidates(
    account_id: str = Query(..., description="Account ID"),
    statement_date: str = Query(..., description="Statement date YYYY-MM-DD"),
    delta_cents: int = Query(..., description="Delta to explain"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Find transactions that might explain a reconciliation delta.

    Useful for investigating discrepancies by finding transactions
    with amounts close to the delta.
    """
    try:
        stmt_date = date.fromisoformat(statement_date)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid date format. Use YYYY-MM-DD."},
        )

    candidates = get_missing_transaction_candidates(
        conn, account_id, stmt_date, delta_cents
    )

    return JSONResponse(content={
        "account_id": account_id,
        "statement_date": statement_date,
        "delta_cents": delta_cents,
        "candidates": candidates,
    })


# ---------------------------------------------------------------------------
# Audit / Versioning API
# ---------------------------------------------------------------------------
@app.get("/api/audit")
def api_audit_log(
    entity_type: str | None = Query(None, description="Filter by entity type"),
    entity_id: str | None = Query(None, description="Filter by entity ID"),
    event_type: str | None = Query(None, description="Filter by event type"),
    limit: int = Query(100, description="Max events to return"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Get audit log with optional filters."""
    evt_type = None
    if event_type:
        try:
            evt_type = AuditEventType(event_type)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": f"Invalid event_type. Valid types: {[e.value for e in AuditEventType]}"},
            )

    events = get_audit_log(
        conn,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=evt_type,
        limit=limit,
    )

    return JSONResponse(content={
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type.value,
                "timestamp": e.timestamp.isoformat(),
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "old_value": e.old_value,
                "new_value": e.new_value,
                "metadata": e.metadata,
                "classifier_version": e.classifier_version,
                "report_version": e.report_version,
            }
            for e in events
        ],
        "count": len(events),
    })


@app.get("/api/audit/entity/{entity_type}/{entity_id:path}")
def api_entity_history(
    entity_type: str,
    entity_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Get complete audit history for a specific entity."""
    events = get_entity_history(conn, entity_type, entity_id)

    return JSONResponse(content={
        "entity_type": entity_type,
        "entity_id": entity_id,
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type.value,
                "timestamp": e.timestamp.isoformat(),
                "old_value": e.old_value,
                "new_value": e.new_value,
                "metadata": e.metadata,
            }
            for e in events
        ],
    })


@app.get("/api/version")
def api_version():
    """Get classifier and report version info."""
    return JSONResponse(content=get_version_info())


@app.get("/api/cache/stats")
def api_cache_stats():
    """Get cache statistics for performance monitoring."""
    return JSONResponse(content=get_cache_stats())


@app.post("/api/cache/clear")
def api_cache_clear(_auth: bool = Depends(verify_auth_token)):
    """Clear all caches (use after data changes)."""
    invalidate_pattern_cache()
    invalidate_report_cache()
    return JSONResponse(content={"status": "ok", "message": "All caches cleared"})


@app.get("/api/report/snapshot")
def api_report_snapshot(
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Export a report snapshot for reproducibility.

    Includes report data, versions, and active overrides.
    """
    from .reporting import report_period

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid date format. Use YYYY-MM-DD."},
        )

    report = report_period(conn, start, end)
    snapshot = export_report_snapshot(conn, report)

    return JSONResponse(content=snapshot)


# ---------------------------------------------------------------------------
# Budget Planner API
# ---------------------------------------------------------------------------
@app.get("/api/planner/budget")
def api_budget_plan(
    months: int = Query(6, description="Months of history to analyze"),
    accounts: str | None = Query(None, description="Comma-separated account IDs"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Get budget plan based on spending bucket analysis.

    Analyzes historical spending by bucket (fixed obligations, variable
    essentials, discretionary, one-offs) and provides planning insights.
    """
    account_filter, _ = parse_account_filter(accounts)

    plan = analyze_spending_buckets(conn, months, account_filter)

    return JSONResponse(content={
        "period_months": plan.period_months,
        "monthly_income_cents": plan.total_monthly_income_cents,
        "monthly_spend_cents": plan.total_monthly_spend_cents,
        "monthly_net_cents": plan.net_monthly_cents,
        "savings_rate_percent": round(plan.savings_rate, 1),
        "health_score": round(plan.health_score, 2),
        "suggestions": plan.suggestions,
        "buckets": [
            {
                "bucket": b.bucket.name,
                "label": b.label,
                "description": b.description,
                "monthly_avg_cents": b.monthly_avg_cents,
                "monthly_min_cents": b.monthly_min_cents,
                "monthly_max_cents": b.monthly_max_cents,
                "trend": b.trend,
                "trend_percent": round(b.trend_percent, 1),
                "predictability": round(b.predictability, 2),
                "merchant_count": b.merchant_count,
                "transaction_count": b.transaction_count,
            }
            for b in plan.buckets
        ],
    })


@app.get("/api/planner/bucket/{bucket_name}")
def api_bucket_detail(
    bucket_name: str,
    months: int = Query(6, description="Months of history"),
    accounts: str | None = Query(None, description="Comma-separated account IDs"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Get detailed breakdown for a specific spending bucket."""
    try:
        bucket = SpendingBucket[bucket_name.upper()]
    except KeyError:
        valid = [b.name for b in SpendingBucket]
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid bucket. Valid: {valid}"},
        )

    account_filter, _ = parse_account_filter(accounts)

    detail = get_bucket_detail(conn, bucket, months, account_filter)

    return JSONResponse(content={
        "bucket": detail.bucket.name,
        "merchants": detail.merchants,
        "monthly_totals": detail.monthly_totals,
    })


@app.get("/api/planner/projection")
def api_budget_projection(
    history_months: int = Query(6, description="Months of history to base projection on"),
    forward_months: int = Query(3, description="Months to project forward"),
    accounts: str | None = Query(None, description="Comma-separated account IDs"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Project future monthly budget based on historical patterns.

    Returns projected income, expenses by bucket, and net for upcoming months.
    """
    account_filter, _ = parse_account_filter(accounts)

    projection = project_monthly_budget(conn, history_months, forward_months, account_filter)

    return JSONResponse(content=projection)


# ---------------------------------------------------------------------------
# Cash Flow Projections API
# ---------------------------------------------------------------------------
@app.get("/api/cashflow/projection")
def api_cashflow_projection(
    days: int = Query(30, description="Days to project forward"),
    accounts: str | None = Query(None, description="Comma-separated account IDs"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Project cash flow for the next N days.

    Shows expected income, upcoming charges (subscriptions, bills),
    and projected net position.
    """
    account_filter, _ = parse_account_filter(accounts)

    projection = project_cash_flow(conn, days, account_filter)

    return JSONResponse(content={
        "start_date": projection.start_date.isoformat(),
        "end_date": projection.end_date.isoformat(),
        "expected_income_cents": projection.expected_income_cents,
        "expected_fixed_cents": projection.expected_fixed_cents,
        "expected_variable_cents": projection.expected_variable_cents,
        "expected_discretionary_cents": projection.expected_discretionary_cents,
        "expected_net_cents": projection.expected_net_cents,
        "confidence": round(projection.confidence, 2),
        "upcoming_charges": [
            {
                "merchant": c.merchant,
                "display_name": c.display_name,
                "expected_date": c.expected_date.isoformat(),
                "expected_amount_cents": c.expected_amount_cents,
                "confidence": round(c.confidence, 2),
                "cadence": c.cadence,
                "bucket": c.bucket.name,
                "is_subscription": c.is_subscription,
                "last_charge_date": c.last_charge_date.isoformat() if c.last_charge_date else None,
            }
            for c in projection.upcoming_charges
        ],
    })


@app.get("/api/cashflow/alerts")
def api_cashflow_alerts(
    days: int = Query(30, description="Days to look ahead"),
    accounts: str | None = Query(None, description="Comma-separated account IDs"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Get cash flow alerts for potential issues.

    Alerts include:
    - Projected shortfalls (expenses > income)
    - Large upcoming charges
    - Multiple charges on same day
    """
    account_filter, _ = parse_account_filter(accounts)

    alerts = detect_cash_flow_alerts(conn, days, account_filter)

    return JSONResponse(content={
        "alert_count": len(alerts),
        "alerts": [
            {
                "alert_type": a.alert_type,
                "severity": a.severity,
                "date": a.date.isoformat(),
                "message": a.message,
                "amount_cents": a.amount_cents,
                "merchant": a.merchant,
            }
            for a in alerts
        ],
    })


@app.get("/sync-log", response_class=HTMLResponse)
def sync_log_page(
    conn: sqlite3.Connection = Depends(get_db),
):
    """Sync audit log page - shows history of all syncs and data provenance."""
    # Get sync history
    runs = conn.execute(
        """
        SELECT id, ran_at, txns_fetched, txns_inserted, txns_updated
        FROM runs
        ORDER BY ran_at DESC
        LIMIT 50
        """,
    ).fetchall()

    # Get data stats
    stats = conn.execute(
        """
        SELECT
            COUNT(*) as total_txns,
            MIN(posted_at) as earliest,
            MAX(posted_at) as latest,
            MIN(created_at) as first_sync,
            MAX(updated_at) as last_update
        FROM transactions
        """
    ).fetchone()

    # Get recently added transactions (last sync)
    recent_inserts = conn.execute(
        """
        SELECT
            t.posted_at,
            t.amount_cents,
            COALESCE(t.merchant, t.description, '') as payee,
            COALESCE(a.name, t.account_id) as account_name,
            t.created_at
        FROM transactions t
        LEFT JOIN accounts a ON t.account_id = a.account_id
        ORDER BY t.created_at DESC
        LIMIT 15
        """
    ).fetchall()

    # Get recently updated transactions
    recent_updates = conn.execute(
        """
        SELECT
            t.posted_at,
            t.amount_cents,
            COALESCE(t.merchant, t.description, '') as payee,
            COALESCE(a.name, t.account_id) as account_name,
            t.updated_at
        FROM transactions t
        LEFT JOIN accounts a ON t.account_id = a.account_id
        WHERE t.updated_at > t.created_at
        ORDER BY t.updated_at DESC
        LIMIT 15
        """
    ).fetchall()

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Sync Audit Log - fin</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f7fa; margin: 0; padding: 20px; }}
            .container {{ max-width: 1000px; margin: 0 auto; }}
            h1 {{ color: #1a1f36; }}
            .back-link {{ color: #2563eb; text-decoration: none; }}
            .card {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
            .card h2 {{ margin-top: 0; font-size: 16px; color: #525f7f; text-transform: uppercase; letter-spacing: 0.5px; }}
            .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; }}
            .stat {{ text-align: center; }}
            .stat-value {{ font-size: 24px; font-weight: 600; color: #1a1f36; }}
            .stat-label {{ font-size: 12px; color: #8792a2; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
            th {{ text-align: left; padding: 8px; border-bottom: 2px solid #e1e5eb; color: #525f7f; font-weight: 500; }}
            td {{ padding: 8px; border-bottom: 1px solid #e1e5eb; }}
            .positive {{ color: #0e6245; }}
            .negative {{ color: #c53030; }}
            .muted {{ color: #8792a2; font-size: 11px; }}
            .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }}
            .badge-new {{ background: #c6f6d5; color: #0e6245; }}
            .badge-updated {{ background: #feebc8; color: #b7791f; }}
            .trust-banner {{ background: #e6fffa; border: 1px solid #38b2ac; border-radius: 8px; padding: 16px; margin-bottom: 20px; }}
            .trust-banner h3 {{ margin: 0 0 8px 0; color: #234e52; font-size: 14px; }}
            .trust-banner p {{ margin: 0; color: #285e61; font-size: 13px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <p><a href="/dashboard" class="back-link">&larr; Back to Dashboard</a></p>
            <h1>Sync Audit Log</h1>

            <div class="trust-banner">
                <h3>Data Retention Policy</h3>
                <p>fin persists all data locally. Sync operations are <strong>append-only</strong>: new records are inserted,
                existing records may be updated, but nothing is deleted. You control your data retention.</p>
            </div>

            <div class="card">
                <h2>Data Overview</h2>
                <div class="stats">
                    <div class="stat">
                        <div class="stat-value">{stats['total_txns']:,}</div>
                        <div class="stat-label">Total Transactions</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{stats['earliest'][:10] if stats['earliest'] else 'N/A'}</div>
                        <div class="stat-label">Earliest Transaction</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{stats['latest'][:10] if stats['latest'] else 'N/A'}</div>
                        <div class="stat-label">Latest Transaction</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{len(runs)}</div>
                        <div class="stat-label">Total Syncs</div>
                    </div>
                </div>
            </div>

            <div class="card">
                <h2>Sync History</h2>
                <table>
                    <thead>
                        <tr>
                            <th>When</th>
                            <th>Fetched</th>
                            <th>New</th>
                            <th>Updated</th>
                        </tr>
                    </thead>
                    <tbody>
    """

    for run in runs:
        ran_at = run["ran_at"][:16].replace("T", " ") if run["ran_at"] else "Unknown"
        html_content += f"""
                        <tr>
                            <td>{ran_at}</td>
                            <td>{run['txns_fetched']}</td>
                            <td class="positive">+{run['txns_inserted']}</td>
                            <td>{run['txns_updated']}</td>
                        </tr>
        """

    html_content += """
                    </tbody>
                </table>
            </div>

            <div class="card">
                <h2>Recently Added Transactions</h2>
                <table>
                    <thead>
                        <tr>
                            <th>First Seen</th>
                            <th>Date</th>
                            <th>Payee</th>
                            <th>Amount</th>
                            <th>Account</th>
                        </tr>
                    </thead>
                    <tbody>
    """

    for txn in recent_inserts:
        created = txn["created_at"][:16].replace("T", " ") if txn["created_at"] else ""
        posted = txn["posted_at"][:10] if txn["posted_at"] else ""
        amount = txn["amount_cents"] / 100
        amount_class = "positive" if amount >= 0 else "negative"
        html_content += f"""
                        <tr>
                            <td class="muted">{created}</td>
                            <td>{posted}</td>
                            <td>{html.escape(txn['payee'][:40])}</td>
                            <td class="{amount_class}">${abs(amount):.2f}</td>
                            <td>{html.escape(txn['account_name'])}</td>
                        </tr>
        """

    html_content += """
                    </tbody>
                </table>
            </div>

            <div class="card">
                <h2>Recently Updated Transactions</h2>
    """

    if recent_updates:
        html_content += """
                <table>
                    <thead>
                        <tr>
                            <th>Updated</th>
                            <th>Date</th>
                            <th>Payee</th>
                            <th>Amount</th>
                            <th>Account</th>
                        </tr>
                    </thead>
                    <tbody>
        """
        for txn in recent_updates:
            updated = txn["updated_at"][:16].replace("T", " ") if txn["updated_at"] else ""
            posted = txn["posted_at"][:10] if txn["posted_at"] else ""
            amount = txn["amount_cents"] / 100
            amount_class = "positive" if amount >= 0 else "negative"
            html_content += f"""
                        <tr>
                            <td class="muted">{updated}</td>
                            <td>{posted}</td>
                            <td>{html.escape(txn['payee'][:40])}</td>
                            <td class="{amount_class}">${abs(amount):.2f}</td>
                            <td>{html.escape(txn['account_name'])}</td>
                        </tr>
            """
        html_content += """
                    </tbody>
                </table>
        """
    else:
        html_content += "<p style='color: #8792a2;'>No transactions have been updated since they were first synced.</p>"

    html_content += """
            </div>
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)


# ---------------------------------------------------------------------------
# Close-the-Books API
# ---------------------------------------------------------------------------
@app.post("/api/close-period")
def api_close_period(
    req: ClosePeriodRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """
    Close a period, creating an official snapshot of totals.

    This is the "close the books" action. Any future transactions
    landing in this period will be flagged as post-close adjustments.
    """
    try:
        start = date.fromisoformat(req.start_date)
        end = date.fromisoformat(req.end_date)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid date format. Use YYYY-MM-DD."},
        )

    period = close_period(
        conn, start, end,
        account_filter=req.account_filter,
        notes=req.notes,
    )

    return JSONResponse(content={
        "status": "ok",
        "closed_period": {
            "id": period.id,
            "start_date": period.start_date.isoformat(),
            "end_date": period.end_date.isoformat(),
            "closed_at": period.closed_at.isoformat(),
            "report_hash": period.report_hash,
            "snapshot_id": period.snapshot_id,
            "transaction_count": period.transaction_count,
            "totals": {
                "income_cents": period.income_cents,
                "expenses_cents": period.total_expenses_cents,
                "net_cents": period.net_cents,
            },
        },
    })


@app.get("/api/closed-periods")
def api_closed_periods(conn: sqlite3.Connection = Depends(get_db)):
    """List all closed periods."""
    periods = get_all_closed_periods(conn)

    return JSONResponse(content={
        "closed_periods": [
            {
                "id": p.id,
                "start_date": p.start_date.isoformat(),
                "end_date": p.end_date.isoformat(),
                "closed_at": p.closed_at.isoformat(),
                "report_hash": p.report_hash,
                "transaction_count": p.transaction_count,
                "income_cents": p.income_cents,
                "expenses_cents": p.total_expenses_cents,
                "net_cents": p.net_cents,
                "status": p.status,
            }
            for p in periods
        ]
    })


@app.get("/api/closed-period/{period_id}")
def api_closed_period_detail(
    period_id: int,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Get details of a closed period including adjustments."""
    # Find the period
    periods = get_all_closed_periods(conn)
    period = next((p for p in periods if p.id == period_id), None)

    if not period:
        return JSONResponse(
            status_code=404,
            content={"error": "Closed period not found"},
        )

    # Get adjustment summary
    summary = get_adjustment_summary(conn, period)

    return JSONResponse(content={
        "closed_period": {
            "id": period.id,
            "start_date": period.start_date.isoformat(),
            "end_date": period.end_date.isoformat(),
            "closed_at": period.closed_at.isoformat(),
            "report_hash": period.report_hash,
            "snapshot_id": period.snapshot_id,
            "transaction_count": period.transaction_count,
            "totals": {
                "income_cents": period.income_cents,
                "fixed_obligations_cents": period.fixed_obligations_cents,
                "variable_essentials_cents": period.variable_essentials_cents,
                "discretionary_cents": period.discretionary_cents,
                "one_offs_cents": period.one_offs_cents,
                "refunds_cents": period.refunds_cents,
                "transfers_in_cents": period.transfers_in_cents,
                "transfers_out_cents": period.transfers_out_cents,
                "total_expenses_cents": period.total_expenses_cents,
                "net_cents": period.net_cents,
            },
            "notes": period.notes,
        },
        "adjustments": {
            "has_pending": summary.has_pending,
            "pending_count": len(summary.pending_adjustments),
            "total_adjustment_cents": summary.total_adjustment_cents,
            "adjusted_net_cents": summary.adjusted_net_cents,
            "net_change_cents": summary.net_change_cents,
            "pending": [
                {
                    "id": a.id,
                    "fingerprint": a.fingerprint,
                    "detected_at": a.detected_at.isoformat(),
                    "adjustment_type": a.adjustment_type,
                    "posted_at": a.posted_at.isoformat() if a.posted_at else None,
                    "amount_cents": a.amount_cents,
                    "merchant_norm": a.merchant_norm,
                    "description": a.description,
                    "status": a.status,
                }
                for a in summary.pending_adjustments
            ],
        },
    })


@app.get("/api/adjustments")
def api_all_adjustments(conn: sqlite3.Connection = Depends(get_db)):
    """Get all pending post-close adjustments across all periods."""
    adjustments = get_pending_adjustments(conn)

    return JSONResponse(content={
        "pending_adjustments": [
            {
                "id": a.id,
                "closed_period_id": a.closed_period_id,
                "fingerprint": a.fingerprint,
                "detected_at": a.detected_at.isoformat(),
                "adjustment_type": a.adjustment_type,
                "posted_at": a.posted_at.isoformat() if a.posted_at else None,
                "amount_cents": a.amount_cents,
                "merchant_norm": a.merchant_norm,
                "description": a.description,
            }
            for a in adjustments
        ],
        "count": len(adjustments),
    })


@app.post("/api/adjustments/{adjustment_id}/acknowledge")
def api_acknowledge_adjustment(
    adjustment_id: int,
    req: AcknowledgeAdjustmentRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Acknowledge a post-close adjustment (user accepts it)."""
    acknowledge_adjustment(conn, adjustment_id, notes=req.notes)
    return JSONResponse(content={"status": "ok"})


@app.post("/api/statement-match")
def api_statement_match(
    req: StatementMatchRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Save a user-confirmed statement-to-transaction match."""
    stmt_date = date.fromisoformat(req.statement_date) if req.statement_date else None

    save_statement_match(
        conn,
        fingerprint=req.fingerprint,
        statement_date=stmt_date,
        statement_amount_cents=req.statement_amount_cents,
        statement_description=req.statement_description,
        confidence="user_confirmed",
    )

    return JSONResponse(content={"status": "ok"})


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main():
    uvicorn.run("fin.web:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
