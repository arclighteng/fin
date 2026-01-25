# transfer_pairing.py
"""
Transfer pairing engine with tolerance matching.

TRUTH CONTRACT:
- Matched transfers (both legs identified) net to $0
- Unmatched transfers are flagged for resolution
- Transfer_in and transfer_out are tracked separately
- Pair IDs link both legs of a transfer

Transfer detection:
1. Opposite amounts on different accounts within date tolerance
2. At least one side matches transfer patterns (keywords, bank names)
3. Amount tolerance allows for ACH fees (typically $0-$3)
"""
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from .reporting_models import TransferStatus


# ---------------------------------------------------------------------------
# Transfer Keywords
# ---------------------------------------------------------------------------
TRANSFER_KEYWORDS = frozenset([
    "transfer", "xfer", "ach", "wire", "zelle", "venmo", "paypal",
    "cash app", "cashapp", "apple cash", "square cash", "wisely",
    "online banking transfer", "mobile deposit",
])

BANK_KEYWORDS = frozenset([
    "chase", "wells fargo", "bank of america", "bofa", "citi", "citibank",
    "capital one", "us bank", "pnc", "td bank", "ally", "discover",
    "american express", "amex", "barclays", "synchrony", "marcus",
    "fidelity", "schwab", "vanguard", "betterment", "wealthfront",
    "savings", "checking", "brokerage",
])

CC_PAYMENT_KEYWORDS = frozenset([
    "payment thank you", "autopay", "online payment", "payment received",
    "credit card payment", "cc payment", "automatic payment",
])


def _is_transfer_pattern(merchant_norm: str) -> bool:
    """Check if merchant matches transfer patterns."""
    for keyword in TRANSFER_KEYWORDS:
        if keyword in merchant_norm:
            return True
    return False


def _is_bank_pattern(merchant_norm: str) -> bool:
    """Check if merchant contains bank-related keywords as whole words."""
    import re
    for keyword in BANK_KEYWORDS:
        # Use word boundary matching to avoid "purchase" matching "chase"
        pattern = r'\b' + re.escape(keyword) + r'\b'
        if re.search(pattern, merchant_norm):
            return True
    return False


def _is_cc_payment(merchant_norm: str) -> bool:
    """Check if merchant matches credit card payment patterns."""
    for keyword in CC_PAYMENT_KEYWORDS:
        if keyword in merchant_norm:
            return True
    return False


# ---------------------------------------------------------------------------
# Transfer Pair Data Structures
# ---------------------------------------------------------------------------
@dataclass
class TransferLeg:
    """One leg of a transfer."""
    fingerprint: str
    account_id: str
    posted_at: date
    amount_cents: int
    merchant_norm: str
    is_outflow: bool


@dataclass
class TransferPair:
    """A matched transfer pair."""
    pair_id: str
    outflow: TransferLeg
    inflow: TransferLeg
    confidence: float
    match_reason: str
    amount_diff_cents: int  # Usually 0 or small ACH fee

    @property
    def net_cents(self) -> int:
        """Net should be 0 (or close to it for ACH fees)."""
        return self.inflow.amount_cents + self.outflow.amount_cents

    @property
    def is_balanced(self) -> bool:
        """Check if transfer is balanced (net near zero)."""
        return abs(self.net_cents) <= 500  # Allow $5 tolerance


@dataclass
class TransferPairingResult:
    """Result of transfer pairing analysis."""
    matched_pairs: list[TransferPair] = field(default_factory=list)
    unmatched_outflows: list[TransferLeg] = field(default_factory=list)
    unmatched_inflows: list[TransferLeg] = field(default_factory=list)

    def get_paired_fingerprints(self) -> set[str]:
        """Get all fingerprints that are part of matched pairs."""
        result = set()
        for pair in self.matched_pairs:
            result.add(pair.outflow.fingerprint)
            result.add(pair.inflow.fingerprint)
        return result

    def get_pair_id(self, fingerprint: str) -> Optional[str]:
        """Get pair ID for a fingerprint, if it's part of a pair."""
        for pair in self.matched_pairs:
            if fingerprint in (pair.outflow.fingerprint, pair.inflow.fingerprint):
                return pair.pair_id
        return None

    @property
    def has_unmatched(self) -> bool:
        """Check if there are unmatched transfer-like transactions."""
        return bool(self.unmatched_outflows) or bool(self.unmatched_inflows)


# ---------------------------------------------------------------------------
# Main Pairing Function
# ---------------------------------------------------------------------------
def detect_transfer_pairs(
    conn: sqlite3.Connection,
    start_date: date,
    end_date: date,
    tolerance_days: int = 3,
    tolerance_cents: int = 300,  # Allow ~$3 difference for ACH fees
) -> TransferPairingResult:
    """
    Detect internal transfer pairs across accounts.

    When you transfer $1000 from Savings to Checking:
    - Savings shows: -$1000 (outflow)
    - Checking shows: +$1000 (inflow)

    This function finds matching pairs and returns detailed pairing info.

    Args:
        start_date: Start of date range
        end_date: End of date range (exclusive)
        tolerance_days: Maximum days between matching transactions
        tolerance_cents: Maximum cents difference for ACH fee tolerance

    Returns:
        TransferPairingResult with matched pairs and unmatched legs
    """
    # Get all posted (non-pending) transactions in range
    rows = conn.execute(
        """
        SELECT
            t.fingerprint,
            t.account_id,
            t.posted_at,
            t.amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(t.merchant,''), NULLIF(t.description,''), ''))) AS merchant_norm
        FROM transactions t
        WHERE t.posted_at >= ? AND t.posted_at < ?
          AND COALESCE(t.pending, 0) = 0
        ORDER BY t.posted_at, ABS(t.amount_cents) DESC
        """,
        (start_date.isoformat(), end_date.isoformat()),
    ).fetchall()

    # Build transfer legs
    outflows: list[TransferLeg] = []
    inflows: list[TransferLeg] = []

    for r in rows:
        posted = datetime.fromisoformat(r["posted_at"]).date()
        merchant = r["merchant_norm"]

        # Only consider transactions that look like transfers
        looks_like_transfer = (
            _is_transfer_pattern(merchant) or
            _is_bank_pattern(merchant) or
            _is_cc_payment(merchant)
        )

        if not looks_like_transfer:
            continue

        leg = TransferLeg(
            fingerprint=r["fingerprint"],
            account_id=r["account_id"],
            posted_at=posted,
            amount_cents=r["amount_cents"],
            merchant_norm=merchant,
            is_outflow=r["amount_cents"] < 0,
        )

        if r["amount_cents"] < 0:
            outflows.append(leg)
        elif r["amount_cents"] > 0:
            inflows.append(leg)

    # Match pairs
    result = TransferPairingResult()
    matched_outflow_fps: set[str] = set()
    matched_inflow_fps: set[str] = set()

    for outflow in outflows:
        if outflow.fingerprint in matched_outflow_fps:
            continue

        outflow_abs = abs(outflow.amount_cents)
        best_match: Optional[TransferLeg] = None
        best_score: float = 0.0
        best_reason: str = ""

        for inflow in inflows:
            if inflow.fingerprint in matched_inflow_fps:
                continue
            if inflow.account_id == outflow.account_id:
                continue  # Same account - not a transfer

            # Check amount match
            amount_diff = abs(outflow_abs - inflow.amount_cents)
            if amount_diff > tolerance_cents:
                continue

            # Check date proximity
            days_diff = abs((inflow.posted_at - outflow.posted_at).days)
            if days_diff > tolerance_days:
                continue

            # Calculate match score
            # Closer date = higher score
            # Closer amount = higher score
            date_score = 1.0 - (days_diff / (tolerance_days + 1))
            amount_score = 1.0 - (amount_diff / (tolerance_cents + 1))
            score = (date_score * 0.4) + (amount_score * 0.6)

            # Bonus for exact amount match
            if amount_diff == 0:
                score += 0.2

            # Bonus for same-day
            if days_diff == 0:
                score += 0.1

            if score > best_score:
                best_score = score
                best_match = inflow
                best_reason = _match_reason(outflow, inflow, days_diff, amount_diff)

        if best_match and best_score >= 0.5:
            pair_id = str(uuid.uuid4())[:8]  # Short UUID for pair ID

            pair = TransferPair(
                pair_id=pair_id,
                outflow=outflow,
                inflow=best_match,
                confidence=min(1.0, best_score),
                match_reason=best_reason,
                amount_diff_cents=abs(outflow_abs - best_match.amount_cents),
            )
            result.matched_pairs.append(pair)
            matched_outflow_fps.add(outflow.fingerprint)
            matched_inflow_fps.add(best_match.fingerprint)

    # Collect unmatched
    for outflow in outflows:
        if outflow.fingerprint not in matched_outflow_fps:
            result.unmatched_outflows.append(outflow)

    for inflow in inflows:
        if inflow.fingerprint not in matched_inflow_fps:
            result.unmatched_inflows.append(inflow)

    return result


def _match_reason(
    outflow: TransferLeg,
    inflow: TransferLeg,
    days_diff: int,
    amount_diff: int,
) -> str:
    """Generate human-readable match reason."""
    parts = []

    if amount_diff == 0:
        parts.append("exact amount")
    else:
        parts.append(f"±${amount_diff/100:.2f}")

    if days_diff == 0:
        parts.append("same day")
    elif days_diff == 1:
        parts.append("1 day apart")
    else:
        parts.append(f"{days_diff} days apart")

    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Pair ID Storage
# ---------------------------------------------------------------------------
def store_transfer_pairs(
    conn: sqlite3.Connection,
    result: TransferPairingResult,
) -> None:
    """
    Store transfer pair IDs in the database.

    Creates or updates a transfer_pairs table linking fingerprints to pair IDs.
    """
    # Ensure table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transfer_pairs (
            fingerprint TEXT PRIMARY KEY,
            pair_id TEXT NOT NULL,
            status TEXT NOT NULL,  -- MATCHED, SUSPECTED
            confidence REAL,
            match_reason TEXT,
            created_at TEXT NOT NULL
        )
    """)

    now = datetime.now().isoformat()

    for pair in result.matched_pairs:
        status = "MATCHED" if pair.confidence >= 0.8 else "SUSPECTED"

        for fp in [pair.outflow.fingerprint, pair.inflow.fingerprint]:
            conn.execute(
                """
                INSERT INTO transfer_pairs (fingerprint, pair_id, status, confidence, match_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    pair_id = excluded.pair_id,
                    status = excluded.status,
                    confidence = excluded.confidence,
                    match_reason = excluded.match_reason
                """,
                (fp, pair.pair_id, status, pair.confidence, pair.match_reason, now),
            )

    conn.commit()


def get_pair_info(
    conn: sqlite3.Connection,
    fingerprint: str,
) -> Optional[tuple[str, str, float]]:
    """
    Get pair info for a transaction.

    Returns:
        Tuple of (pair_id, status, confidence) or None if not paired
    """
    row = conn.execute(
        "SELECT pair_id, status, confidence FROM transfer_pairs WHERE fingerprint = ?",
        (fingerprint,),
    ).fetchone()

    if row:
        return (row[0], row[1], row[2])
    return None


def get_paired_fingerprint(
    conn: sqlite3.Connection,
    fingerprint: str,
) -> Optional[str]:
    """
    Get the other fingerprint in a transfer pair.

    Returns:
        Fingerprint of the paired transaction, or None if not paired
    """
    # Get pair ID for this fingerprint
    row = conn.execute(
        "SELECT pair_id FROM transfer_pairs WHERE fingerprint = ?",
        (fingerprint,),
    ).fetchone()

    if not row:
        return None

    pair_id = row[0]

    # Get the other fingerprint in this pair
    row = conn.execute(
        "SELECT fingerprint FROM transfer_pairs WHERE pair_id = ? AND fingerprint != ?",
        (pair_id, fingerprint),
    ).fetchone()

    return row[0] if row else None
