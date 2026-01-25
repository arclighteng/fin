import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Account, Transaction

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL UNIQUE,
  institution TEXT NOT NULL,
  name TEXT NOT NULL,
  type TEXT,
  currency TEXT NOT NULL,
  last_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL,
  posted_at TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  currency TEXT NOT NULL,
  description TEXT,
  merchant TEXT,
  source_txn_id TEXT,
  fingerprint TEXT NOT NULL,
  pending INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, source_txn_id) ON CONFLICT IGNORE
);

-- Fallback uniqueness when source_txn_id is NULL:
CREATE UNIQUE INDEX IF NOT EXISTS ux_txn_fallback
ON transactions(account_id, posted_at, amount_cents, fingerprint)
WHERE source_txn_id IS NULL;

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ran_at TEXT NOT NULL,
  lookback_days INTEGER NOT NULL,
  txns_fetched INTEGER NOT NULL,
  txns_inserted INTEGER NOT NULL,
  txns_updated INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS subscription_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL,
  merchant_norm TEXT NOT NULL,
  amount_median_cents INTEGER NOT NULL,
  interval_days INTEGER NOT NULL,
  confidence REAL NOT NULL,
  last_seen_at TEXT NOT NULL,
  next_expected_at TEXT,
  monthly_cost_estimate_cents INTEGER NOT NULL,
  details_json TEXT
);

CREATE TABLE IF NOT EXISTS anomalies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  txn_id INTEGER NOT NULL,
  type TEXT NOT NULL,
  severity TEXT NOT NULL,
  details_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(txn_id) REFERENCES transactions(id)
);

-- Alert actions: user feedback on detected alerts
CREATE TABLE IF NOT EXISTS alert_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_key TEXT NOT NULL UNIQUE,  -- Unique identifier: "type|merchant|amount|date"
  action TEXT NOT NULL,             -- "ack", "not_suspicious", "confirmed", "canceled"
  merchant_norm TEXT,               -- For learning across similar transactions
  pattern_type TEXT,                -- "duplicate_charge", "unusual_amount", etc.
  notes TEXT,                       -- User notes
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Index for quick lookups
CREATE INDEX IF NOT EXISTS idx_alert_actions_merchant ON alert_actions(merchant_norm);
CREATE INDEX IF NOT EXISTS idx_alert_actions_key ON alert_actions(alert_key);

-- Merchant learning: remember user preferences for merchants
CREATE TABLE IF NOT EXISTS merchant_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  merchant_pattern TEXT NOT NULL,   -- Substring or exact match
  rule_type TEXT NOT NULL,          -- "trust", "suspicious", "ignore_duplicates", "income"
  created_at TEXT NOT NULL,
  UNIQUE(merchant_pattern, rule_type)
);

-- Manual type overrides for recurring charges (subscription vs bill)
CREATE TABLE IF NOT EXISTS recurring_type_overrides (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  merchant_norm TEXT NOT NULL UNIQUE,  -- Normalized merchant name
  override_type TEXT NOT NULL,         -- "subscription", "bill", "auto" (auto = use ML)
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Manual category overrides for merchants
CREATE TABLE IF NOT EXISTS category_overrides (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  merchant_norm TEXT NOT NULL UNIQUE,  -- Normalized merchant name
  category_id TEXT NOT NULL,           -- Category ID (e.g., "healthcare", "shopping")
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Transaction type overrides (INCOME, EXPENSE, TRANSFER, REFUND, CREDIT_OTHER)
-- Supports both fingerprint-specific and merchant pattern overrides
CREATE TABLE IF NOT EXISTS txn_type_overrides (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT,                    -- Specific transaction (NULL for merchant pattern)
  merchant_pattern TEXT,               -- Merchant substring (NULL for fingerprint)
  target_type TEXT NOT NULL,           -- INCOME, EXPENSE, TRANSFER, REFUND, CREDIT_OTHER
  reason TEXT,                         -- User note explaining override
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(fingerprint),
  UNIQUE(merchant_pattern)
);

-- Index for fast fingerprint lookup
CREATE INDEX IF NOT EXISTS idx_txn_type_overrides_fp ON txn_type_overrides(fingerprint);
"""


def _utcnow_iso() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: str, check_same_thread: bool = True) -> sqlite3.Connection:
    """
    Connect to SQLite database.

    Args:
        db_path: Path to the database file
        check_same_thread: Set to False for multi-threaded access (e.g., FastAPI)
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    # Migration: add pending column if it doesn't exist
    try:
        conn.execute("ALTER TABLE transactions ADD COLUMN pending INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()


def upsert_accounts(conn: sqlite3.Connection, accounts: Iterable[Account]) -> None:
    now = _utcnow_iso()
    for a in accounts:
        conn.execute(
            """
            INSERT INTO accounts(account_id, institution, name, type, currency, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
              institution=excluded.institution,
              name=excluded.name,
              type=excluded.type,
              currency=excluded.currency,
              last_seen_at=excluded.last_seen_at
            """,
            (a.account_id, a.institution, a.name, a.type, a.currency, now),
        )
    conn.commit()


def upsert_transactions(conn: sqlite3.Connection, txns: Iterable[Transaction]) -> tuple[int, int]:
    inserted = 0
    updated = 0
    now = _utcnow_iso()

    for t in txns:
        pending_int = 1 if t.pending else 0
        if t.source_txn_id:
            # Update first - use <> instead of IS NOT for non-NULL comparisons
            cur_upd = conn.execute(
                """
                UPDATE transactions
                SET posted_at=?, amount_cents=?, currency=?, description=?, merchant=?,
                    fingerprint=?, pending=?, updated_at=?
                WHERE account_id=? AND source_txn_id=?
                AND (
                    posted_at <> ?
                    OR amount_cents <> ?
                    OR currency <> ?
                    OR COALESCE(description, '') <> COALESCE(?, '')
                    OR COALESCE(merchant, '') <> COALESCE(?, '')
                    OR fingerprint <> ?
                    OR pending <> ?
                )
                """,
                (
                    t.posted_at.isoformat(), t.amount_cents, t.currency, t.description, t.merchant,
                    t.fingerprint, pending_int, now, t.account_id, t.source_txn_id,
                    # comparison params
                    t.posted_at.isoformat(), t.amount_cents, t.currency, t.description, t.merchant, t.fingerprint, pending_int
                ),
            )
            if cur_upd.rowcount > 0:
                updated += 1
                continue

            # Insert if missing
            cur_ins = conn.execute(
                """
                INSERT OR IGNORE INTO transactions(
                  account_id, posted_at, amount_cents, currency, description, merchant,
                  source_txn_id, fingerprint, pending, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    t.account_id, t.posted_at.isoformat(), t.amount_cents, t.currency,
                    t.description, t.merchant, t.source_txn_id, t.fingerprint, pending_int, now, now
                ),
            )
            if cur_ins.rowcount == 1:
                inserted += 1
        else:
            cur_ins = conn.execute(
                """
                INSERT OR IGNORE INTO transactions(
                  account_id, posted_at, amount_cents, currency, description, merchant,
                  source_txn_id, fingerprint, pending, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    t.account_id, t.posted_at.isoformat(), t.amount_cents, t.currency,
                    t.description, t.merchant, t.fingerprint, pending_int, now, now
                ),
            )
            if cur_ins.rowcount == 1:
                inserted += 1

    conn.commit()
    return inserted, updated


def record_run(conn: sqlite3.Connection, lookback_days: int, fetched: int, ins: int, upd: int) -> None:
    conn.execute(
        "INSERT INTO runs(ran_at, lookback_days, txns_fetched, txns_inserted, txns_updated) VALUES (?, ?, ?, ?, ?)",
        (_utcnow_iso(), lookback_days, fetched, ins, upd),
    )
    conn.commit()


def save_alert_action(
    conn: sqlite3.Connection,
    alert_key: str,
    action: str,
    merchant_norm: str | None = None,
    pattern_type: str | None = None,
    notes: str | None = None,
) -> None:
    """Save or update an alert action."""
    now = _utcnow_iso()
    conn.execute(
        """
        INSERT INTO alert_actions(alert_key, action, merchant_norm, pattern_type, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(alert_key) DO UPDATE SET
          action=excluded.action,
          notes=excluded.notes,
          updated_at=excluded.updated_at
        """,
        (alert_key, action, merchant_norm, pattern_type, notes, now, now),
    )
    conn.commit()


def get_alert_actions(conn: sqlite3.Connection) -> dict[str, str]:
    """Get all alert actions as a dict of alert_key -> action."""
    rows = conn.execute("SELECT alert_key, action FROM alert_actions").fetchall()
    return {r["alert_key"]: r["action"] for r in rows}


def get_actioned_alert_keys(conn: sqlite3.Connection) -> set[str]:
    """Get set of alert keys that have been acknowledged (ack)."""
    rows = conn.execute(
        "SELECT alert_key FROM alert_actions WHERE action = 'ack'"
    ).fetchall()
    return {r["alert_key"] for r in rows}


def mark_income_source(conn: sqlite3.Connection, merchant_pattern: str, is_income: bool) -> None:
    """
    Mark a merchant pattern as income or not-income.

    is_income=True: This is real income (paychecks, etc.)
    is_income=False: This is NOT income (transfers, refunds, etc.) - exclude from income totals
    """
    now = _utcnow_iso()
    merchant = merchant_pattern.lower().strip()
    rule_type = "income" if is_income else "not_income"

    # Remove any existing rule for this merchant (both income and not_income)
    conn.execute(
        "DELETE FROM merchant_rules WHERE merchant_pattern = ? AND rule_type IN ('income', 'not_income')",
        (merchant,),
    )

    # Add the new rule
    conn.execute(
        """
        INSERT INTO merchant_rules(merchant_pattern, rule_type, created_at)
        VALUES (?, ?, ?)
        """,
        (merchant, rule_type, now),
    )
    conn.commit()


def get_income_rules(conn: sqlite3.Connection) -> tuple[set[str], set[str]]:
    """
    Get merchant rules for income classification.

    Returns: (income_sources, excluded_sources)
    - income_sources: merchants explicitly marked as income
    - excluded_sources: merchants explicitly marked as NOT income (transfers, refunds)
    """
    rows = conn.execute(
        "SELECT merchant_pattern, rule_type FROM merchant_rules WHERE rule_type IN ('income', 'not_income')"
    ).fetchall()

    income_sources = set()
    excluded_sources = set()

    for r in rows:
        if r["rule_type"] == "income":
            income_sources.add(r["merchant_pattern"])
        else:
            excluded_sources.add(r["merchant_pattern"])

    return income_sources, excluded_sources


def get_income_sources(conn: sqlite3.Connection) -> set[str]:
    """Get set of merchant patterns marked as income sources."""
    income, _ = get_income_rules(conn)
    return income


def learn_from_alert_action(
    conn: sqlite3.Connection,
    merchant_norm: str,
    pattern_type: str,
    action: str,
) -> None:
    """
    Learn from user's alert action.

    When user dismisses alerts multiple times, create a rule to suppress similar future alerts.
    - not_suspicious: trust this pattern for this merchant
    - confirmed: flag this merchant as suspicious
    """
    now = _utcnow_iso()

    # Count how many times user dismissed this pattern for this merchant
    count_result = conn.execute(
        """
        SELECT COUNT(*) as cnt FROM alert_actions
        WHERE merchant_norm = ? AND pattern_type = ? AND action = 'not_suspicious'
        """,
        (merchant_norm, pattern_type),
    ).fetchone()

    dismiss_count = count_result["cnt"] if count_result else 0

    # After 2+ dismissals of same pattern type for same merchant, auto-trust
    if action == "not_suspicious" and dismiss_count >= 1:
        # Create rule to suppress this pattern type for this merchant
        rule_type = f"trust_{pattern_type}"
        conn.execute(
            """
            INSERT OR REPLACE INTO merchant_rules(merchant_pattern, rule_type, created_at)
            VALUES (?, ?, ?)
            """,
            (merchant_norm, rule_type, now),
        )
        conn.commit()


def get_suppressed_patterns(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """
    Get patterns to suppress per merchant.

    Returns: dict mapping merchant_norm -> set of suppressed pattern_types
    """
    rows = conn.execute(
        """
        SELECT merchant_pattern, rule_type FROM merchant_rules
        WHERE rule_type LIKE 'trust_%'
        """
    ).fetchall()

    suppressed: dict[str, set[str]] = {}
    for r in rows:
        merchant = r["merchant_pattern"]
        pattern_type = r["rule_type"].replace("trust_", "")
        if merchant not in suppressed:
            suppressed[merchant] = set()
        suppressed[merchant].add(pattern_type)

    return suppressed


def get_trusted_merchants(conn: sqlite3.Connection) -> set[str]:
    """
    Get merchants that are fully trusted (all alert types suppressed).
    """
    rows = conn.execute(
        "SELECT merchant_pattern FROM merchant_rules WHERE rule_type = 'trust'"
    ).fetchall()
    return {r["merchant_pattern"] for r in rows}


def set_recurring_type_override(
    conn: sqlite3.Connection,
    merchant_norm: str,
    override_type: str,
) -> None:
    """
    Set manual type override for a recurring charge.

    override_type:
      - "subscription": Force as subscription
      - "bill": Force as bill
      - "ignore": Dismiss from recurring lists entirely
      - "auto": Remove override, let ML decide
    """
    now = _utcnow_iso()
    merchant = merchant_norm.lower().strip()

    if override_type == "auto":
        # Remove override - let ML decide
        conn.execute(
            "DELETE FROM recurring_type_overrides WHERE merchant_norm = ?",
            (merchant,),
        )
    else:
        conn.execute(
            """
            INSERT INTO recurring_type_overrides(merchant_norm, override_type, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(merchant_norm) DO UPDATE SET
              override_type=excluded.override_type,
              updated_at=excluded.updated_at
            """,
            (merchant, override_type, now, now),
        )
    conn.commit()


def get_recurring_type_overrides(conn: sqlite3.Connection) -> dict[str, str]:
    """
    Get all manual type overrides for recurring charges.

    Returns: dict mapping merchant_norm -> override_type ("subscription" or "bill")
    """
    rows = conn.execute(
        "SELECT merchant_norm, override_type FROM recurring_type_overrides"
    ).fetchall()
    return {r["merchant_norm"]: r["override_type"] for r in rows}


def set_category_override(
    conn: sqlite3.Connection,
    merchant_norm: str,
    category_id: str,
) -> None:
    """
    Set manual category override for a merchant.

    category_id: Category ID like "healthcare", "shopping", etc. Use "auto" to remove override.
    """
    now = _utcnow_iso()
    merchant = merchant_norm.lower().strip()

    if category_id == "auto":
        # Remove override - let ML decide
        conn.execute(
            "DELETE FROM category_overrides WHERE merchant_norm = ?",
            (merchant,),
        )
    else:
        conn.execute(
            """
            INSERT INTO category_overrides(merchant_norm, category_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(merchant_norm) DO UPDATE SET
              category_id=excluded.category_id,
              updated_at=excluded.updated_at
            """,
            (merchant, category_id, now, now),
        )
    conn.commit()


def get_category_overrides(conn: sqlite3.Connection) -> dict[str, str]:
    """
    Get all manual category overrides.

    Returns: dict mapping merchant_norm -> category_id
    """
    rows = conn.execute(
        "SELECT merchant_norm, category_id FROM category_overrides"
    ).fetchall()
    return {r["merchant_norm"]: r["category_id"] for r in rows}


def dismiss_duplicate(conn: sqlite3.Connection, merchant_norm: str) -> None:
    """
    Dismiss duplicate warning for a merchant.
    """
    now = _utcnow_iso()
    merchant = merchant_norm.lower().strip()
    conn.execute(
        """
        INSERT INTO merchant_rules(merchant_pattern, rule_type, created_at)
        VALUES (?, 'ignore_duplicates', ?)
        ON CONFLICT(merchant_pattern, rule_type) DO NOTHING
        """,
        (merchant, now),
    )
    conn.commit()


def undismiss_duplicate(conn: sqlite3.Connection, merchant_norm: str) -> None:
    """
    Remove duplicate dismissal for a merchant.
    """
    merchant = merchant_norm.lower().strip()
    conn.execute(
        "DELETE FROM merchant_rules WHERE merchant_pattern = ? AND rule_type = 'ignore_duplicates'",
        (merchant,),
    )
    conn.commit()


def get_dismissed_duplicates(conn: sqlite3.Connection) -> set[str]:
    """
    Get all merchants with dismissed duplicate warnings.
    """
    rows = conn.execute(
        "SELECT merchant_pattern FROM merchant_rules WHERE rule_type = 'ignore_duplicates'"
    ).fetchall()
    return {r["merchant_pattern"] for r in rows}


# ---------------------------------------------------------------------------
# Transaction Type Overrides
# ---------------------------------------------------------------------------
# Valid transaction types (from reporting_models.TransactionType)
VALID_TXN_TYPES = frozenset(["INCOME", "EXPENSE", "TRANSFER", "REFUND", "CREDIT_OTHER"])


def set_txn_type_override_fingerprint(
    conn: sqlite3.Connection,
    fingerprint: str,
    target_type: str,
    reason: str | None = None,
) -> None:
    """
    Set transaction type override for a specific transaction by fingerprint.

    Args:
        fingerprint: Transaction fingerprint
        target_type: One of INCOME, EXPENSE, TRANSFER, REFUND, CREDIT_OTHER
        reason: Optional user note
    """
    if target_type not in VALID_TXN_TYPES:
        raise ValueError(f"Invalid target_type: {target_type}. Must be one of {VALID_TXN_TYPES}")

    now = _utcnow_iso()
    conn.execute(
        """
        INSERT INTO txn_type_overrides(fingerprint, merchant_pattern, target_type, reason, created_at, updated_at)
        VALUES (?, NULL, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
          target_type=excluded.target_type,
          reason=excluded.reason,
          updated_at=excluded.updated_at
        """,
        (fingerprint, target_type, reason, now, now),
    )
    conn.commit()


def set_txn_type_override_merchant(
    conn: sqlite3.Connection,
    merchant_pattern: str,
    target_type: str,
    reason: str | None = None,
) -> None:
    """
    Set transaction type override for all transactions matching merchant pattern.

    Args:
        merchant_pattern: Merchant substring to match
        target_type: One of INCOME, EXPENSE, TRANSFER, REFUND, CREDIT_OTHER
        reason: Optional user note
    """
    if target_type not in VALID_TXN_TYPES:
        raise ValueError(f"Invalid target_type: {target_type}. Must be one of {VALID_TXN_TYPES}")

    now = _utcnow_iso()
    pattern = merchant_pattern.lower().strip()
    conn.execute(
        """
        INSERT INTO txn_type_overrides(fingerprint, merchant_pattern, target_type, reason, created_at, updated_at)
        VALUES (NULL, ?, ?, ?, ?, ?)
        ON CONFLICT(merchant_pattern) DO UPDATE SET
          target_type=excluded.target_type,
          reason=excluded.reason,
          updated_at=excluded.updated_at
        """,
        (pattern, target_type, reason, now, now),
    )
    conn.commit()


def remove_txn_type_override(
    conn: sqlite3.Connection,
    fingerprint: str | None = None,
    merchant_pattern: str | None = None,
) -> None:
    """
    Remove a transaction type override.

    Specify either fingerprint OR merchant_pattern, not both.
    """
    if fingerprint and merchant_pattern:
        raise ValueError("Specify either fingerprint or merchant_pattern, not both")

    if fingerprint:
        conn.execute(
            "DELETE FROM txn_type_overrides WHERE fingerprint = ?",
            (fingerprint,),
        )
    elif merchant_pattern:
        pattern = merchant_pattern.lower().strip()
        conn.execute(
            "DELETE FROM txn_type_overrides WHERE merchant_pattern = ?",
            (pattern,),
        )
    conn.commit()


def get_txn_type_overrides(
    conn: sqlite3.Connection,
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Get all transaction type overrides.

    Returns:
        Tuple of (fingerprint_overrides, merchant_overrides)
        Each is a dict mapping fingerprint/pattern -> target_type
    """
    rows = conn.execute(
        "SELECT fingerprint, merchant_pattern, target_type FROM txn_type_overrides"
    ).fetchall()

    fp_overrides: dict[str, str] = {}
    merchant_overrides: dict[str, str] = {}

    for row in rows:
        if row["fingerprint"]:
            fp_overrides[row["fingerprint"]] = row["target_type"]
        elif row["merchant_pattern"]:
            merchant_overrides[row["merchant_pattern"]] = row["target_type"]

    return fp_overrides, merchant_overrides


def get_txn_type_override(
    conn: sqlite3.Connection,
    fingerprint: str,
    merchant_norm: str,
) -> str | None:
    """
    Get the effective transaction type override for a transaction.

    Checks fingerprint first, then merchant patterns.

    Returns:
        Target type string, or None if no override
    """
    # Check fingerprint override first
    row = conn.execute(
        "SELECT target_type FROM txn_type_overrides WHERE fingerprint = ?",
        (fingerprint,),
    ).fetchone()
    if row:
        return row["target_type"]

    # Check merchant patterns
    rows = conn.execute(
        "SELECT merchant_pattern, target_type FROM txn_type_overrides WHERE merchant_pattern IS NOT NULL"
    ).fetchall()

    for row in rows:
        if row["merchant_pattern"] in merchant_norm:
            return row["target_type"]

    return None