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
from .analysis import TimePeriod, analyze_periods, get_current_period
from .categorize import CATEGORIES, get_category_breakdown
from .classify import detect_alerts, detect_duplicates, detect_sketchy, get_subscriptions, get_bills, detect_cross_account_duplicates, detect_price_changes
from .config import Config, load_config
from .models import Account
from .normalize import normalize_simplefin_txn
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


class DuplicateDismissRequest(BaseModel):
    merchant: str
    dismiss: bool  # True to dismiss, False to restore warning


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
    from .analysis import analyze_custom_range
    from calendar import monthrange

    # Parse account filter - "none" means show no data (early return)
    # None or [] = all accounts, non-empty list = filter to those accounts
    account_filter: list[str] | None = None
    show_no_data = False
    if accounts:
        if accounts.lower() == "none":
            show_no_data = True  # Causes early return below
        else:
            account_filter = [a.strip() for a in accounts.split(",") if a.strip()]

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
        })

    # Compute date range based on period type
    today = date.today()
    custom_start = None
    custom_end = None
    custom_analysis = None

    if start_date and end_date:
        # Explicit date range provided
        try:
            custom_start = date.fromisoformat(start_date)
            custom_end = date.fromisoformat(end_date)
            custom_analysis = analyze_custom_range(conn, custom_start, custom_end, account_filter=account_filter)
        except ValueError:
            pass  # Invalid dates, ignore
    elif period == "this_month":
        # Current calendar month (1st of month through today)
        custom_start = date(today.year, today.month, 1)
        custom_end = today
        custom_analysis = analyze_custom_range(conn, custom_start, custom_end, account_filter=account_filter)
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
        custom_analysis = analyze_custom_range(conn, custom_start, custom_end, account_filter=account_filter)

    # Get period analysis for historical comparison (use MONTH for trend data)
    period_type = _period_type_from_str("month")
    periods = analyze_periods(conn, period_type, num_periods=6, avg_window=3, account_filter=account_filter)
    current_period = custom_analysis if custom_analysis else (periods[0] if periods else None)

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

    # Get category breakdown for current period
    category_breakdown = []
    if current_period:
        category_breakdown = get_category_breakdown(
            conn,
            current_period.start_date.isoformat(),
            current_period.end_date.isoformat(),
            account_filter=account_filter,
        )

    # Detect if viewing expense-only accounts (all credit cards)
    expense_only_view = _are_expense_only_accounts(all_accounts, account_filter)

    # Count pending transactions in current period
    pending_count = 0
    if current_period:
        # Convert inclusive end to exclusive (add 1 day)
        end_exclusive = (current_period.end_date + timedelta(days=1)).isoformat()
        query = """
            SELECT COUNT(*) FROM transactions
            WHERE posted_at >= ? AND posted_at < ? AND pending = 1
        """
        params = [current_period.start_date.isoformat(), end_exclusive]
        if account_filter:
            placeholders = ",".join("?" * len(account_filter))
            query += f" AND account_id IN ({placeholders})"
            params.extend(account_filter)
        pending_count = conn.execute(query, params).fetchone()[0]

    # Detect subscription price changes
    price_changes = detect_price_changes(conn, days=180, account_filter=account_filter)

    # Serialize periods for Chart.js
    periods_json = [
        {
            "period_label": p.period_label,
            "income_cents": p.income_cents,
            "credit_cents": p.credit_cents,  # Refunds, rewards, adjustments
            "recurring_cents": p.recurring_cents,
            "discretionary_cents": p.discretionary_cents,
            "net_cents": p.net_cents,
        }
        for p in periods
    ] if periods else []

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
    })


@app.post("/api/alert-action")
def alert_action(
    req: AlertActionRequest,
    conn: sqlite3.Connection = Depends(get_db),
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
    account_filter: list[str] | None = None
    if accounts and accounts.lower() != "none":
        account_filter = [a.strip() for a in accounts.split(",") if a.strip()]

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
    account_filter: list[str] | None = None
    if accounts and accounts.lower() != "none":
        account_filter = [a.strip() for a in accounts.split(",") if a.strip()]

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

    This re-runs the same classification logic used in analysis to ensure
    the drill-down matches exactly what the user sees in the totals.
    """
    from .analysis import _is_credit_card_account, _is_cc_payment_expense, _is_income_transfer
    from .classify import _detect_patterns, _is_transfer

    valid_types = {"income", "recurring", "discretionary", "transfer"}
    if txn_type not in valid_types:
        return JSONResponse(status_code=400, content={"error": f"Invalid type. Must be one of: {', '.join(valid_types)}"})

    # Parse account filter
    account_filter: list[str] | None = None
    if accounts and accounts.lower() != "none":
        account_filter = [a.strip() for a in accounts.split(",") if a.strip()]

    # Get user-marked income rules
    income_sources, excluded_sources = dbmod.get_income_rules(conn)

    # Detect patterns for recurring classification
    patterns = _detect_patterns(conn, lookback_days=800)

    # Get account info to determine account types
    account_types: dict[str, tuple[bool, str]] = {}  # account_id -> (is_credit_card, name)
    for acc in conn.execute("SELECT account_id, name FROM accounts").fetchall():
        account_types[acc["account_id"]] = (_is_credit_card_account(acc["name"]), acc["name"])

    # Convert inclusive end to exclusive (add 1 day)
    end_exclusive = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()

    # Build query (end-exclusive internally)
    query = """
        SELECT
            t.posted_at,
            t.account_id,
            t.amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(t.merchant,''), NULLIF(t.description,''), ''))) AS merchant_norm,
            COALESCE(t.description, t.merchant, '') AS raw_description,
            COALESCE(t.pending, 0) AS pending
        FROM transactions t
        WHERE t.posted_at >= ? AND t.posted_at < ?
    """
    params: list = [start_date, end_exclusive]

    if account_filter:
        placeholders = ",".join("?" * len(account_filter))
        query += f" AND t.account_id IN ({placeholders})"
        params.extend(account_filter)

    query += " ORDER BY t.posted_at DESC"
    rows = conn.execute(query, params).fetchall()

    # Classify each transaction and collect matching ones
    results = []
    for r in rows:
        amount = r["amount_cents"]
        merchant_norm = r["merchant_norm"]
        account_id = r["account_id"]
        is_cc, account_name = account_types.get(account_id, (False, "Unknown"))
        pattern = patterns.get(merchant_norm)

        is_user_marked_income = any(src in merchant_norm for src in income_sources)
        is_user_excluded = any(src in merchant_norm for src in excluded_sources)

        # Classify this transaction
        classified_type = None
        if amount > 0:
            if is_cc:
                classified_type = "transfer"
            elif is_user_excluded:
                classified_type = "transfer"
            elif is_user_marked_income:
                classified_type = "income"
            elif _is_income_transfer(merchant_norm):
                classified_type = "transfer"
            else:
                classified_type = "income"
        else:
            abs_amount = abs(amount)
            if not is_cc and _is_cc_payment_expense(merchant_norm):
                classified_type = "transfer"
            elif pattern and pattern.is_transfer:
                classified_type = "transfer"
            elif _is_transfer(merchant_norm):
                classified_type = "transfer"
            elif pattern and pattern.is_recurring:
                classified_type = "recurring"
            else:
                classified_type = "discretionary"

        # Include if it matches the requested type
        if classified_type == txn_type:
            results.append({
                "date": r["posted_at"][:10],
                "amount_cents": amount,
                "merchant": merchant_norm,
                "description": r["raw_description"],
                "account_name": account_name,
                "pending": bool(r["pending"]),
            })

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
    # Parse account filter - "none" means show no data (early return)
    # None or [] = all accounts, non-empty list = filter to those accounts
    account_filter: list[str] | None = None
    show_no_data = False
    if accounts:
        if accounts.lower() == "none":
            show_no_data = True  # Causes early return below
        else:
            account_filter = [a.strip() for a in accounts.split(",") if a.strip()]

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
    from .classify import _match_known_subscription

    since = (date.today() - timedelta(days=days)).isoformat()

    # Parse account filter
    account_filter: list[str] | None = None
    if accounts and accounts.lower() != "none":
        account_filter = [a.strip() for a in accounts.split(",") if a.strip()]

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
    """Export period summary as CSV."""
    period_type = _period_type_from_str(period)
    periods = analyze_periods(conn, period_type, num_periods=12, avg_window=3)

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
def api_sync():
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

            return JSONResponse(content={
                "status": "ok",
                "accounts": len(accounts),
                "fetched": fetched,
                "inserted": inserted,
                "updated": updated,
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
# Entrypoint
# ---------------------------------------------------------------------------
def main():
    uvicorn.run("fin.web:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
