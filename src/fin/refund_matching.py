# refund_matching.py
"""
Refund matching engine.

TRUTH CONTRACT:
- REFUND = Matched refund to prior expense
- Refunds are positive amounts that reduce net spend
- Matched refunds must be same merchant, similar amount, within time window
- Refunds should net against the expense category

Matching criteria:
1. Same or similar merchant (normalized)
2. Positive amount (credit)
3. Amount <= original expense
4. Within 90 days of original expense
"""
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional


# ---------------------------------------------------------------------------
# Refund Keywords
# ---------------------------------------------------------------------------
REFUND_KEYWORDS = frozenset([
    "refund", "credit", "return", "reversal", "chargeback",
    "adjustment", "rebate", "reimbursement", "cashback",
])


def _has_refund_keyword(merchant_norm: str) -> bool:
    """Check if description suggests a refund."""
    for keyword in REFUND_KEYWORDS:
        if keyword in merchant_norm:
            return True
    return False


# ---------------------------------------------------------------------------
# Refund Match Data Structures
# ---------------------------------------------------------------------------
@dataclass
class RefundMatch:
    """A matched refund to expense pair."""
    refund_fingerprint: str
    expense_fingerprint: str
    refund_amount_cents: int
    expense_amount_cents: int
    merchant_norm: str
    days_apart: int
    confidence: float
    match_reason: str

    @property
    def is_full_refund(self) -> bool:
        """Check if refund fully covers the expense."""
        return self.refund_amount_cents >= abs(self.expense_amount_cents)

    @property
    def is_partial_refund(self) -> bool:
        """Check if this is a partial refund."""
        return 0 < self.refund_amount_cents < abs(self.expense_amount_cents)


@dataclass
class RefundMatchingResult:
    """Result of refund matching analysis."""
    matched_refunds: list[RefundMatch] = field(default_factory=list)
    unmatched_refunds: list[str] = field(default_factory=list)  # Fingerprints

    def get_matched_fingerprints(self) -> set[str]:
        """Get all fingerprints that are matched refunds."""
        return {m.refund_fingerprint for m in self.matched_refunds}

    def get_refund_for_expense(self, expense_fp: str) -> Optional[RefundMatch]:
        """Get refund match for a specific expense."""
        for match in self.matched_refunds:
            if match.expense_fingerprint == expense_fp:
                return match
        return None

    def get_expense_for_refund(self, refund_fp: str) -> Optional[str]:
        """Get the expense fingerprint that a refund matches."""
        for match in self.matched_refunds:
            if match.refund_fingerprint == refund_fp:
                return match.expense_fingerprint
        return None


# ---------------------------------------------------------------------------
# Main Matching Function
# ---------------------------------------------------------------------------
def detect_refund_matches(
    conn: sqlite3.Connection,
    start_date: date,
    end_date: date,
    lookback_days: int = 90,
    amount_tolerance_percent: float = 5.0,
) -> RefundMatchingResult:
    """
    Detect refunds that match prior expenses.

    Looks for positive transactions (credits) that match:
    - Same or similar merchant as a prior expense
    - Amount <= expense amount (within tolerance)
    - Within lookback_days of the expense

    Args:
        start_date: Start of analysis period
        end_date: End of analysis period (exclusive)
        lookback_days: How far back to look for matching expenses
        amount_tolerance_percent: Allowed percentage difference in amounts

    Returns:
        RefundMatchingResult with matched refunds and unmatched credits
    """
    # Get potential refunds (positive amounts in date range)
    refunds = conn.execute(
        """
        SELECT
            t.fingerprint,
            t.account_id,
            t.posted_at,
            t.amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(t.merchant,''), NULLIF(t.description,''), ''))) AS merchant_norm
        FROM transactions t
        WHERE t.posted_at >= ? AND t.posted_at < ?
          AND t.amount_cents > 0
          AND COALESCE(t.pending, 0) = 0
        ORDER BY t.posted_at DESC
        """,
        (start_date.isoformat(), end_date.isoformat()),
    ).fetchall()

    # Get potential expense matches (negative amounts going back further)
    expense_start = (start_date - timedelta(days=lookback_days)).isoformat()
    expenses = conn.execute(
        """
        SELECT
            t.fingerprint,
            t.account_id,
            t.posted_at,
            t.amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(t.merchant,''), NULLIF(t.description,''), ''))) AS merchant_norm
        FROM transactions t
        WHERE t.posted_at >= ? AND t.posted_at < ?
          AND t.amount_cents < 0
          AND COALESCE(t.pending, 0) = 0
        ORDER BY t.posted_at DESC
        """,
        (expense_start, end_date.isoformat()),
    ).fetchall()

    result = RefundMatchingResult()
    matched_expense_fps: set[str] = set()

    for refund_row in refunds:
        refund_fp = refund_row["fingerprint"]
        refund_date = datetime.fromisoformat(refund_row["posted_at"]).date()
        refund_amount = refund_row["amount_cents"]
        refund_merchant = refund_row["merchant_norm"]

        # Check if this looks like a refund
        is_refund_like = _has_refund_keyword(refund_merchant)

        best_match: Optional[dict] = None
        best_score: float = 0.0

        for expense_row in expenses:
            expense_fp = expense_row["fingerprint"]
            if expense_fp in matched_expense_fps:
                continue  # Already matched

            expense_date = datetime.fromisoformat(expense_row["posted_at"]).date()
            expense_amount = abs(expense_row["amount_cents"])
            expense_merchant = expense_row["merchant_norm"]

            # Check date constraint: refund must be AFTER expense
            days_apart = (refund_date - expense_date).days
            if days_apart < 0 or days_apart > lookback_days:
                continue

            # Check merchant similarity
            merchant_match = _merchants_match(refund_merchant, expense_merchant)
            if not merchant_match and not is_refund_like:
                continue  # Merchants must match unless it has refund keyword

            # Check amount constraint: refund <= expense (with tolerance)
            tolerance = int(expense_amount * amount_tolerance_percent / 100)
            if refund_amount > expense_amount + tolerance:
                continue  # Refund larger than expense - unlikely match

            # Calculate match score
            score = 0.0

            # Merchant match contributes 40%
            if merchant_match:
                score += 0.4

            # Refund keyword contributes 20%
            if is_refund_like:
                score += 0.2

            # Amount similarity contributes 30%
            amount_diff_pct = abs(refund_amount - expense_amount) / expense_amount * 100
            if amount_diff_pct < 1:
                score += 0.3
            elif amount_diff_pct < 5:
                score += 0.2
            elif amount_diff_pct < 20:
                score += 0.1

            # Date proximity contributes 10%
            if days_apart <= 7:
                score += 0.1
            elif days_apart <= 30:
                score += 0.05

            if score > best_score and score >= 0.4:
                best_score = score
                best_match = {
                    "expense_fp": expense_fp,
                    "expense_amount": expense_row["amount_cents"],
                    "expense_merchant": expense_merchant,
                    "days_apart": days_apart,
                    "score": score,
                }

        if best_match:
            match = RefundMatch(
                refund_fingerprint=refund_fp,
                expense_fingerprint=best_match["expense_fp"],
                refund_amount_cents=refund_amount,
                expense_amount_cents=best_match["expense_amount"],
                merchant_norm=refund_merchant,
                days_apart=best_match["days_apart"],
                confidence=best_match["score"],
                match_reason=_match_reason(best_match, is_refund_like),
            )
            result.matched_refunds.append(match)
            matched_expense_fps.add(best_match["expense_fp"])
        else:
            # Unmatched potential refund
            if is_refund_like:
                result.unmatched_refunds.append(refund_fp)

    return result


def _merchants_match(merchant1: str, merchant2: str) -> bool:
    """Check if two merchants are similar enough to be the same."""
    import re

    if not merchant1 or not merchant2:
        return False

    # Exact match
    if merchant1 == merchant2:
        return True

    # One contains the other (handles "AMAZON" vs "AMAZON MARKETPLACE")
    if merchant1 in merchant2 or merchant2 in merchant1:
        return True

    # Extract significant words (split on spaces and punctuation)
    def get_words(s):
        return [w for w in re.split(r'[\s\.\-_]+', s) if len(w) > 2]

    words1 = get_words(merchant1)
    words2 = get_words(merchant2)

    # First significant word matches (handles "AMAZON.COM" vs "AMAZON PRIME")
    if words1 and words2 and words1[0] == words2[0]:
        return True

    return False


def _match_reason(match: dict, has_refund_keyword: bool) -> str:
    """Generate human-readable match reason."""
    parts = []

    if has_refund_keyword:
        parts.append("refund keyword")

    if match["days_apart"] == 0:
        parts.append("same day")
    elif match["days_apart"] <= 7:
        parts.append(f"{match['days_apart']}d apart")
    else:
        parts.append(f"{match['days_apart']}d apart")

    return ", ".join(parts) if parts else "merchant match"


# ---------------------------------------------------------------------------
# Category Netting
# ---------------------------------------------------------------------------
@dataclass
class CategoryNetAmount:
    """Net amount for a category after refunds."""
    category_id: str
    gross_expense_cents: int
    refund_cents: int

    @property
    def net_cents(self) -> int:
        return self.gross_expense_cents - self.refund_cents


def compute_category_net_amounts(
    conn: sqlite3.Connection,
    start_date: date,
    end_date: date,
    refund_result: RefundMatchingResult,
) -> dict[str, CategoryNetAmount]:
    """
    Compute net amounts per category after applying matched refunds.

    Refunds reduce the expense amount for the category of the original expense.

    Returns:
        Dict mapping category_id -> CategoryNetAmount
    """
    from .categorize import get_category_breakdown

    # Get base expense breakdown by category
    breakdown = get_category_breakdown(
        conn,
        start_date.isoformat(),
        end_date.isoformat(),
    )

    result: dict[str, CategoryNetAmount] = {}

    # Initialize with gross expenses
    for item in breakdown:
        result[item["id"]] = CategoryNetAmount(
            category_id=item["id"],
            gross_expense_cents=item["amount_cents"],
            refund_cents=0,
        )

    # Get expense fingerprints and their categories
    expense_categories = _get_expense_categories(conn, refund_result, end_date)

    # Apply refunds to their categories
    for match in refund_result.matched_refunds:
        category_id = expense_categories.get(match.expense_fingerprint)
        if category_id and category_id in result:
            result[category_id].refund_cents += match.refund_amount_cents

    return result


def _get_expense_categories(
    conn: sqlite3.Connection,
    refund_result: RefundMatchingResult,
    end_date: date,
) -> dict[str, str]:
    """Get category for each matched expense fingerprint."""
    if not refund_result.matched_refunds:
        return {}

    expense_fps = [m.expense_fingerprint for m in refund_result.matched_refunds]

    from .categorize import categorize_transaction

    result = {}
    for fp in expense_fps:
        row = conn.execute(
            """
            SELECT
                t.amount_cents,
                TRIM(LOWER(COALESCE(NULLIF(t.merchant,''), NULLIF(t.description,''), ''))) AS merchant_norm,
                t.description
            FROM transactions t
            WHERE t.fingerprint = ?
            """,
            (fp,),
        ).fetchone()

        if row:
            cat_id, _ = categorize_transaction(
                amount_cents=row["amount_cents"],
                merchant_norm=row["merchant_norm"],
                description=row["description"] or "",
                category_overrides={},
            )
            result[fp] = cat_id

    return result


# ---------------------------------------------------------------------------
# Refund Match Storage
# ---------------------------------------------------------------------------
def store_refund_matches(
    conn: sqlite3.Connection,
    result: RefundMatchingResult,
) -> None:
    """
    Store refund matches in the database.

    Creates or updates a refund_matches table.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS refund_matches (
            refund_fingerprint TEXT PRIMARY KEY,
            expense_fingerprint TEXT NOT NULL,
            confidence REAL,
            match_reason TEXT,
            created_at TEXT NOT NULL
        )
    """)

    now = datetime.now().isoformat()

    for match in result.matched_refunds:
        conn.execute(
            """
            INSERT INTO refund_matches (refund_fingerprint, expense_fingerprint, confidence, match_reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(refund_fingerprint) DO UPDATE SET
                expense_fingerprint = excluded.expense_fingerprint,
                confidence = excluded.confidence,
                match_reason = excluded.match_reason
            """,
            (match.refund_fingerprint, match.expense_fingerprint, match.confidence, match.match_reason, now),
        )

    conn.commit()


def get_matched_expense_for_refund(
    conn: sqlite3.Connection,
    refund_fingerprint: str,
) -> Optional[str]:
    """Get the expense fingerprint that a refund is matched to."""
    row = conn.execute(
        "SELECT expense_fingerprint FROM refund_matches WHERE refund_fingerprint = ?",
        (refund_fingerprint,),
    ).fetchone()
    return row[0] if row else None
