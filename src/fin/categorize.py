# categorize.py
"""
Smart transaction categorization engine.

Uses pattern matching and rule-based classification to categorize transactions
into standard financial categories.
"""
import re
import sqlite3
from dataclasses import dataclass
from typing import Callable


@dataclass
class Category:
    """A transaction category."""
    id: str           # Short identifier: "food", "utilities", etc.
    name: str         # Display name: "Food & Dining"
    icon: str         # Emoji/icon for display
    color: str        # CSS color variable


# Standard categories
CATEGORIES: dict[str, Category] = {
    "income": Category("income", "Income", "💰", "var(--accent-green)"),
    "one_time_deposit": Category("one_time_deposit", "One-Time Deposits", "💵", "var(--accent-green)"),
    "housing": Category("housing", "Housing & Rent", "🏠", "var(--accent-blue)"),
    "debt_payment": Category("debt_payment", "Debt Payments", "🏦", "var(--accent-red)"),
    "utilities": Category("utilities", "Utilities", "⚡", "var(--accent-yellow)"),
    "groceries": Category("groceries", "Groceries", "🛒", "var(--accent-green)"),
    "dining": Category("dining", "Dining & Restaurants", "🍽️", "var(--accent-yellow)"),
    "transport": Category("transport", "Transportation", "🚗", "var(--accent-blue)"),
    "entertainment": Category("entertainment", "Entertainment", "🎬", "var(--accent-purple)"),
    "shopping": Category("shopping", "Shopping", "🛍️", "var(--accent-purple)"),
    "health": Category("health", "Health & Medical", "🏥", "var(--accent-red)"),
    "insurance": Category("insurance", "Insurance", "🛡️", "var(--accent-blue)"),
    "subscriptions": Category("subscriptions", "Subscriptions", "📱", "var(--accent-purple)"),
    "travel": Category("travel", "Travel", "✈️", "var(--accent-blue)"),
    "education": Category("education", "Education", "📚", "var(--accent-blue)"),
    "personal": Category("personal", "Personal Care", "💇", "var(--accent-purple)"),
    "pets": Category("pets", "Pets & Pet Care", "🐾", "var(--accent-yellow)"),
    "gifts": Category("gifts", "Gifts & Donations", "🎁", "var(--accent-green)"),
    "fees": Category("fees", "Fees & Charges", "💳", "var(--accent-red)"),
    "transfer": Category("transfer", "Transfers", "↔️", "var(--text-muted)"),
    "other": Category("other", "Other", "📦", "var(--text-secondary)"),
}


# Pattern-based categorization rules
# Each rule is (regex_pattern, category_id, confidence_score)
CATEGORIZATION_RULES: list[tuple[str, str, float]] = [
    # Income patterns (recurring wages/salary)
    (r"(payroll|direct deposit|paycheck|salary|unemployment)", "income", 0.95),

    # One-time deposits (refunds, reimbursements, selling items, etc.)
    (r"(refund|rebate|cashback|cash back|reimbursement|reimburse)", "one_time_deposit", 0.9),
    (r"(sold|sale proceeds|ebay|poshmark|mercari|offerup|craigslist)", "one_time_deposit", 0.85),
    (r"(insurance claim|insurance payout|settlement)", "one_time_deposit", 0.9),
    (r"(gift|inheritance|bonus|award|prize|lottery|winnings)", "one_time_deposit", 0.85),
    (r"(tax refund|irs treas|state tax)", "one_time_deposit", 0.9),
    (r"(returned|credit|adjustment|reversal)", "one_time_deposit", 0.7),

    # Housing (rent only - mortgage is debt)
    (r"(rent|lease|apartment|property|hoa|homeowner)", "housing", 0.9),

    # Debt Payments - MUST come before other categories to catch CC payments, loans, mortgages
    (r"(mortgage|home loan|mtg pmt)", "debt_payment", 0.95),
    (r"(loan|lending|student loan|auto loan|car loan|personal loan)", "debt_payment", 0.95),
    (r"(credit card|card payment|card pmt|payment.*thank)", "debt_payment", 0.95),
    # Note: "autopay" removed - too generic, used by insurance companies too
    (r"(chase card|citi card|discover card|capital one card|amex|american express)", "debt_payment", 0.95),
    (r"(bank of america|wells fargo|synchrony|barclays).*(payment|pmt)", "debt_payment", 0.9),
    (r"(payment to|pmt to|pay to).*bank", "debt_payment", 0.85),

    # Utilities - Note: "gas" changed to "natural gas|gas bill|gas co" to avoid matching gas stations
    (r"(electric|power|natural gas|gas bill|gas co\b|one gas|water|sewage|trash|waste|utility|utilities)", "utilities", 0.9),
    (r"(comcast|xfinity|at&t|verizon|t-mobile|spectrum|cox|centurylink|google fiber)", "utilities", 0.9),
    (r"(city of \w+)(?!.*\b(hall|limit|cashless|parking|airport)\b)", "utilities", 0.85),  # Municipal utilities
    (r"(internet|cable|phone|wireless|mobile|telecom)", "utilities", 0.8),

    # Groceries
    (r"(grocery|groceries|supermarket|whole foods|trader joe|kroger|safeway|publix|aldi|costco|sam's club|walmart supercenter|target)", "groceries", 0.9),
    (r"(h-e-b|heb|wegmans|food lion|giant|stop.?shop|sprouts|meijer)", "groceries", 0.9),

    # Dining
    (r"(restaurant|cafe|coffee|starbucks|dunkin|mcdonald|burger|pizza|taco|chipotle|subway|panera|chick-fil-a|wendy)", "dining", 0.9),
    (r"(doordash|uber eats|grubhub|postmates|seamless|caviar)", "dining", 0.85),
    (r"(bar|pub|brewery|tavern|grill)", "dining", 0.8),

    # Transportation
    (r"(uber|lyft|taxi|cab|transit|metro|bus|train|amtrak|parking)", "transport", 0.9),
    (r"(gas station|shell|chevron|exxon|mobil|bp\b|arco|76 gas|76 station|speedway|wawa)", "transport", 0.9),
    (r"(car wash|auto|tire|oil change|mechanic|jiffy lube)", "transport", 0.85),
    (r"(toll|dmv|registration)", "transport", 0.8),

    # Entertainment
    (r"(netflix|hulu|disney|hbo|paramount|peacock|apple tv|amazon prime video|spotify|pandora|youtube|twitch)", "entertainment", 0.95),
    (r"(movie|cinema|theater|theatre|amc|regal)", "entertainment", 0.9),
    (r"(game|playstation|xbox|steam|nintendo|epic games)", "entertainment", 0.9),
    (r"(concert|ticket|ticketmaster|stubhub|eventbrite|live nation)", "entertainment", 0.85),

    # Shopping
    (r"(amazon(?! prime video)|walmart|target|best buy|home depot|lowe's|ikea|wayfair)", "shopping", 0.8),
    (r"(ebay|etsy|wish|aliexpress|shein)", "shopping", 0.85),
    (r"(clothing|apparel|shoes|fashion|zara|h&m|gap|old navy|nike|adidas)", "shopping", 0.85),

    # Health
    (r"(pharmacy|cvs|walgreens|rite aid|drug|medication|rx)", "health", 0.9),
    (r"(doctor|medical|hospital|clinic|urgent care|dental|dentist|vision|optom)", "health", 0.9),
    (r"(carenow|carespot|minute clinic|nextcare|patient first|medexpress)", "health", 0.95),
    (r"(health|healthcare|fitness|gym|planet fitness|la fitness|equinox|peloton)", "health", 0.85),

    # Insurance
    (r"(insurance|geico|state farm|allstate|progressive|liberty mutual|usaa)", "insurance", 0.95),
    (r"(insurance premium|coverage|policy)", "insurance", 0.7),

    # Subscriptions (general software/services)
    (r"(adobe|microsoft|office|dropbox|google one|icloud|onedrive)", "subscriptions", 0.9),
    (r"(patreon|substack|medium|linkedin premium|github)", "subscriptions", 0.85),
    (r"(membership|subscription|recurring|monthly fee)", "subscriptions", 0.7),

    # Travel
    (r"(airline|united|delta|american|southwest|jetblue|spirit|frontier)", "travel", 0.95),
    (r"(hotel|marriott|hilton|hyatt|airbnb|vrbo|motel|inn)", "travel", 0.9),
    (r"(expedia|booking|kayak|priceline|trivago)", "travel", 0.9),

    # Education
    (r"(university|college|tuition|school|education|course|udemy|coursera|skillshare)", "education", 0.9),
    (r"(textbook|student loan|sallie mae|navient)", "education", 0.85),

    # Personal care
    (r"(salon|spa|barber|haircut|nail|massage|wax)", "personal", 0.9),
    (r"(beauty|cosmetic|sephora|ulta)", "personal", 0.85),

    # Pets & Pet Care
    (r"(petco|petsmart|pet supplies plus|chewy|pet food|pet store)", "pets", 0.95),
    (r"(veterinar|vet clinic|animal hospital|animal clinic|banfield|vca\b)", "pets", 0.95),
    (r"(dog food|cat food|pet meds|pet pharmacy|1800petmeds)", "pets", 0.9),
    (r"(groomer|pet grooming|dog wash|doggy daycare|pet boarding|pet hotel)", "pets", 0.9),
    (r"(rover|wag\b|pet sit|dog walk|bark box|barkbox)", "pets", 0.85),

    # Gifts & donations
    (r"(charity|donation|donate|nonprofit|foundation|red cross|unicef)", "gifts", 0.9),
    (r"(gift|present|flowers|1-800)", "gifts", 0.7),

    # Fees
    (r"(fee|charge|interest|overdraft|late fee|penalty|finance charge)", "fees", 0.85),
    (r"(atm|withdrawal|service charge)", "fees", 0.8),

    # Transfers (should be excluded from expenses)
    (r"(transfer|zelle|venmo|paypal|cash app|wire|ach)", "transfer", 0.85),
    (r"(credit card payment|payment to|bill pay)", "transfer", 0.9),
]


def categorize_merchant(merchant_norm: str, description: str = "") -> tuple[str, float]:
    """
    Categorize a transaction based on merchant name and description.

    Returns: (category_id, confidence_score)
    """
    text = f"{merchant_norm} {description}".lower()

    best_category = "other"
    best_confidence = 0.0

    for pattern, category_id, confidence in CATEGORIZATION_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            if confidence > best_confidence:
                best_category = category_id
                best_confidence = confidence

    return best_category, best_confidence


def categorize_transactions(
    conn: sqlite3.Connection,
    start_date: str,
    end_date: str,
    overrides: dict[str, str] | None = None,
    account_filter: list[str] | None = None,
) -> dict[str, list[tuple[str, int, str]]]:
    """
    Categorize all transactions in a date range.

    Manual overrides take precedence over ML/rule-based categorization.

    Returns: dict mapping category_id to list of (merchant, amount_cents, date)
    """
    # Get manual overrides if not provided
    if overrides is None:
        from . import db as dbmod
        overrides = dbmod.get_category_overrides(conn)

    # Build query with optional account filter
    sql = """
        SELECT
            posted_at,
            amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), ''))) AS merchant_norm,
            COALESCE(description, '') AS description
        FROM transactions
        WHERE posted_at >= ? AND posted_at <= ?
    """
    params: list = [start_date, end_date]

    if account_filter is not None:
        if not account_filter:  # Empty list = no accounts
            return {cat: [] for cat in CATEGORIES}
        placeholders = ",".join("?" * len(account_filter))
        sql += f" AND account_id IN ({placeholders})"
        params.extend(account_filter)

    sql += " ORDER BY posted_at DESC"
    rows = conn.execute(sql, params).fetchall()

    by_category: dict[str, list[tuple[str, int, str]]] = {cat: [] for cat in CATEGORIES}

    for r in rows:
        amount = r["amount_cents"]
        merchant = r["merchant_norm"]
        description = r["description"]
        date_str = r["posted_at"]

        # Check for manual override first (works for both income and expenses)
        override = overrides.get(merchant.lower())
        if override and override in CATEGORIES:
            cat_id = override
        elif amount > 0:
            # Positive amount: check if it's a one-time deposit or regular income
            cat_id, confidence = categorize_merchant(merchant, description)
            # If no specific match or matched as expense category, default to income
            if cat_id not in ("income", "one_time_deposit") or confidence == 0:
                cat_id = "income"
        else:
            # Negative amount: categorize as expense
            cat_id, _ = categorize_merchant(merchant, description)

        by_category[cat_id].append((merchant, amount, date_str))

    return by_category


def get_category_breakdown(
    conn: sqlite3.Connection,
    start_date: str,
    end_date: str,
    account_filter: list[str] | None = None,
) -> list[tuple[Category, int, int]]:
    """
    Get spending breakdown by category.

    Returns: list of (Category, total_cents, transaction_count) sorted by total
    """
    categorized = categorize_transactions(conn, start_date, end_date, account_filter=account_filter)

    breakdown = []
    for cat_id, transactions in categorized.items():
        if cat_id in ("income", "transfer", "one_time_deposit"):
            continue  # Skip income, transfers, and refunds/credits for expense breakdown

        # Only count negative amounts (actual expenses)
        expense_txns = [t for t in transactions if t[1] < 0]
        total = sum(abs(t[1]) for t in expense_txns)
        count = len(expense_txns)

        if total > 0:
            breakdown.append((CATEGORIES[cat_id], total, count))

    # Sort by total descending
    breakdown.sort(key=lambda x: -x[1])
    return breakdown


def get_top_merchants_by_category(
    conn: sqlite3.Connection,
    start_date: str,
    end_date: str,
    category_id: str,
    limit: int = 10,
) -> list[tuple[str, int, int]]:
    """
    Get top merchants in a category.

    Returns: list of (merchant, total_cents, count) sorted by total
    """
    categorized = categorize_transactions(conn, start_date, end_date)
    transactions = categorized.get(category_id, [])

    # Aggregate by merchant
    by_merchant: dict[str, tuple[int, int]] = {}
    for merchant, amount, _ in transactions:
        total, count = by_merchant.get(merchant, (0, 0))
        by_merchant[merchant] = (total + abs(amount), count + 1)

    # Sort and limit
    sorted_merchants = sorted(by_merchant.items(), key=lambda x: -x[1][0])
    return [(m, t, c) for m, (t, c) in sorted_merchants[:limit]]
