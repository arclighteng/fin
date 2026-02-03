"""
Demo data generator for fin.

Creates realistic-looking transaction data so users can explore
the app before connecting SimpleFIN.
"""
import random
import uuid
from datetime import date, timedelta
from typing import Generator

# Demo accounts
DEMO_ACCOUNTS = [
    {
        "account_id": "demo-checking-001",
        "institution": "Demo Bank",
        "name": "Primary Checking",
        "type": "checking",
        "currency": "USD",
    },
    {
        "account_id": "demo-credit-001",
        "institution": "Demo Bank",
        "name": "Rewards Credit Card",
        "type": "credit",
        "currency": "USD",
    },
]

# Subscriptions: (merchant, amount_cents, day_of_month, account_index)
SUBSCRIPTIONS = [
    ("NETFLIX.COM", -1599, 15, 1),
    ("SPOTIFY USA", -1099, 3, 1),
    ("DISNEY PLUS", -1399, 22, 1),
    ("AMAZON PRIME", -1499, 7, 1),  # Monthly
    ("YOUTUBE PREMIUM", -1399, 12, 1),
    ("OPENAI *CHATGPT", -2000, 18, 1),
    ("GITHUB INC", -400, 10, 1),
    ("ICLOUD STORAGE", -299, 1, 1),
    ("NYT DIGITAL", -1700, 25, 1),
]

# Utility bills: (merchant, base_amount, variance, day_of_month, account_index)
BILLS = [
    ("CITY WATER UTILITY", -6500, 2000, 5, 0),  # $45-85
    ("POWER ELECTRIC CO", -12000, 5000, 12, 0),  # $70-170
    ("GAS COMPANY", -8000, 4000, 8, 0),  # $40-120
    ("INTERNET PROVIDER", -7999, 0, 20, 0),  # Fixed $79.99
    ("MOBILE CARRIER", -8500, 0, 15, 0),  # Fixed $85
]

# One-off merchants: (merchant, min_cents, max_cents, frequency_per_month, account_index)
# Note: amounts are negative (expenses), so min is the smaller absolute value
ONE_OFFS = [
    # Groceries - weekly
    ("WHOLE FOODS MKT", -15000, -4000, 4, 1),
    ("TRADER JOES", -8000, -3000, 2, 1),
    ("COSTCO WHSE", -25000, -8000, 1, 1),

    # Dining - frequent
    ("CHIPOTLE", -1800, -1200, 3, 1),
    ("STARBUCKS", -900, -500, 6, 1),
    ("DOORDASH", -5000, -2500, 2, 1),
    ("LOCAL RESTAURANT", -8000, -3000, 2, 1),

    # Gas
    ("SHELL OIL", -6500, -3500, 2, 1),
    ("CHEVRON", -7000, -4000, 2, 1),

    # Shopping
    ("AMAZON.COM", -12000, -1500, 3, 1),
    ("TARGET", -8000, -2000, 1, 1),
    ("WALMART", -10000, -3000, 1, 0),

    # Entertainment
    ("AMC THEATRES", -3500, -1500, 0.5, 1),
    ("STEAM GAMES", -6000, -1000, 0.3, 1),

    # Health
    ("CVS PHARMACY", -5000, -1000, 0.5, 1),
    ("URGENT CARE COPAY", -5000, -5000, 0.2, 1),

    # Transport
    ("UBER", -4000, -1500, 1, 1),
    ("PARKING METER", -1000, -200, 2, 1),
]

# Income: (merchant, amount_cents, day1, day2, account_index) - bi-weekly paycheck
INCOME = [
    ("ACME CORP PAYROLL", 385000, 1, 15, 0),  # $3,850 bi-weekly
]


def _random_date_in_month(year: int, month: int, target_day: int) -> date:
    """Get a date close to target_day, handling month lengths."""
    from calendar import monthrange
    _, last_day = monthrange(year, month)
    actual_day = min(target_day, last_day)
    # Add some variance (-2 to +2 days)
    variance = random.randint(-2, 2)
    actual_day = max(1, min(last_day, actual_day + variance))
    return date(year, month, actual_day)


def generate_demo_transactions(months: int = 12) -> Generator[dict, None, None]:
    """
    Generate demo transactions for the specified number of months.

    Yields transaction dicts ready for database insertion.
    """
    today = date.today()
    start_date = today - timedelta(days=months * 30)

    # Generate month by month
    current = date(start_date.year, start_date.month, 1)

    while current <= today:
        year, month = current.year, current.month

        # Income (bi-weekly, so 2 per month)
        for merchant, amount, day1, day2, acct_idx in INCOME:
            for day in [day1, day2]:
                txn_date = _random_date_in_month(year, month, day)
                if txn_date <= today:
                    yield _make_txn(txn_date, amount, merchant, DEMO_ACCOUNTS[acct_idx]["account_id"])

        # Subscriptions (monthly, fixed amount)
        for merchant, amount, day, acct_idx in SUBSCRIPTIONS:
            txn_date = _random_date_in_month(year, month, day)
            if txn_date <= today:
                yield _make_txn(txn_date, amount, merchant, DEMO_ACCOUNTS[acct_idx]["account_id"])

        # Bills (monthly, variable amount)
        for merchant, base, variance, day, acct_idx in BILLS:
            txn_date = _random_date_in_month(year, month, day)
            if txn_date <= today:
                amount = base + random.randint(-variance, variance)
                yield _make_txn(txn_date, amount, merchant, DEMO_ACCOUNTS[acct_idx]["account_id"])

        # One-off spending (random throughout month)
        for merchant, min_amt, max_amt, freq, acct_idx in ONE_OFFS:
            # Determine number of transactions this month
            if freq < 1:
                count = 1 if random.random() < freq else 0
            else:
                count = int(freq) + (1 if random.random() < (freq % 1) else 0)

            for _ in range(count):
                day = random.randint(1, 28)
                txn_date = _random_date_in_month(year, month, day)
                if txn_date <= today:
                    amount = random.randint(min_amt, max_amt)
                    yield _make_txn(txn_date, amount, merchant, DEMO_ACCOUNTS[acct_idx]["account_id"])

        # Credit card payment (transfer from checking to pay off card)
        if month > start_date.month or year > start_date.year:
            payment_date = _random_date_in_month(year, month, 25)
            if payment_date <= today:
                # Pay between $800 and $2500
                payment = random.randint(80000, 250000)
                yield _make_txn(payment_date, -payment, "CREDIT CARD PAYMENT", DEMO_ACCOUNTS[0]["account_id"])
                yield _make_txn(payment_date, payment, "PAYMENT RECEIVED", DEMO_ACCOUNTS[1]["account_id"])

        # Move to next month
        if month == 12:
            current = date(year + 1, 1, 1)
        else:
            current = date(year, month + 1, 1)

    # Add some demo alerts
    # Duplicate charge (same merchant, same amount, 2 days apart)
    dup_date1 = today - timedelta(days=5)
    dup_date2 = today - timedelta(days=3)
    yield _make_txn(dup_date1, -4599, "STREAMING SERVICE", DEMO_ACCOUNTS[1]["account_id"])
    yield _make_txn(dup_date2, -4599, "STREAMING SERVICE", DEMO_ACCOUNTS[1]["account_id"])

    # Unusual amount (much higher than normal)
    yield _make_txn(today - timedelta(days=7), -8500, "STARBUCKS", DEMO_ACCOUNTS[1]["account_id"])


def _make_txn(txn_date: date, amount_cents: int, merchant: str, account_id: str) -> dict:
    """Create a transaction dict."""
    return {
        "account_id": account_id,
        "posted_at": txn_date.isoformat(),
        "amount_cents": amount_cents,
        "currency": "USD",
        "description": merchant,
        "merchant": merchant,
        "fingerprint": f"demo_{uuid.uuid4().hex[:12]}",
        "pending": 0,
    }


def load_demo_data(conn, months: int = 12) -> tuple[int, int]:
    """
    Load demo data into the database.

    Returns (accounts_created, transactions_created).
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

    # Insert accounts
    accounts_created = 0
    for acct in DEMO_ACCOUNTS:
        try:
            conn.execute(
                """
                INSERT INTO accounts (account_id, institution, name, type, currency, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (acct["account_id"], acct["institution"], acct["name"],
                 acct["type"], acct["currency"], now)
            )
            accounts_created += 1
        except Exception:
            pass  # Account already exists

    # Insert transactions
    transactions_created = 0
    for txn in generate_demo_transactions(months):
        try:
            conn.execute(
                """
                INSERT INTO transactions
                (account_id, posted_at, amount_cents, currency, description, merchant, fingerprint, pending, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (txn["account_id"], txn["posted_at"], txn["amount_cents"], txn["currency"],
                 txn["description"], txn["merchant"], txn["fingerprint"], txn["pending"], now, now)
            )
            transactions_created += 1
        except Exception:
            pass  # Duplicate fingerprint

    conn.commit()

    # Log the demo run
    conn.execute(
        """
        INSERT INTO runs (ran_at, lookback_days, txns_fetched, txns_inserted, txns_updated)
        VALUES (?, ?, ?, ?, ?)
        """,
        (now, months * 30, transactions_created, transactions_created, 0)
    )
    conn.commit()

    return accounts_created, transactions_created
