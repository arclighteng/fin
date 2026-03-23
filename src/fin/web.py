# web.py
import csv
import html
import io
import secrets
import sqlite3
from decimal import Decimal
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Generator

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, File, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from . import db as dbmod
from . import dates as dates_mod
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
from .reporting_models import SpendingBucket, TransactionType
from .security import verify_auth_token, get_signed_session_token
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
from .integrity import get_resolution_summary, format_integrity_badge
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


class TxnTypeOverrideItem(BaseModel):
    """Single transaction override within bulk request."""
    fingerprint: str
    target_type: str  # INCOME, EXPENSE, TRANSFER, REFUND, CREDIT_OTHER
    reason: str | None = None  # Optional per-transaction reason


class BulkTxnTypeOverrideRequest(BaseModel):
    """Request to override multiple transactions at once."""
    overrides: list[TxnTypeOverrideItem]
    reason: str | None = None  # Optional global reason


class ReconcileRequest(BaseModel):
    """Request to reconcile an account against a statement."""
    account_id: str
    statement_date: str  # YYYY-MM-DD
    statement_balance: str  # Dollar amount (can be negative)


class BudgetTargetRequest(BaseModel):
    """Request to set a budget target for a category."""
    category_id: str
    monthly_target_cents: int


class TransactionNoteRequest(BaseModel):
    """Request to set a note on a transaction."""
    note: str


class TransactionTagRequest(BaseModel):
    """Request to add a tag to a transaction."""
    tag: str


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

# ---------------------------------------------------------------------------
# Rate limiting (Item 4)
# ---------------------------------------------------------------------------
import os as _os
_rate_limit_auth = _os.getenv("FIN_RATE_LIMIT_AUTH", "10/minute")
_rate_limit_api = _os.getenv("FIN_RATE_LIMIT_API", "200/minute")

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Setup Jinja2 templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# CSRF token and auth-disabled flag for template conditionals
from .security import get_api_token, get_csrf_token

# Ensure the session token is generated on startup so it's available for all requests
_startup_token = get_api_token()

# Register CSRF token getter as callable global
# NOTE: api_token is intentionally NOT injected into templates (Item 1 — HttpOnly cookie).
# The read-only mode check uses whether auth is disabled, not the raw token value.
templates.env.globals["csrf_token"] = get_csrf_token
# Provide a callable that tells the template if auth is active (for UI state only)
templates.env.globals["auth_enabled"] = lambda: get_api_token() is not None

# Inject package version
from importlib.metadata import version as _pkg_version, PackageNotFoundError
try:
    templates.env.globals["app_version"] = _pkg_version("finproj")
except PackageNotFoundError:
    templates.env.globals["app_version"] = "dev"

# Mount static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/health")
async def health():
    return {"status": "ok"}




# ---------------------------------------------------------------------------
# Observability: request logging with auth failure tracking
# ---------------------------------------------------------------------------
import time as _time
import logging as _logging
from collections import defaultdict as _defaultdict

_access_log = _logging.getLogger("fin.access")
_auth_failures: dict[str, int] = _defaultdict(int)


@app.middleware("http")
async def log_requests(request, call_next):
    start = _time.monotonic()
    response = await call_next(request)
    elapsed_ms = (_time.monotonic() - start) * 1000
    client_ip = request.client.host if request.client else "-"
    method = request.method
    path = request.url.path
    status = response.status_code

    if status in (401, 403):
        _auth_failures[client_ip] += 1
        total = _auth_failures[client_ip]
        _access_log.warning(
            "%s | %s %s | %d | %.0fms | auth failure #%d from %s",
            client_ip, method, path, status, elapsed_ms, total, client_ip,
        )
    elif path.startswith("/static/"):
        pass  # skip static file noise
    else:
        _access_log.info(
            "%s | %s %s | %d | %.0fms",
            client_ip, method, path, status, elapsed_ms,
        )

    return response


# ---------------------------------------------------------------------------
# Security: Content-Security-Policy header
# ---------------------------------------------------------------------------
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self' https://cdn.jsdelivr.net; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


# ---------------------------------------------------------------------------
# Security: Auth required on ALL /api/* endpoints (Item 2)
# ---------------------------------------------------------------------------
@app.middleware("http")
async def require_api_auth(request: Request, call_next):
    """
    Require authentication on every /api/* path.

    Explicit public exceptions (no auth required):
    - /auth/session  (the login endpoint itself)
    - /static/*      (served by StaticFiles — never reaches here, but guard anyway)

    Auth is checked by inspecting the Authorization header or fin_session cookie,
    matching the logic in verify_auth_token. We replicate it here in middleware form
    so we don't have to add Depends(verify_auth_token) to every single GET route.
    """
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)

    from .security import get_api_token as _get_token, _verify_token_value
    from fastapi import HTTPException as _HTTPException
    from starlette.responses import JSONResponse as _JSONResp

    required = _get_token()
    if required is None:
        # Auth disabled — permitted only on loopback (startup_security_check enforces this)
        return await call_next(request)

    # Check Authorization: Bearer header
    authorization = request.headers.get("authorization", "")
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            try:
                _verify_token_value(parts[1])
                return await call_next(request)
            except _HTTPException as exc:
                return _JSONResp(status_code=exc.status_code, content={"detail": exc.detail})
        return _JSONResp(
            status_code=401,
            content={"detail": "Invalid Authorization header format."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check fin_session cookie
    fin_session = request.cookies.get("fin_session")
    if fin_session:
        try:
            _verify_token_value(fin_session)
            return await call_next(request)
        except _HTTPException as exc:
            return _JSONResp(status_code=exc.status_code, content={"detail": exc.detail})

    # No credentials provided
    return _JSONResp(
        status_code=401,
        content={"detail": "Authentication required for API access."},
        headers={"WWW-Authenticate": "Bearer"},
    )


# ---------------------------------------------------------------------------
# Security: CSRF protection for mutation endpoints
# ---------------------------------------------------------------------------
_CSRF_BYPASS_PATHS = frozenset({"/auth/session"})


@app.middleware("http")
async def verify_csrf(request, call_next):
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        # Skip CSRF check for bootstrapping endpoints (no session exists yet)
        if request.url.path in _CSRF_BYPASS_PATHS:
            return await call_next(request)
        csrf_header = request.headers.get("x-csrf-token", "")
        expected = get_csrf_token()
        if not csrf_header or not secrets.compare_digest(csrf_header, expected):
            # Allow only if auth is fully disabled (loopback-only — startup_security_check
            # already hard-blocked the auth_disabled + non-loopback combination)
            if not get_api_token():
                return await call_next(request)
            from starlette.responses import JSONResponse as _JSONResp
            return _JSONResp(
                status_code=403,
                content={"detail": "CSRF token missing or invalid"},
            )
    return await call_next(request)




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
# Auth session model
# ---------------------------------------------------------------------------
class SessionRequest(BaseModel):
    token: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/auth/session")
@limiter.limit(_rate_limit_auth)
def create_session(req: SessionRequest, request: Request, response: Response):
    """
    Establish a browser session by validating the API token and setting an HttpOnly cookie.

    This endpoint is intentionally public (no auth dependency) — it IS the login endpoint.
    Rate-limited to prevent brute-force.
    """
    import os as _os
    from .security import _verify_token_value, get_api_token as _get_token
    from fastapi import HTTPException as _HTTPException

    required = _get_token()
    if required is None:
        # Auth disabled — issue a session cookie with a sentinel value so the
        # browser auth prompt can be suppressed.
        resp = JSONResponse(content={"status": "ok"})
        resp.set_cookie(
            key="fin_session",
            value="auth_disabled",
            httponly=True,
            secure=False,  # No TLS when auth disabled (loopback only by startup check)
            samesite="strict",
            max_age=3600 * 8,
            path="/",
        )
        return resp

    try:
        _verify_token_value(req.token)
    except _HTTPException:
        raise _HTTPException(status_code=401, detail="Invalid token")

    # Determine if we're running over TLS (controls cookie Secure flag)
    is_https = request.url.scheme == "https"

    resp = JSONResponse(content={"status": "ok"})
    resp.set_cookie(
        key="fin_session",
        value=req.token,
        httponly=True,
        secure=is_https,
        samesite="strict",
        max_age=3600 * 8,  # 8 hours
        path="/",
    )
    return resp


@app.get("/auto-login")
def auto_login(request: Request, t: str | None = None):
    """
    Browser auto-login: validate token from query param, set session cookie, redirect.

    Called by webbrowser.open() at startup. Intentionally public — it IS the auth
    endpoint for the browser. Listed in _LICENSE_BYPASS_PATHS so the license gate
    does not redirect before the cookie can be set.

    On success: sets fin_session cookie (1-year) + 302 /dashboard.
    On any failure (missing/invalid token, auth disabled): 302 /dashboard without cookie.
    """
    from .security import get_api_token as _get_token, _verify_token_value
    from fastapi import HTTPException as _HTTPEx

    if _get_token() is None:
        return RedirectResponse(url="/dashboard", status_code=302)

    if not t:
        return RedirectResponse(url="/dashboard", status_code=302)

    try:
        _verify_token_value(t)
    except _HTTPEx:
        return RedirectResponse(url="/dashboard", status_code=302)

    is_https = request.url.scheme == "https"
    resp = RedirectResponse(url="/dashboard", status_code=302)
    resp.set_cookie(
        key="fin_session",
        value=t,
        httponly=True,
        secure=is_https,
        samesite="strict",
        max_age=365 * 24 * 3600,  # 1 year — token is permanent, cookie matches
        path="/",
    )
    return resp


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

    # Detect demo/empty state.
    # is_demo: demo transactions are actually loaded (account_id starts with "demo-")
    # is_empty: no transactions at all — user has not imported any data yet
    txn_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    demo_txn_count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE account_id LIKE 'demo-%'"
    ).fetchone()[0]
    is_demo = demo_txn_count > 0
    is_empty = txn_count == 0

    # If no data mode, return empty state
    if show_no_data:
        return templates.TemplateResponse("dashboard_v2.html", {
            "request": request,
            "period_type": period,
            "current_period": None,
            "periods": [],
            "savings_rate_pct": 0,
            "avg_net_cents": 0,
            "prev_category_map": {},
            "attention_items": [],
            "subscriptions": [],
            "bills": [],
            "price_changes_by_merchant": {},
            "total_recurring_cents": 0,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "category_breakdown": [],
            "categories": CATEGORIES,
            "all_accounts": all_accounts,
            "selected_accounts": [],
            "show_no_data": True,
            "expense_only_view": False,
            "pending_count": 0,
            "today": dates_mod.today(),
            "timedelta": timedelta,
            "closed_period": None,
            "is_demo": is_demo,
            "is_empty": is_empty,
            "avg_savings_rate_pct": 0.0,
            "savings_tier": "negative",
            "pace_data": None,
        })

    # Initialize ReportService - THE canonical source of truth
    report_service = ReportService(conn)

    # Compute date range based on period type
    today = dates_mod.today()
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
    period_vms = compute_period_trends(reports)
    periods_json = [vm.to_json_dict() for vm in period_vms]

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
        alert_end = dates_mod.today()
        alert_start = alert_end - timedelta(days=60)

    # Generate alert keys and filter by date range
    alerts_with_keys = []
    for alert in all_alerts:
        # Filter by date range
        if alert.posted_at < alert_start or alert.posted_at >= alert_end:
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

    # Get all subscriptions with duplicate flags
    subscriptions = get_subscriptions(conn, days=400, account_filter=account_filter)

    # Get utility bills (separate from subscriptions)
    bills = get_bills(conn, days=400, account_filter=account_filter)

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

    # Savings rate
    savings_rate_pct = 0.0
    if current_period and current_period.income_cents > 0:
        savings_rate_pct = round(current_period.net_cents / current_period.income_cents * 100, 1)

    # 3-month average savings rate and benchmark tier for Savings Rate card
    avg_savings_rate_pct = 0.0
    if len(reports) >= 4:
        historical_rates = []
        for r in reports[1:4]:  # skip reports[0] (current period), use 3 prior months
            vm = PeriodViewModel.from_report(r)
            if vm.income_cents > 0:
                historical_rates.append(vm.net_cents / vm.income_cents * 100)
        if historical_rates:
            avg_savings_rate_pct = round(sum(historical_rates) / len(historical_rates), 1)

    savings_tier = _compute_savings_tier(savings_rate_pct)

    # Previous month category breakdown for month-over-month comparison
    prev_category_map: dict[str, int] = {}
    if len(reports) > 1:
        for cat, net_cents, count, gross, refunds in category_breakdown_from_report(reports[1]):
            prev_category_map[cat.id] = net_cents

    # 3-month average net for comparison
    avg_net_cents = 0
    if period_vms:
        vm0 = period_vms[0]
        avg_net_cents = vm0.avg_income_cents - vm0.avg_recurring_cents - vm0.avg_discretionary_cents

    # V3 DASHBOARD METRICS
    # =====================================================================
    # 1. Compute 3-month category averages from historical reports
    # This provides a stable baseline for comparison (not volatile month-to-month)
    category_averages: dict[str, int] = {}
    if len(reports) >= 4:
        # Use reports 1, 2, 3 (skip current month at index 0 for true historical avg)
        category_totals: dict[str, list[int]] = {}
        for report in reports[1:4]:
            for cat, net_cents, count, gross, refunds in category_breakdown_from_report(report):
                cat_id = cat.id
                if cat_id not in category_totals:
                    category_totals[cat_id] = []
                category_totals[cat_id].append(net_cents)

        # Compute averages
        for cat_id, amounts in category_totals.items():
            category_averages[cat_id] = sum(amounts) // len(amounts)

    # 2. Compute outlier percentages for current month categories
    # Compare current month spending to 3-month average
    category_outliers: dict[str, int] = {}
    if category_averages and current_report:
        for cat, net_cents, count, gross, refunds in category_breakdown_from_report(current_report):
            cat_id = cat.id
            avg = category_averages.get(cat_id, 0)
            if avg > 0:
                # Percentage deviation: +33% means spending 33% more than average
                pct_deviation = int((net_cents - avg) * 100 / avg)
                if pct_deviation > 0:  # Only flag above-average spending
                    category_outliers[cat_id] = pct_deviation

    # 3. Multi-month category trend detection: 3+ consecutive increases
    category_trends: list[dict] = []
    if len(reports) >= 4:
        # Check last 4 months for consecutive increases
        category_histories: dict[str, list[int]] = {}
        for i, report in enumerate(reports[:4]):
            for cat, net_cents, count, gross, refunds in category_breakdown_from_report(report):
                cat_id = cat.id
                if cat_id not in category_histories:
                    category_histories[cat_id] = []
                category_histories[cat_id].append(net_cents)

        # Detect 3+ consecutive increases (most recent to oldest)
        for cat_id, amounts in category_histories.items():
            if len(amounts) >= 3:
                # Check if each month is greater than the next (amounts[0] is current month)
                consecutive_increases = 0
                for i in range(len(amounts) - 1):
                    if amounts[i] > amounts[i + 1]:
                        consecutive_increases += 1
                    else:
                        break

                if consecutive_increases >= 3:
                    category = CATEGORIES.get(cat_id, CATEGORIES["other"])
                    historical = amounts[1:4]
                    avg_3mo = sum(historical) // len(historical) if historical else 0
                    category_trends.append({
                        "category_id": cat_id,
                        "category_name": category.name,
                        "current_amount": amounts[0],
                        "avg_3mo": avg_3mo,
                        "months_increasing": consecutive_increases,
                    })

    # 4. Bill deviation detection: >15% from rolling average
    bill_deviations: list[dict] = []
    if bills and len(reports) >= 4:
        # Build set of bill merchants (normalized)
        bill_merchants = set()
        bill_display_names = {}
        for bill_merchant, _, _, _, _, _ in bills:
            merchant_norm = bill_merchant.lower()
            bill_merchants.add(merchant_norm)
            bill_display_names[merchant_norm] = bill_merchant

        # Compute rolling average for each bill merchant over last 4 months
        bill_histories: dict[str, list[int]] = {}
        for i, report in enumerate(reports[:4]):
            for txn in report.transactions:
                if txn.txn_type == TransactionType.EXPENSE:
                    merchant = txn.merchant_norm
                    if merchant in bill_merchants:
                        if merchant not in bill_histories:
                            bill_histories[merchant] = []
                        # Store (month_index, amount) to track which month it came from
                        bill_histories[merchant].append((i, abs(txn.amount_cents)))

        # Check current month bills (index 0) against their 3-month rolling average
        checked_merchants = set()
        for merchant, history in bill_histories.items():
            # Get current month charges (index 0)
            current_charges = [amt for idx, amt in history if idx == 0]
            if not current_charges:
                continue

            # Get historical charges (indices 1, 2, 3)
            historical_charges = [amt for idx, amt in history if idx > 0]
            if len(historical_charges) < 2:  # Need at least 2 months of history
                continue

            # Use most recent charge in current month (in case of multiple bills)
            current_amount = current_charges[0]

            # Compute average of historical charges
            avg_amount = sum(historical_charges) // len(historical_charges)

            if avg_amount > 0 and merchant not in checked_merchants:
                pct_deviation = abs(current_amount - avg_amount) * 100 / avg_amount
                if pct_deviation > 15:
                    display_name = bill_display_names.get(merchant, merchant)
                    bill_deviations.append({
                        "merchant": merchant,
                        "display_name": display_name,
                        "current_amount": current_amount,
                        "avg_amount": avg_amount,
                        "pct_deviation": int(pct_deviation),
                    })
                    checked_merchants.add(merchant)

    # 5. Restructured attention_items for v3
    # REMOVE: integrity tasks, price changes, cross-account duplicates, duplicate subs
    # KEEP: suspicious/unusual charges
    # ADD: bill deviations, multi-month category trends
    # MAX: 4 items total
    attention_items: list[dict] = []

    # Add suspicious charges (from alerts)
    for item in alerts_with_keys:
        alert = item["alert"]
        # Only include genuinely suspicious items (not routine)
        if alert.pattern_type in ["new_merchant", "unusual_amount", "suspicious_pattern"]:
            attention_items.append({
                "type": "alert",
                "title": f"New charge: {alert.merchant_norm}",
                "detail": f"${abs(alert.amount_cents)/100:.2f} on {alert.posted_at.strftime('%b %d')}",
                "severity": alert.severity,
                "action_type": "alert",
                "key": item["key"],
                "actioned": item["action"],
                "drilldown_scope": f"merchant:{alert.merchant_norm}",
            })

    # Add multi-month category trends
    for trend in category_trends[:2]:  # Max 2 trend items
        attention_items.append({
            "type": "category_trend",
            "title": f"{trend['category_name']} is up over the last {trend['months_increasing']} months",
            "detail": f"Your average was ${trend['avg_3mo']/100:.0f}/mo, now ${trend['current_amount']/100:.0f}",
            "severity": "warning",
            "action_type": "dismiss",
            "key": f"trend_{trend['category_id']}",
            "drilldown_scope": f"category:{trend['category_id']}",
        })

    # Add bill deviations
    for deviation in bill_deviations[:2]:  # Max 2 bill items
        diff = abs(deviation['current_amount'] - deviation['avg_amount'])
        direction = "above" if deviation['current_amount'] > deviation['avg_amount'] else "below"
        attention_items.append({
            "type": "bill_deviation",
            "title": f"Your {deviation['display_name']} bill was ${deviation['current_amount']/100:.2f}",
            "detail": f"${diff/100:.0f} {direction} avg (${deviation['avg_amount']/100:.2f})",
            "severity": "warning",
            "action_type": "dismiss",
            "key": f"bill_{deviation['merchant']}",
            "drilldown_scope": f"merchant:{deviation['merchant']}",
        })

    # Limit to 4 items total
    attention_items = attention_items[:4]

    # 6. Price changes (keep for inline display in Commitments card, not in attention_items)
    price_changes_by_merchant: dict[str, dict] = {}
    for pc in price_changes:
        price_changes_by_merchant[pc["merchant"]] = pc

    # 7. Compute 3-month cash flow average for comparison
    # Average of (income - expenses) over last 3 complete months
    avg_cash_flow_cents = 0
    if len(reports) >= 4:
        cash_flows = []
        for report in reports[1:4]:  # Skip current month, use previous 3
            vm = PeriodViewModel.from_report(report)
            cash_flow = vm.income_cents - vm.recurring_cents - vm.discretionary_cents
            cash_flows.append(cash_flow)
        avg_cash_flow_cents = sum(cash_flows) // len(cash_flows)

    # Top spending movers: categories most above their 3-month average by dollar amount
    top_movers = []
    if category_averages and category_breakdown:
        for cat, net_cents, count, gross, refunds in category_breakdown:
            avg = category_averages.get(cat.id, 0)
            if avg > 0:
                delta = net_cents - avg
                if delta > 3000:  # Only meaningful moves (>$30)
                    pct = int(delta * 100 / avg)
                    top_movers.append({
                        "category": cat,
                        "current_cents": net_cents,
                        "avg_cents": avg,
                        "delta_cents": delta,
                        "pct": pct,
                    })
        top_movers.sort(key=lambda x: x["delta_cents"], reverse=True)
        top_movers = top_movers[:2]

    # 3-month average income for income-vs-spending diagnosis
    avg_income_cents = 0
    if len(reports) >= 4:
        income_vals = [PeriodViewModel.from_report(r).income_cents for r in reports[1:4]]
        avg_income_cents = sum(income_vals) // len(income_vals)

    # Intra-month pace card (only for "this_month" view, not custom ranges or last_month)
    pace_data = None
    if period == "this_month" and current_period and current_report:
        today_date = today  # reuse `today` already in scope
        days_elapsed = today_date.day  # day-of-month = days elapsed so far
        _, days_in_month = monthrange(today_date.year, today_date.month)  # monthrange already imported

        total_expenses_cents = current_period.recurring_cents + current_period.discretionary_cents

        avg_monthly_expenses_cents = 0
        if len(reports) >= 4:
            exp_vals = []
            for r in reports[1:4]:
                vm = PeriodViewModel.from_report(r)
                exp_vals.append(vm.recurring_cents + vm.discretionary_cents)
            if exp_vals:
                avg_monthly_expenses_cents = sum(exp_vals) // len(exp_vals)

        pace_data = _compute_pace_data(
            total_expenses_cents=total_expenses_cents,
            days_elapsed=days_elapsed,
            days_in_month=days_in_month,
            avg_monthly_expenses_cents=avg_monthly_expenses_cents,
            category_breakdown=category_breakdown,
            category_averages=category_averages,
        )

    # 8. Integrity data (kept for integrity banner, not in attention_items)
    integrity_data = None
    if current_report:
        integrity_data = get_resolution_summary(current_report)

    # Recurring total
    total_recurring_cents = sum(abs(s[1]) for s in subscriptions) + sum(abs(b[1]) for b in bills)

    # V3 DASHBOARD TEMPLATE CONTEXT
    # =====================================================================
    # New template variables for v3 dashboard redesign:
    #
    # category_averages: dict[str, int]
    #   - 3-month rolling average spending per category (in cents)
    #   - Key: category_id, Value: average spending
    #   - Used in CARD 3 (Spending) to show "avg $640" next to each category
    #
    # category_outliers: dict[str, int]
    #   - Percentage deviation from 3-month average (only positive deviations)
    #   - Key: category_id, Value: percentage (+33 means 33% above average)
    #   - Used in CARD 3 for outlier indicators
    #
    # avg_cash_flow_cents: int
    #   - 3-month average of (income - expenses)
    #   - Used in CARD 1 (Cash Flow) for comparison text
    #
    # attention_items: list[dict]
    #   - Restructured for v3: suspicious charges, multi-month trends, bill deviations
    #   - NO integrity tasks, NO price changes (moved inline to CARD 2)
    #   - Max 4 items
    #   - Used in CARD 4 (Heads Up) - only renders if items exist
    #
    # integrity_data: dict | None
    #   - Used for top-of-page integrity banner when score < 0.8
    #   - NOT shown in attention_items card anymore

    return templates.TemplateResponse("dashboard_v2.html", {
        "request": request,
        "period_type": period,
        "current_period": current_period,
        "periods": periods_json,
        "savings_rate_pct": savings_rate_pct,
        "avg_net_cents": avg_net_cents,
        "prev_category_map": prev_category_map,
        "attention_items": attention_items,
        "subscriptions": subscriptions,
        "bills": bills,
        "price_changes_by_merchant": price_changes_by_merchant,
        "total_recurring_cents": total_recurring_cents,
        "start_date": start_date or "",
        "end_date": end_date or "",
        "category_breakdown": category_breakdown,
        "categories": CATEGORIES,
        "all_accounts": all_accounts,
        "selected_accounts": account_filter or [],
        "show_no_data": False,
        "expense_only_view": expense_only_view,
        "pending_count": pending_count,
        "today": dates_mod.today(),
        "timedelta": timedelta,
        "closed_period": closed_period,
        # V3 new variables:
        "category_averages": category_averages,
        "category_outliers": category_outliers,
        "avg_cash_flow_cents": avg_cash_flow_cents,
        "integrity_data": integrity_data,
        "top_movers": top_movers,
        "avg_income_cents": avg_income_cents,
        "avg_savings_rate_pct": avg_savings_rate_pct,
        "savings_tier": savings_tier,
        "pace_data": pace_data,
        # Item 8: HTTP banner
        "is_http": request.url.scheme == "http",
        # Demo mode: shown when demo transactions are loaded
        "is_demo": is_demo,
        "is_empty": is_empty,
    })


def _drilldown_filter(report, scope: str):
    """
    Filter a report's transactions by drilldown scope.
    Returns (scope_label, inclusion_rules, included_txns, scope_total, excluded, resolution_context).
    """
    from .reporting_models import TransactionType, SpendingBucket, TransferStatus

    transactions = []
    scope_label = ""
    scope_total = 0
    inclusion_rules: list[str] = []

    if scope == "income":
        scope_label = "Income"
        inclusion_rules = ["Transactions classified as Income", "Posted only"]
        for txn in report.transactions:
            if txn.txn_type == TransactionType.INCOME:
                transactions.append(txn)
                scope_total += txn.amount_cents
    elif scope == "spend":
        scope_label = "Total Spending"
        inclusion_rules = ["All expenses and refunds", "Posted only"]
        for txn in report.transactions:
            if txn.txn_type in (TransactionType.EXPENSE, TransactionType.REFUND):
                transactions.append(txn)
                if txn.txn_type == TransactionType.EXPENSE:
                    scope_total -= txn.amount_cents
                else:
                    scope_total += txn.amount_cents
    elif scope == "recurring":
        scope_label = "Recurring / Fixed"
        inclusion_rules = ["Fixed obligations (subscriptions, bills)", "Posted only"]
        for txn in report.transactions:
            if txn.spending_bucket == SpendingBucket.FIXED_OBLIGATIONS:
                transactions.append(txn)
                scope_total += abs(txn.amount_cents)
    elif scope == "discretionary":
        scope_label = "One-Time / Discretionary"
        inclusion_rules = ["Variable, discretionary, and one-off spending", "Posted only"]
        for txn in report.transactions:
            if txn.spending_bucket in (SpendingBucket.VARIABLE_ESSENTIALS, SpendingBucket.DISCRETIONARY, SpendingBucket.ONE_OFFS):
                transactions.append(txn)
                scope_total += abs(txn.amount_cents)
    elif scope == "net":
        scope_label = "Net (Income - Spending)"
        inclusion_rules = ["Income minus expenses", "Transfers excluded", "Posted only"]
        for txn in report.transactions:
            if txn.txn_type not in (TransactionType.TRANSFER, TransactionType.CREDIT_OTHER):
                transactions.append(txn)
        scope_total = report.totals.net_cents
    elif scope == "credit_other":
        scope_label = "Unclassified Credits"
        inclusion_rules = ["Unclassified credits awaiting classification"]
        for txn in report.transactions:
            if txn.txn_type == TransactionType.CREDIT_OTHER:
                transactions.append(txn)
                scope_total += txn.amount_cents
    elif scope == "unmatched_transfers":
        scope_label = "Unmatched Transfers"
        inclusion_rules = ["Transfers with only one leg identified"]
        for txn in report.transactions:
            if txn.txn_type == TransactionType.TRANSFER and txn.transfer_status in (TransferStatus.UNMATCHED, TransferStatus.PENDING_MATCH):
                transactions.append(txn)
                scope_total += txn.amount_cents
    elif scope.startswith("category:"):
        cat_id = scope.split(":", 1)[1]
        cat = CATEGORIES.get(cat_id)
        scope_label = f"Category: {cat.name if cat else cat_id}"
        inclusion_rules = [f"Category: {cat.name if cat else cat_id}", "Expenses and refunds"]
        for txn in report.transactions:
            if txn.category_id == cat_id:
                transactions.append(txn)
                scope_total += abs(txn.amount_cents)
    elif scope.startswith("merchant:"):
        merchant_name = scope.split(":", 1)[1]
        scope_label = f"Merchant: {merchant_name}"
        inclusion_rules = [f"Transactions from {merchant_name}", "All types"]
        for txn in report.transactions:
            if txn.merchant_norm and txn.merchant_norm.lower() == merchant_name.lower():
                transactions.append(txn)
                scope_total += abs(txn.amount_cents)
    else:
        return None

    # Build excluded section — count transactions NOT included, grouped by reason
    included_fps = {txn.fingerprint for txn in transactions}
    excluded = {"transfers": {"count": 0, "total_cents": 0}, "credit_other": {"count": 0, "total_cents": 0}}
    for txn in report.transactions:
        if txn.fingerprint in included_fps:
            continue
        if txn.txn_type == TransactionType.TRANSFER:
            excluded["transfers"]["count"] += 1
            excluded["transfers"]["total_cents"] += abs(txn.amount_cents)
        elif txn.txn_type == TransactionType.CREDIT_OTHER:
            excluded["credit_other"]["count"] += 1
            excluded["credit_other"]["total_cents"] += abs(txn.amount_cents)
    # Strip zero-count exclusion categories
    excluded = {k: v for k, v in excluded.items() if v["count"] > 0}

    # Add resolution context for resolvable scopes
    resolution_context = None
    if scope == "credit_other":
        resolution_context = {
            "scope": "credit_other",
            "allows_resolution": True,
            "resolution_options": [
                {"value": "INCOME", "label": "Income", "description": "Real income (paycheck, business income)"},
                {"value": "REFUND", "label": "Refund", "description": "Return or credit for prior purchase"},
                {"value": "TRANSFER", "label": "Transfer", "description": "Internal transfer between accounts"},
                {"value": "CREDIT_OTHER", "label": "Keep Unclassified", "description": "Leave as unclassified"},
            ]
        }
    elif scope == "unmatched_transfers":
        resolution_context = {
            "scope": "unmatched_transfers",
            "allows_resolution": True,
            "resolution_options": [
                {"value": "TRANSFER", "label": "Confirm Transfer", "description": "This is a valid transfer"},
                {"value": "INCOME", "label": "Reclassify as Income", "description": "Not a transfer, this is income"},
                {"value": "EXPENSE", "label": "Reclassify as Expense", "description": "Not a transfer, this is spending"},
            ]
        }

    return scope_label, inclusion_rules, transactions, scope_total, excluded, resolution_context


def _drilldown_report(scope: str, start_date: str, end_date: str, accounts: str | None, conn):
    """Parse params, run report, filter by scope. Returns (result, error_response)."""
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return None, JSONResponse(status_code=400, content={"error": "Invalid date format"})

    account_filter, _ = parse_account_filter(accounts)
    report = ReportService(conn).report_period(start, end, account_filter=account_filter)
    result = _drilldown_filter(report, scope)
    if result is None:
        return None, JSONResponse(status_code=400, content={"error": f"Unknown scope: {scope}"})

    return result, None


def _account_names(conn) -> dict[str, str]:
    return {r["account_id"]: r["name"] for r in conn.execute("SELECT account_id, name FROM accounts").fetchall()}


@app.get("/api/drilldown")
@limiter.limit("60/minute")
def drilldown(
    request: Request,
    scope: str = Query(..., description="Scope: income, spend, recurring, discretionary, net, category:<id>, credit_other, unmatched_transfers"),
    start_date: str = Query(..., description="Period start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="Period end date (YYYY-MM-DD, exclusive)"),
    accounts: str | None = Query(None, description="Comma-separated account IDs"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Drilldown into dashboard numbers with full transaction detail."""
    result, err = _drilldown_report(scope, start_date, end_date, accounts, conn)
    if err:
        return err

    scope_label, inclusion_rules, transactions, scope_total, excluded, resolution_context = result
    acct_names = _account_names(conn)

    # Get existing overrides for these transactions
    fp_overrides, _ = dbmod.get_txn_type_overrides(conn)

    sorted_txns = sorted(transactions, key=lambda t: t.posted_at, reverse=True)
    all_fps = [txn.fingerprint for txn in sorted_txns]
    annotations = dbmod.get_notes_and_tags_bulk(conn, all_fps)

    txn_list = []
    for txn in sorted_txns:
        ann = annotations.get(txn.fingerprint, {"note": None, "tags": []})
        cat = CATEGORIES.get(txn.category_id) if txn.category_id else None
        txn_list.append({
            "fingerprint": txn.fingerprint,
            "date": txn.posted_at.isoformat(),
            "merchant": txn.merchant_norm or txn.raw_description or "Unknown",
            "amount_cents": txn.amount_cents,
            "type": txn.txn_type.value if txn.txn_type else "unknown",
            "account_id": txn.account_id,
            "account_name": acct_names.get(txn.account_id, "Unknown"),
            "override_type": fp_overrides.get(txn.fingerprint),
            "category_id": txn.category_id,
            "category": cat.name if cat else None,
            "category_icon": cat.icon if cat else None,
            "note": ann["note"],
            "tags": ann["tags"],
        })

    response_data = {
        "scope": scope,
        "scope_label": scope_label,
        "start_date": start_date,
        "end_date": end_date,
        "total_cents": scope_total,
        "transaction_count": len(txn_list),
        "inclusion_rules": inclusion_rules,
        "excluded": excluded,
        "transactions": txn_list,
        "accounts": acct_names,
    }

    if resolution_context:
        response_data["resolution_context"] = resolution_context

    return JSONResponse(content=response_data)


@app.get("/api/drilldown/export")
def drilldown_export(
    scope: str = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...),
    accounts: str | None = Query(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Export drilldown transactions as CSV."""
    result, err = _drilldown_report(scope, start_date, end_date, accounts, conn)
    if err:
        return err

    _label, _rules, transactions, _total, _excluded, _resolution_context = result
    acct_names = _account_names(conn)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["date", "merchant", "amount_usd", "type", "category", "account", "reason"])
    for txn in sorted(transactions, key=lambda t: t.posted_at, reverse=True):
        writer.writerow([
            txn.posted_at.isoformat(),
            _sanitize_csv_field(txn.merchant_norm or txn.raw_description or "Unknown"),
            str(Decimal(txn.amount_cents) / 100),
            txn.txn_type.value if txn.txn_type else "unknown",
            txn.category_id or "",
            _sanitize_csv_field(acct_names.get(txn.account_id, "Unknown")),
            txn.reason.primary_code if txn.reason else "",
        ])

    output.seek(0)
    import re
    safe = lambda s: re.sub(r'[^a-zA-Z0-9_\-]', '_', s)
    fname = f"drilldown_{safe(scope)}_{safe(start_date)}_{safe(end_date)}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/explain")
def explain_number(
    scope: str = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...),
    accounts: str | None = Query(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Deprecated: use /api/drilldown instead."""
    return drilldown(scope=scope, start_date=start_date, end_date=end_date, accounts=accounts, conn=conn)


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

    invalidate_report_cache()
    return JSONResponse(content={"status": "ok", "alert_key": req.alert_key, "action": req.action})


@app.post("/api/income-source")
def income_source(
    req: IncomeSourceRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Mark a merchant as income or not-income."""
    dbmod.mark_income_source(conn, req.merchant, req.is_income)
    invalidate_report_cache()
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
    invalidate_report_cache()
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
    invalidate_report_cache()
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
        invalidate_report_cache()
        return JSONResponse(content={
            "status": "ok",
            "fingerprint": req.fingerprint,
            "target_type": req.target_type,
        })
    else:
        dbmod.set_txn_type_override_merchant(
            conn, req.merchant_pattern, req.target_type, req.reason
        )
        invalidate_report_cache()
        return JSONResponse(content={
            "status": "ok",
            "merchant_pattern": req.merchant_pattern,
            "target_type": req.target_type,
        })


@app.post("/api/txn-type-override/bulk")
def set_bulk_txn_type_override(
    req: BulkTxnTypeOverrideRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """
    Set transaction type overrides for multiple transactions.

    Validates all target types, applies overrides in a transaction,
    invalidates report cache, and returns updated integrity score.
    """
    valid_types = {"INCOME", "EXPENSE", "TRANSFER", "REFUND", "CREDIT_OTHER"}

    # Validate all target types
    for override in req.overrides:
        if override.target_type not in valid_types:
            return JSONResponse(
                status_code=400,
                content={"error": f"Invalid target_type: {override.target_type}"},
            )

    # Apply all overrides atomically (defer commits until all succeed)
    for override in req.overrides:
        reason = override.reason or req.reason
        dbmod.set_txn_type_override_fingerprint(
            conn, override.fingerprint, override.target_type, reason,
            commit=False,
        )
    conn.commit()

    # Invalidate report cache to force recalculation
    invalidate_report_cache()

    # Recompute integrity score for current period
    # (Use same date range as dashboard context)
    from .dates import period_bounds

    # Default to current month for score calculation
    start, end = period_bounds(TimePeriod.MONTH, dates_mod.today())
    report = ReportService(conn).report_period(start, end)

    return JSONResponse(content={
        "status": "ok",
        "overrides_applied": len(req.overrides),
        "new_integrity_score": report.integrity.score,
        "integrity_percent": int(report.integrity.score * 100),
        "is_actionable": report.integrity.is_actionable,
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
@limiter.limit("60/minute")
def search_transactions(
    request: Request,
    q: str = Query(..., description="Search query"),
    accounts: str | None = Query(None, description="Comma-separated account IDs for primary results"),
    days: int = Query(365, ge=1, le=3650, description="Days to search back"),
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
    cutoff_date = (dates_mod.today() - timedelta(days=days)).isoformat()
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


@app.get("/insights", response_class=HTMLResponse)
def insights_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Financial health insights: 12-month savings rate history and income variability."""
    rs = ReportService(conn)
    reports = rs.report_periods(TimePeriod.MONTH, num_periods=12)
    insights = _compute_insights_data(reports)
    return templates.TemplateResponse("insights.html", {"request": request, **insights})


@app.get("/plan", response_class=HTMLResponse)
def plan_page(request: Request):
    """Plan page — placeholder shell for future cash flow / debt / goals features."""
    return templates.TemplateResponse("plan.html", {"request": request})


@app.get("/audit", response_class=HTMLResponse)
def audit_page(
    request: Request,
    accounts: str | None = Query(None, description="Comma-separated account IDs to filter"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Financial audit page — coverage report, missing charges, ghost transactions."""
    from .reporting_models import TransactionType

    account_filter, show_no_data = parse_account_filter(accounts)

    all_accounts = conn.execute(
        "SELECT account_id, name, institution, type FROM accounts ORDER BY institution, name"
    ).fetchall()

    if show_no_data:
        return templates.TemplateResponse("audit.html", {
            "request": request,
            "all_accounts": all_accounts,
            "selected_accounts": [],
            "show_no_data": True,
            "coverage": {},
            "top_merchants": [],
            "not_yet_posted": [],
            "low_confidence": [],
            "ghosts": [],
            "categories": CATEGORIES,
        })

    today = dates_mod.today()
    month_start = date(today.year, today.month, 1)
    month_end = today + timedelta(days=1)  # exclusive

    # Build report for current month
    report_service = ReportService(conn)
    report = report_service.report_period(month_start, month_end, account_filter=account_filter)

    # Get tracked items (uses allowed public APIs, not _detect_patterns)
    subscriptions = get_subscriptions(conn, days=400, account_filter=account_filter)
    bills = get_bills(conn, days=400, account_filter=account_filter)

    tracked_merchants = set()
    for sub in subscriptions:
        tracked_merchants.add(sub[0])
    for bill in bills:
        tracked_merchants.add(bill[0])

    # --- Coverage ---
    total_txns = len(report.transactions)
    by_type: dict[str, int] = {}
    categorized = 0
    uncategorized = 0
    for txn in report.transactions:
        type_name = txn.txn_type.name
        by_type[type_name] = by_type.get(type_name, 0) + 1
        if txn.txn_type == TransactionType.EXPENSE:
            if txn.category_id and txn.category_id != "other":
                categorized += 1
            else:
                uncategorized += 1

    expense_total = by_type.get("EXPENSE", 0)
    cat_pct = round(categorized / expense_total * 100) if expense_total else 100

    high_conf = sum(1 for t in report.transactions if t.reason.confidence >= 0.8)
    low_conf_count = total_txns - high_conf

    coverage = {
        "total_txns": total_txns,
        "by_type": by_type,
        "categorized": categorized,
        "uncategorized": uncategorized,
        "cat_pct": cat_pct,
        "high_conf": high_conf,
        "low_conf": low_conf_count,
        "tracked_merchants": len(tracked_merchants),
    }

    # --- Top merchants by spend ---
    merchant_norm_expr = "TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), '')))"
    top_sql = f"""
        SELECT {merchant_norm_expr} AS merchant_norm,
               COUNT(*) as cnt, SUM(ABS(amount_cents)) as total,
               MAX(posted_at) as last_seen
        FROM transactions
        WHERE amount_cents < 0 AND COALESCE(pending, 0) = 0
    """
    top_params: list = []
    if account_filter:
        placeholders = ",".join("?" * len(account_filter))
        top_sql += f" AND account_id IN ({placeholders})"
        top_params.extend(account_filter)
    top_sql += " GROUP BY merchant_norm ORDER BY total DESC LIMIT 20"
    rows = conn.execute(top_sql, top_params).fetchall()

    # Build set of recurring merchants from subs/bills for status
    recurring_merchants = set()
    for sub in subscriptions:
        recurring_merchants.add(sub[0])
    for bill in bills:
        recurring_merchants.add(bill[0])

    top_merchants = []
    for row in rows:
        merchant = row[0]
        if merchant in tracked_merchants:
            status = "tracked"
        elif merchant in recurring_merchants:
            status = "recurring"
        else:
            status = "untracked"
        top_merchants.append({
            "merchant": merchant,
            "count": row[1],
            "total_cents": row[2],
            "last_seen": row[3],
            "status": status,
        })

    # --- Not yet posted (expected recurring charges not seen this month) ---
    cadence_days_map = {"weekly": 7, "biweekly": 14, "monthly": 30, "bimonthly": 60,
                        "quarterly": 91, "annual": 365}
    not_yet_posted = []
    for items in (subscriptions, bills):
        for item in items:
            merchant_name = item[0]
            cadence = item[2]
            last_seen = item[4]
            median_amount = item[1]
            interval = cadence_days_map.get(cadence, 30)
            expected_next = last_seen + timedelta(days=interval)
            days_since = (today - expected_next).days
            if days_since <= 7 or days_since > 90:
                continue
            has_current = any(
                t.merchant_norm == merchant_name and t.posted_at >= month_start
                for t in report.transactions
            )
            if has_current:
                continue
            not_yet_posted.append({
                "merchant": merchant_name,
                "cadence": cadence,
                "median_amount": median_amount,
                "last_seen": last_seen,
                "expected_date": expected_next,
            })
    not_yet_posted.sort(key=lambda x: x["expected_date"])

    # --- Low confidence transactions ---
    low_confidence = []
    for txn in report.transactions:
        if txn.reason.confidence < 0.8 and txn.reason.primary_code != "USER_OVERRIDE":
            low_confidence.append(txn)
    low_confidence.sort(key=lambda t: t.reason.confidence)

    # --- Ghost transactions (uncategorized expenses) ---
    ghosts = []
    for txn in report.transactions:
        if txn.txn_type == TransactionType.EXPENSE and txn.category_id in (None, "other"):
            ghosts.append(txn)

    return templates.TemplateResponse("audit.html", {
        "request": request,
        "all_accounts": all_accounts,
        "selected_accounts": account_filter or [],
        "show_no_data": False,
        "coverage": coverage,
        "top_merchants": top_merchants,
        "not_yet_posted": not_yet_posted,
        "low_confidence": low_confidence,
        "ghosts": ghosts,
        "categories": CATEGORIES,
    })


@app.get("/api/payee/{payee_norm:path}")
def get_payee_transactions(
    payee_norm: str,
    days: int = Query(400, ge=1, le=3650, description="Lookback days"),
    accounts: str | None = Query(None, description="Comma-separated account IDs to filter"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Get transactions for a specific payee (drill-down) with enhanced details."""
    from .legacy_classify import _match_known_subscription

    since = (dates_mod.today() - timedelta(days=days)).isoformat()

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
    since = (dates_mod.today() - timedelta(days=days)).isoformat()
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
            str(Decimal(alert.amount_cents) / 100),
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
    import re
    safe_period = re.sub(r'[^a-zA-Z0-9_\-]', '_', period)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_period}_summary.csv"'},
    )


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------
@app.post("/api/sync")
@limiter.limit("10/minute")
def api_sync(request: Request, _auth: bool = Depends(verify_auth_token)):
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
        # Sanitize error message to prevent credential leakage
        # SimpleFIN access URLs contain auth tokens that must not be exposed
        import re
        msg = str(e)
        msg = re.sub(r'https?://[^\s"\'<>]+', '[URL redacted]', msg)
        return JSONResponse(
            status_code=500,
            content={"error": msg},
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
    limit: int = Query(20, ge=1, le=500, description="Number of recent syncs to return"),
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
    limit: int = Query(50, ge=1, le=500, description="Number of records to return"),
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
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
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
# Transaction Notes & Tags API
# ---------------------------------------------------------------------------
@app.get("/api/transaction/{fingerprint}/annotations")
def api_get_annotations(
    fingerprint: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Get note and tags for a transaction."""
    note = dbmod.get_transaction_note(conn, fingerprint)
    tags = dbmod.get_transaction_tags(conn, fingerprint)
    return JSONResponse(content={"note": note, "tags": tags})


@app.post("/api/transaction/{fingerprint}/note")
def api_set_note(
    fingerprint: str,
    req: TransactionNoteRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Set or update a note on a transaction."""
    note = req.note.strip()
    if not note:
        dbmod.delete_transaction_note(conn, fingerprint)
    else:
        dbmod.set_transaction_note(conn, fingerprint, note)
    return JSONResponse(content={"status": "ok"})


@app.delete("/api/transaction/{fingerprint}/note")
def api_delete_note(
    fingerprint: str,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Remove a note from a transaction."""
    dbmod.delete_transaction_note(conn, fingerprint)
    return JSONResponse(content={"status": "ok"})


@app.post("/api/transaction/{fingerprint}/tag")
def api_add_tag(
    fingerprint: str,
    req: TransactionTagRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Add a tag to a transaction."""
    import re
    tag = req.tag.strip().lower()
    if not tag or not re.match(r'^[a-z0-9][a-z0-9_ -]{0,48}[a-z0-9]$', tag):
        return JSONResponse(
            status_code=400,
            content={"error": "Tag must be 2-50 chars: letters, numbers, spaces, hyphens, underscores"},
        )
    dbmod.add_transaction_tag(conn, fingerprint, tag)
    return JSONResponse(content={"status": "ok"})


@app.delete("/api/transaction/{fingerprint}/tag/{tag}")
def api_remove_tag(
    fingerprint: str,
    tag: str,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Remove a tag from a transaction."""
    dbmod.remove_transaction_tag(conn, fingerprint, tag)
    return JSONResponse(content={"status": "ok"})


@app.get("/api/tags")
def api_list_tags(conn: sqlite3.Connection = Depends(get_db)):
    """List all tags used across transactions (for autocomplete)."""
    tags = dbmod.get_all_tags(conn)
    return JSONResponse(content={"tags": tags})


# ---------------------------------------------------------------------------
# Budget Targets API
# ---------------------------------------------------------------------------
@app.get("/api/budget/targets")
def api_budget_targets(conn: sqlite3.Connection = Depends(get_db)):
    """Get all budget targets."""
    targets = dbmod.get_budget_targets(conn)
    return JSONResponse(content={
        "targets": [
            {"category_id": cid, "monthly_target_cents": cents}
            for cid, cents in targets.items()
        ]
    })


@app.post("/api/budget/target")
def api_set_budget_target(
    req: BudgetTargetRequest,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Set or update a budget target for a category."""
    if req.category_id not in CATEGORIES:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid category: {req.category_id}"},
        )
    if req.monthly_target_cents < 0:
        return JSONResponse(
            status_code=400,
            content={"error": "Target must be non-negative"},
        )
    dbmod.set_budget_target(conn, req.category_id, req.monthly_target_cents)
    return JSONResponse(content={"status": "ok"})


@app.delete("/api/budget/target/{category_id}")
def api_delete_budget_target(
    category_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    _auth: bool = Depends(verify_auth_token),
):
    """Remove a budget target."""
    dbmod.delete_budget_target(conn, category_id)
    return JSONResponse(content={"status": "ok"})


@app.get("/api/budget/status")
def api_budget_status(
    conn: sqlite3.Connection = Depends(get_db),
):
    """Get current month budget status: targets vs actual spending."""
    from .dates import period_bounds

    start, end = period_bounds(TimePeriod.MONTH, dates_mod.today())
    report = ReportService(conn).report_period(start, end)
    breakdown = category_breakdown_from_report(report)
    targets = dbmod.get_budget_targets(conn)

    # Build status per category that has a target
    categories_status = []
    total_budget_cents = 0
    total_spent_cents = 0

    # Map actuals: category_id -> net_cents
    actuals = {cat.id: net for cat, net, count, gross, refund in breakdown}

    for cat_id, target_cents in targets.items():
        cat = CATEGORIES.get(cat_id)
        if not cat:
            continue
        spent = actuals.get(cat_id, 0)
        pct = round(spent / target_cents * 100, 1) if target_cents > 0 else 0
        total_budget_cents += target_cents
        total_spent_cents += spent
        categories_status.append({
            "category_id": cat_id,
            "name": cat.name,
            "icon": cat.icon,
            "color": cat.color,
            "target_cents": target_cents,
            "spent_cents": spent,
            "remaining_cents": target_cents - spent,
            "percent_used": pct,
        })

    # Sort by percent used descending (most over-budget first)
    categories_status.sort(key=lambda x: -x["percent_used"])

    total_pct = round(total_spent_cents / total_budget_cents * 100, 1) if total_budget_cents > 0 else 0

    return JSONResponse(content={
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "total_budget_cents": total_budget_cents,
        "total_spent_cents": total_spent_cents,
        "total_remaining_cents": total_budget_cents - total_spent_cents,
        "total_percent_used": total_pct,
        "categories": categories_status,
    })


@app.get("/budget", response_class=HTMLResponse)
def budget_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Budget management page."""
    from .dates import period_bounds

    start, end = period_bounds(TimePeriod.MONTH, dates_mod.today())
    report = ReportService(conn).report_period(start, end)
    breakdown = category_breakdown_from_report(report)
    targets = dbmod.get_budget_targets(conn)

    # Build category list with targets and actuals
    actuals = {cat.id: (net, count) for cat, net, count, gross, refund in breakdown}
    budget_rows = []
    total_budget = 0
    total_spent = 0

    for cat_id, cat in CATEGORIES.items():
        if cat_id in ("income", "transfer", "one_time_deposit"):
            continue
        target = targets.get(cat_id, 0)
        spent, count = actuals.get(cat_id, (0, 0))
        if target > 0 or spent > 0:
            pct = round(spent / target * 100, 1) if target > 0 else 0
            budget_rows.append({
                "category": cat,
                "target_cents": target,
                "spent_cents": spent,
                "remaining_cents": target - spent if target > 0 else 0,
                "percent": pct,
                "count": count,
            })
            total_budget += target
            total_spent += spent

    # Sort: categories with targets first (by % used desc), then unbudgeted by spend desc
    budget_rows.sort(key=lambda r: (-1 if r["target_cents"] > 0 else 0, -r["percent"], -r["spent_cents"]))

    total_pct = round(total_spent / total_budget * 100, 1) if total_budget > 0 else 0

    # All categories for the "add target" dropdown
    all_categories = [
        {"id": cid, "name": c.name, "icon": c.icon}
        for cid, c in CATEGORIES.items()
        if cid not in ("income", "transfer", "one_time_deposit")
    ]

    return templates.TemplateResponse("budget.html", {
        "request": request,
        "budget_rows": budget_rows,
        "total_budget_cents": total_budget,
        "total_spent_cents": total_spent,
        "total_remaining_cents": total_budget - total_spent,
        "total_percent": total_pct,
        "all_categories": all_categories,
        "period_start": start,
        "period_end": end,
    })


# ---------------------------------------------------------------------------
# Budget Planner API
# ---------------------------------------------------------------------------
@app.get("/api/planner/budget")
def api_budget_plan(
    months: int = Query(6, ge=1, le=120, description="Months of history to analyze"),
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
    months: int = Query(6, ge=1, le=120, description="Months of history"),
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
    history_months: int = Query(6, ge=1, le=120, description="Months of history to base projection on"),
    forward_months: int = Query(3, ge=1, le=24, description="Months to project forward"),
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
    days: int = Query(30, ge=1, le=365, description="Days to project forward"),
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
    days: int = Query(30, ge=1, le=365, description="Days to look ahead"),
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


@app.get("/sync-log")
def sync_log_page(
    request: Request,
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

    return templates.TemplateResponse("sync-log.html", {
        "request": request,
        "runs": runs,
        "stats": stats,
        "recent_inserts": recent_inserts,
        "recent_updates": recent_updates,
    })


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
# Connect / onboarding page
# ---------------------------------------------------------------------------
@app.get("/connect", response_class=HTMLResponse)
def connect_page(request: Request):
    """
    Onboarding page: CSV import and SimpleFIN connection.
    Public — no auth required (same as /dashboard in demo mode).
    """
    return templates.TemplateResponse("connect.html", {"request": request})


# ---------------------------------------------------------------------------
# CSV Import API
# ---------------------------------------------------------------------------

class CsvConfirmRequest(BaseModel):
    account_id: str = "csv-import"
    date_col: str = "date"
    amount_col: str = "amount"
    description_col: str = "description"
    merchant_col: str | None = None


_CSV_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


@app.post("/api/import/csv/preview")
async def api_csv_preview(
    request: Request,
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Accept a CSV file upload, auto-detect bank format, return a preview.
    Auth is enforced by the require_api_auth middleware (all /api/* routes).
    """
    from .csv_import import preview_csv

    # Size guard — read up to limit + 1 byte to detect oversize
    content_bytes = await file.read(_CSV_MAX_BYTES + 1)
    if len(content_bytes) > _CSV_MAX_BYTES:
        return JSONResponse(
            status_code=413,
            content={"detail": "File too large. Maximum size is 5 MB."},
        )

    try:
        csv_content = content_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        return JSONResponse(status_code=400, content={"detail": f"Cannot decode file: {exc}"})

    result = preview_csv(csv_content)

    if "error" in result:
        return JSONResponse(status_code=422, content={"detail": result["error"]})

    return JSONResponse(content=result)


@app.post("/api/import/csv/confirm")
async def api_csv_confirm(
    request: Request,
    file: UploadFile = File(...),
    account_id: str = "csv-import",
    date_col: str = "date",
    amount_col: str = "amount",
    description_col: str = "description",
    merchant_col: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Accept a CSV file + confirmed column mapping, run the full import.
    Auth is enforced by the require_api_auth middleware (all /api/* routes).
    """
    from .csv_import import import_csv_file

    content_bytes = await file.read(_CSV_MAX_BYTES + 1)
    if len(content_bytes) > _CSV_MAX_BYTES:
        return JSONResponse(
            status_code=413,
            content={"detail": "File too large. Maximum size is 5 MB."},
        )

    try:
        csv_content = content_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        return JSONResponse(status_code=400, content={"detail": f"Cannot decode file: {exc}"})

    result = import_csv_file(
        csv_content=csv_content,
        conn=conn,
        account_id=account_id,
        date_col=date_col,
        amount_col=amount_col,
        description_col=description_col,
        merchant_col=merchant_col,
    )

    return JSONResponse(content={
        "imported": result["imported"],
        "skipped_duplicates": result["skipped"],
        "errors": result["errors"][:10],  # Cap error list for response size
    })


# ---------------------------------------------------------------------------
# SimpleFIN token claim (Story 5 — wizard handoff)
# ---------------------------------------------------------------------------
class SimpleFinTokenRequest(BaseModel):
    setup_token: str


@app.post("/api/simplefin-token")
async def api_simplefin_token(req: SimpleFinTokenRequest):
    """
    Exchange a SimpleFIN setup token for an access URL and save to keyring.
    Auth is enforced by the require_api_auth middleware (all /api/* routes).
    """
    from .simplefin_client import claim_access_url
    from . import credentials

    token = req.setup_token.strip()
    if not token:
        return JSONResponse(status_code=400, content={"detail": "setup_token is required"})

    try:
        access_url = claim_access_url(token)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"detail": f"Token claim failed: {exc}"})

    saved = False
    if credentials.is_keyring_available():
        try:
            saved = credentials.set_simplefin_url(access_url)
        except Exception:
            pass

    return JSONResponse(content={"status": "ok", "saved_to_keyring": saved})


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main():
    uvicorn.run("fin.web:app", host="127.0.0.1", port=8000, reload=False)


# ---------------------------------------------------------------------------
# Dashboard metric helpers
# ---------------------------------------------------------------------------

def _compute_savings_tier(savings_rate_pct: float) -> str:
    """Return benchmark tier label for a savings rate percentage.

    Benchmarks from personal finance literature:
    - 30%+   → wealth-building (aggressive accumulation)
    - 20-29% → progress (solid savings)
    - 0-19%  → survival (positive but below target)
    - <= 0%  → negative (spending exceeds income)
    """
    if savings_rate_pct >= 30.0:
        return "wealth-building"
    elif savings_rate_pct >= 20.0:
        return "progress"
    elif savings_rate_pct > 0.0:
        return "survival"
    else:
        return "negative"


def _compute_pace_data(
    total_expenses_cents: int,
    days_elapsed: int,
    days_in_month: int,
    avg_monthly_expenses_cents: int,
    category_breakdown: list,
    category_averages: dict,
) -> dict | None:
    """
    Compute intra-month pace projection data for the current month.

    Returns None when < 3 days elapsed (too early for reliable projection).

    Args:
        total_expenses_cents: recurring + discretionary spent so far this month
        days_elapsed: day-of-month (1 = first day of month)
        days_in_month: total days in current month
        avg_monthly_expenses_cents: 3-month rolling average total expenses
        category_breakdown: list of (Category, net_cents, count, gross_cents, refund_cents)
        category_averages: dict[category_id -> avg_cents] (3-month rolling avg)

    Returns:
        dict with projection fields and top_drivers, or None if too early
    """
    if days_elapsed < 3 or days_in_month <= 0:
        return None

    days_elapsed = min(days_elapsed, days_in_month)
    pacing_factor = days_elapsed / days_in_month
    projected_spend = int(total_expenses_cents / pacing_factor)
    variance_cents = projected_spend - avg_monthly_expenses_cents
    variance_pct = (
        round(variance_cents * 100 / avg_monthly_expenses_cents)
        if avg_monthly_expenses_cents > 0 else 0
    )

    # Per-category variance drivers (only surface positive variance > $20)
    top_drivers = []
    for cat, net_cents, _count, _gross, _refund in category_breakdown:
        cat_id = cat.id if hasattr(cat, "id") else str(cat)
        avg = category_averages.get(cat_id, 0)
        if avg <= 0:
            continue
        projected_cat = int(net_cents / pacing_factor)
        cat_variance = projected_cat - avg
        if cat_variance > 2000:  # Only surface > $20 over-pace
            top_drivers.append({
                "category_id": cat_id,
                "category_label": cat.label if hasattr(cat, "label") else cat_id,
                "current_cents": net_cents,
                "projected_cents": projected_cat,
                "avg_cents": avg,
                "variance_cents": cat_variance,
            })

    top_drivers.sort(key=lambda x: -x["variance_cents"])

    return {
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "pacing_factor": pacing_factor,
        "total_spend_so_far_cents": total_expenses_cents,
        "projected_spend_cents": projected_spend,
        "avg_monthly_expenses_cents": avg_monthly_expenses_cents,
        "variance_cents": variance_cents,
        "variance_pct": variance_pct,
        "top_drivers": top_drivers[:3],
    }


def _compute_insights_data(reports: list) -> dict:
    """
    Compute financial health insights from a list of FinancialReport objects.

    Args:
        reports: list of FinancialReport, most-recent-first (as returned by
                 report_service.report_periods). May be empty.

    Returns dict with:
        savings_history      — list of monthly dicts, chronological (oldest first)
        avg_savings_rate_pct — mean savings rate across months with income > 0
        income_cv            — coefficient of variation of monthly income (%)
        income_stability     — "stable" / "moderate" / "variable"
        savings_streak       — consecutive positive-net months from most recent
        months_with_data     — count of months with income > 0
    """
    import math

    # Build per-month entries (skip months with no income)
    # Report has .totals (PeriodTotals) and .start_date
    entries = []
    for r in reports:
        income = r.totals.income_cents
        net = r.totals.net_cents
        if income <= 0:
            continue
        rate = round(net / income * 100, 1)
        entries.append({
            "label": r.start_date.strftime("%b %Y"),
            "savings_rate_pct": rate,
            "net_cents": net,
            "income_cents": income,
        })

    # Reverse to chronological order (oldest first) for chart rendering
    entries = list(reversed(entries))

    months_with_data = len(entries)

    # Average savings rate
    avg_savings_rate_pct = 0.0
    if months_with_data > 0:
        avg_savings_rate_pct = round(
            sum(e["savings_rate_pct"] for e in entries) / months_with_data, 1
        )

    # Income coefficient of variation (std dev / mean × 100)
    income_cv = 0.0
    income_stability = "stable"
    if months_with_data >= 2:
        incomes = [e["income_cents"] for e in entries]
        mean_income = sum(incomes) / len(incomes)
        if mean_income > 0:
            variance = sum((x - mean_income) ** 2 for x in incomes) / len(incomes)
            std_dev = math.sqrt(variance)
            income_cv = round(std_dev / mean_income * 100, 1)
    if income_cv >= 25.0:
        income_stability = "variable"
    elif income_cv >= 10.0:
        income_stability = "moderate"
    else:
        income_stability = "stable"

    # Savings streak — count consecutive positive-net months from most recent
    savings_streak = 0
    for e in reversed(entries):
        if e["net_cents"] > 0:
            savings_streak += 1
        else:
            break

    return {
        "savings_history": entries,
        "avg_savings_rate_pct": avg_savings_rate_pct,
        "income_cv": income_cv,
        "income_stability": income_stability,
        "savings_streak": savings_streak,
        "months_with_data": months_with_data,
    }


if __name__ == "__main__":
    main()
