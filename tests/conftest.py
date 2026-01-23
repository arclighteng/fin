"""
Test fixtures for fin financial application.

Provides realistic financial data for thorough testing of:
- Sketchy charge detection
- Duplicate subscription detection
- Income vs spend analysis
- Period calculations
"""
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Generator

import pytest

from fin import db as dbmod


@pytest.fixture
def temp_db_path() -> Generator[str, None, None]:
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def empty_db(temp_db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """Create an empty initialized database."""
    conn = dbmod.connect(temp_db_path)
    dbmod.init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def populated_db(temp_db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """
    Create a database populated with realistic financial transactions.

    Includes:
    - Regular income (bi-weekly paycheck)
    - Monthly subscriptions (Netflix, Spotify, gym)
    - Annual subscriptions (Amazon Prime, domain renewal)
    - Habitual spending (groceries, gas, coffee)
    - One-off purchases
    - Transfers (credit card payments)
    - Sketchy patterns for testing detection
    - Duplicate subscription scenarios
    """
    conn = dbmod.connect(temp_db_path)
    dbmod.init_db(conn)

    today = date.today()
    txn_counter = [0]  # Use list to allow mutation in nested function

    # Helper to insert transactions
    def insert_txn(
        posted_at: date,
        amount_cents: int,
        merchant: str,
        description: str = "",
        account_id: str = "acct_checking",
    ):
        txn_counter[0] += 1
        conn.execute(
            """
            INSERT INTO transactions (
                account_id, posted_at, amount_cents, currency,
                description, merchant, source_txn_id, fingerprint,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'USD', ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                account_id,
                posted_at.isoformat(),
                amount_cents,
                description,
                merchant,
                f"txn_{posted_at.isoformat()}_{merchant}_{amount_cents}_{txn_counter[0]}",
                f"fp_{posted_at.isoformat()}_{merchant}_{amount_cents}_{txn_counter[0]}",
            ),
        )

    # =========================================================================
    # INCOME - Bi-weekly paychecks for 6 months
    # =========================================================================
    paycheck_amount = 250000  # $2,500
    for weeks_ago in range(0, 26, 2):  # Every 2 weeks for 6 months
        d = today - timedelta(weeks=weeks_ago)
        insert_txn(d, paycheck_amount, "ACME CORP PAYROLL", "Direct Deposit")

    # Side income - monthly freelance
    for months_ago in range(6):
        d = today - timedelta(days=months_ago * 30 + 5)
        insert_txn(d, 50000, "FREELANCE CLIENT", "Consulting")  # $500

    # =========================================================================
    # MONTHLY SUBSCRIPTIONS - Regular patterns
    # =========================================================================
    # Netflix - $15.99/month
    for months_ago in range(12):
        d = today - timedelta(days=months_ago * 30 + 3)
        insert_txn(d, -1599, "NETFLIX.COM", "Streaming")

    # Spotify - $10.99/month
    for months_ago in range(12):
        d = today - timedelta(days=months_ago * 30 + 7)
        insert_txn(d, -1099, "SPOTIFY", "Music")

    # Gym - $49.99/month
    for months_ago in range(12):
        d = today - timedelta(days=months_ago * 30 + 1)
        insert_txn(d, -4999, "PLANET FITNESS", "Gym membership")

    # =========================================================================
    # ANNUAL SUBSCRIPTIONS
    # =========================================================================
    # Amazon Prime - $139/year (charged 5 months ago)
    insert_txn(today - timedelta(days=150), -13900, "AMAZON PRIME", "Annual membership")
    insert_txn(today - timedelta(days=515), -13900, "AMAZON PRIME", "Annual membership")

    # Domain renewal - $15/year
    insert_txn(today - timedelta(days=60), -1500, "NAMECHEAP", "Domain renewal")
    insert_txn(today - timedelta(days=425), -1500, "NAMECHEAP", "Domain renewal")

    # =========================================================================
    # WEEKLY SUBSCRIPTIONS
    # =========================================================================
    # Meal kit - $59.99/week
    for weeks_ago in range(20):
        d = today - timedelta(weeks=weeks_ago)
        insert_txn(d, -5999, "HELLOFRESH", "Meal kit")

    # =========================================================================
    # HABITUAL SPENDING (frequent but irregular)
    # =========================================================================
    # Groceries - varies, ~2x/week
    import random
    random.seed(42)  # Reproducible
    for days_ago in range(180):
        if random.random() < 0.3:  # ~30% chance each day
            amount = random.randint(3500, 15000)  # $35-$150
            d = today - timedelta(days=days_ago)
            insert_txn(d, -amount, "KROGER", "Groceries")

    # Coffee - small amounts, frequent
    for days_ago in range(90):
        if random.random() < 0.4:
            d = today - timedelta(days=days_ago)
            insert_txn(d, -random.randint(450, 750), "STARBUCKS", "Coffee")

    # Gas - ~weekly
    for weeks_ago in range(26):
        d = today - timedelta(weeks=weeks_ago, days=random.randint(0, 2))
        insert_txn(d, -random.randint(4000, 6500), "SHELL", "Gas")

    # =========================================================================
    # TRANSFERS (should be excluded from expense analysis)
    # =========================================================================
    for months_ago in range(6):
        d = today - timedelta(days=months_ago * 30 + 15)
        insert_txn(d, -150000, "CHASE CREDIT CARD PAYMENT", "CC Payment")
        insert_txn(d, -50000, "TRANSFER TO SAVINGS", "Internal transfer")

    # =========================================================================
    # ONE-OFF PURCHASES
    # =========================================================================
    insert_txn(today - timedelta(days=10), -25000, "BEST BUY", "Electronics")
    insert_txn(today - timedelta(days=45), -8999, "TARGET", "Household items")
    insert_txn(today - timedelta(days=75), -34500, "HOME DEPOT", "Home repair")

    # =========================================================================
    # SKETCHY CHARGE PATTERNS (for detection testing)
    # =========================================================================

    # 1. DUPLICATE CHARGE - Same merchant + amount within 3 days
    insert_txn(today - timedelta(days=5), -2999, "SKETCHY MERCHANT A", "Purchase")
    insert_txn(today - timedelta(days=3), -2999, "SKETCHY MERCHANT A", "Purchase")

    # 2. UNUSUAL AMOUNT - Way above median (Netflix usually $15.99, suddenly $159.90)
    insert_txn(today - timedelta(days=2), -15990, "NETFLIX.COM", "Streaming")

    # 3. TEST CHARGE - Small amounts $0.01-$1.00
    insert_txn(today - timedelta(days=8), -1, "UNKNOWN VENDOR", "Test")
    insert_txn(today - timedelta(days=12), -100, "MYSTERY CHARGE", "Verification")

    # 4. ROUND AMOUNT SPIKE - First charge from new merchant is exact $100
    insert_txn(today - timedelta(days=15), -10000, "BRAND NEW MERCHANT", "First purchase")

    # 5. RAPID-FIRE CHARGES - 3+ from same merchant in 24h
    insert_txn(today - timedelta(days=20), -1500, "RAPID MERCHANT", "Charge 1")
    insert_txn(today - timedelta(days=20), -1500, "RAPID MERCHANT", "Charge 2")
    insert_txn(today - timedelta(days=20), -1500, "RAPID MERCHANT", "Charge 3")
    insert_txn(today - timedelta(days=20), -1500, "RAPID MERCHANT", "Charge 4")

    # 6. REFUND + RECHARGE - Refund followed by similar charge
    insert_txn(today - timedelta(days=25), 5000, "REFUND MERCHANT", "Refund")
    insert_txn(today - timedelta(days=22), -4800, "REFUND MERCHANT", "New charge")

    # =========================================================================
    # DUPLICATE SUBSCRIPTION PATTERNS
    # =========================================================================

    # Fuzzy match: NETFLIX vs NETFLIX.COM (already have NETFLIX.COM above)
    # Add another Netflix variant
    for months_ago in range(6):
        d = today - timedelta(days=months_ago * 30 + 10)
        insert_txn(d, -1599, "NETFLIX INC", "Streaming service")

    # Bundle family: Disney + Hulu (both from Disney bundle)
    for months_ago in range(8):
        d = today - timedelta(days=months_ago * 30 + 12)
        insert_txn(d, -1399, "DISNEY PLUS", "Streaming")
    for months_ago in range(8):
        d = today - timedelta(days=months_ago * 30 + 12)
        insert_txn(d, -1799, "HULU", "Streaming")

    # Similar pattern: Two $9.99 monthly subscriptions
    for months_ago in range(6):
        d = today - timedelta(days=months_ago * 30 + 8)
        insert_txn(d, -999, "SERVICE ALPHA", "Monthly sub")
    for months_ago in range(6):
        d = today - timedelta(days=months_ago * 30 + 9)
        insert_txn(d, -999, "SERVICE BETA", "Monthly sub")

    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def minimal_db(temp_db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """Create a database with minimal data for edge case testing."""
    conn = dbmod.connect(temp_db_path)
    dbmod.init_db(conn)

    today = date.today()

    # Just a few transactions
    conn.execute(
        """
        INSERT INTO transactions (
            account_id, posted_at, amount_cents, currency,
            description, merchant, fingerprint, created_at, updated_at
        ) VALUES
        (?, ?, ?, 'USD', ?, ?, ?, datetime('now'), datetime('now')),
        (?, ?, ?, 'USD', ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            "acct1", today.isoformat(), 100000, "Income", "EMPLOYER", "fp1",
            "acct1", today.isoformat(), -5000, "Purchase", "STORE", "fp2",
        ),
    )
    conn.commit()
    yield conn
    conn.close()


# =========================================================================
# EXPECTED VALUES FOR ASSERTION
# =========================================================================

@pytest.fixture
def expected_sketchy_patterns() -> dict:
    """Expected sketchy charge patterns in the populated_db."""
    return {
        "duplicate_charge": 1,      # SKETCHY MERCHANT A x2
        "unusual_amount": 1,        # Netflix $159.90 vs usual $15.99
        "test_charge": 2,           # $0.01 and $1.00 charges
        "round_amount_spike": 1,    # BRAND NEW MERCHANT $100
        "rapid_fire": 1,            # RAPID MERCHANT 4x in one day
        "refund_recharge": 1,       # REFUND MERCHANT pattern
    }


@pytest.fixture
def expected_subscription_counts() -> dict:
    """Expected subscription detection counts."""
    return {
        "monthly": 6,   # Netflix, Netflix Inc, Spotify, Gym, Disney, Hulu, Service Alpha, Service Beta
        "annual": 2,    # Amazon Prime, Namecheap
        "weekly": 1,    # HelloFresh
    }


# =========================================================================
# CONSTANTS FOR TESTING
# =========================================================================

# Amounts in cents for precision testing
CENTS_PRECISION_CASES = [
    (15990, "$159.90"),
    (1, "$0.01"),
    (100, "$1.00"),
    (999, "$9.99"),
    (1599, "$15.99"),
    (100000, "$1,000.00"),
    (0, "$0.00"),
    (-1599, "-$15.99"),
]

# Period boundary test cases
PERIOD_BOUNDARY_CASES = [
    # (date, expected_month_start, expected_month_end)
    (date(2026, 1, 15), date(2026, 1, 1), date(2026, 2, 1)),
    (date(2026, 2, 28), date(2026, 2, 1), date(2026, 3, 1)),
    (date(2026, 12, 31), date(2026, 12, 1), date(2027, 1, 1)),
    (date(2025, 1, 1), date(2025, 1, 1), date(2025, 2, 1)),
]

QUARTER_BOUNDARY_CASES = [
    # (date, expected_quarter_start, expected_quarter_end)
    (date(2026, 1, 15), date(2026, 1, 1), date(2026, 4, 1)),   # Q1
    (date(2026, 4, 1), date(2026, 4, 1), date(2026, 7, 1)),    # Q2
    (date(2026, 7, 31), date(2026, 7, 1), date(2026, 10, 1)),  # Q3
    (date(2026, 12, 31), date(2026, 10, 1), date(2027, 1, 1)), # Q4
]
