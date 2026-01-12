import sqlite3
from pathlib import Path
from typing import Iterable
from datetime import datetime
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
"""

def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()

def upsert_accounts(conn: sqlite3.Connection, accounts: Iterable[Account]) -> None:
    now = datetime.utcnow().isoformat()
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
    now = datetime.utcnow().isoformat()

    for t in txns:
        if t.source_txn_id:
            # Update first
            cur_upd = conn.execute(
                """
                UPDATE transactions
                SET posted_at=?, amount_cents=?, currency=?, description=?, merchant=?,
                    fingerprint=?, updated_at=?
                WHERE account_id=? AND source_txn_id=?
                AND (
                    posted_at IS NOT ?
                    OR amount_cents IS NOT ?
                    OR currency IS NOT ?
                    OR COALESCE(description,'') IS NOT COALESCE(?, '')
                    OR COALESCE(merchant,'') IS NOT COALESCE(?, '')
                    OR fingerprint IS NOT ?
                )
                """,
                (
                    t.posted_at.isoformat(), t.amount_cents, t.currency, t.description, t.merchant,
                    t.fingerprint, now, t.account_id, t.source_txn_id,
                    # comparison params
                    t.posted_at.isoformat(), t.amount_cents, t.currency, t.description, t.merchant, t.fingerprint
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
                  source_txn_id, fingerprint, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    t.account_id, t.posted_at.isoformat(), t.amount_cents, t.currency,
                    t.description, t.merchant, t.source_txn_id, t.fingerprint, now, now
                ),
            )
            if cur_ins.rowcount == 1:
                inserted += 1
        else:
            cur_ins = conn.execute(
                """
                INSERT OR IGNORE INTO transactions(
                  account_id, posted_at, amount_cents, currency, description, merchant,
                  source_txn_id, fingerprint, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    t.account_id, t.posted_at.isoformat(), t.amount_cents, t.currency,
                    t.description, t.merchant, t.fingerprint, now, now
                ),
            )
            if cur_ins.rowcount == 1:
                inserted += 1

    conn.commit()
    return inserted, updated

def record_run(conn: sqlite3.Connection, lookback_days: int, fetched: int, ins: int, upd: int) -> None:
    conn.execute(
        "INSERT INTO runs(ran_at, lookback_days, txns_fetched, txns_inserted, txns_updated) VALUES (?, ?, ?, ?, ?)",
        (datetime.utcnow().isoformat(), lookback_days, fetched, ins, upd),
    )
    conn.commit()
