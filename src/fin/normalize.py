# normalize.py
import hashlib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional
from zoneinfo import ZoneInfo
from .models import Transaction


def _get_timezone() -> ZoneInfo:
    """Get configured timezone, defaulting to UTC."""
    from .config import load_config
    try:
        cfg = load_config()
        return ZoneInfo(cfg.timezone)
    except Exception:
        return ZoneInfo("UTC")


def _norm_text(s: Optional[str]) -> str:
    if not s:
        return ""
    return " ".join(s.strip().upper().split())


def fingerprint_txn(account_id: str, posted_at: date, amount_cents: int, merchant: str, desc: str) -> str:
    """
    Generate a fingerprint hash for transaction deduplication.
    
    The fingerprint contains NO raw sensitive data; it's a hash of normalized fields.
    """
    payload = f"{account_id}|{posted_at.isoformat()}|{amount_cents}|{merchant}|{desc}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_amount_to_cents(amount: Any) -> int:
    """
    Convert a dollar amount to cents using Decimal for precision.

    SimpleFIN returns amounts as floats representing dollars (e.g., -12.99 for
    a $12.99 charge). This function converts to integer cents for storage.

    Uses Decimal to avoid floating-point precision errors (e.g., 12.99 * 100
    becoming 1298.9999999... instead of 1299).

    Args:
        amount: Dollar amount as int, float, or string (e.g., -12.99, "-12.99", -12)

    Returns:
        Integer cents (e.g., -1299)

    Raises:
        ValueError: If amount is None or an unsupported type
        ValueError: If amount exceeds sanity bounds (> $1M or < -$1M)

    Examples:
        >>> parse_amount_to_cents(-12.99)
        -1299
        >>> parse_amount_to_cents("100.00")
        10000
        >>> parse_amount_to_cents(50)
        5000
    """
    if amount is None:
        raise ValueError("Amount cannot be None")

    # Convert to Decimal for precise calculation (avoid float rounding errors)
    try:
        if isinstance(amount, str):
            dollars = Decimal(amount.strip().replace(",", ""))
        elif isinstance(amount, float):
            # Convert float to string first to avoid float precision issues
            dollars = Decimal(str(amount))
        elif isinstance(amount, int):
            dollars = Decimal(amount)
        else:
            raise ValueError(f"Unsupported amount type: {type(amount).__name__}")
    except (InvalidOperation, AttributeError) as e:
        raise ValueError(f"Cannot parse amount '{amount}': {e}") from e

    # Sanity check: flag suspiciously large amounts (likely already in cents)
    if abs(dollars) > 1_000_000:
        raise ValueError(
            f"Amount {dollars} exceeds $1M sanity limit. "
            f"If this is intentional, use parse_amount_to_cents_unchecked(). "
            f"If the source provides cents, divide by 100 before calling."
        )

    # Multiply by 100 and round using ROUND_HALF_UP (standard financial rounding)
    # This avoids banker's rounding (HALF_EVEN) which can cause 1¢ surprises
    cents = (dollars * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


def parse_amount_to_cents_unchecked(amount: Any) -> int:
    """
    Convert dollar amount to cents without sanity bounds checking.

    Uses Decimal for precision. Use only when you've verified the source
    provides dollar amounts and values over $1M are expected (e.g., business
    accounts, real estate).
    """
    if amount is None:
        raise ValueError("Amount cannot be None")

    # Convert to Decimal for precise calculation
    if isinstance(amount, str):
        dollars = Decimal(amount.strip().replace(",", ""))
    elif isinstance(amount, float):
        dollars = Decimal(str(amount))
    elif isinstance(amount, int):
        dollars = Decimal(amount)
    else:
        raise ValueError(f"Unsupported amount type: {type(amount).__name__}")

    # Use ROUND_HALF_UP for standard financial rounding
    cents = (dollars * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


def normalize_simplefin_txn(raw: dict, account_id: str) -> Transaction:
    """
    Normalize a raw SimpleFIN transaction dict into a Transaction model.
    
    Args:
        raw: Raw transaction dict from SimpleFIN API
        account_id: The account ID this transaction belongs to
    
    Returns:
        Normalized Transaction object
    
    Raises:
        KeyError: If no recognizable date field is present
        ValueError: If amount cannot be parsed
    """
    # SimpleFIN commonly uses "transacted_at" (epoch seconds). Fall back to ISO keys if present.
    # Use configured timezone to ensure consistent date extraction regardless of server TZ.
    if "transacted_at" in raw:
        tz = _get_timezone()
        posted_at = datetime.fromtimestamp(int(raw["transacted_at"]), tz=tz).date()
    elif "posted_at" in raw:
        posted_at = date.fromisoformat(raw["posted_at"])
    elif "date" in raw:
        posted_at = date.fromisoformat(raw["date"])
    else:
        raise KeyError("No recognizable date field in transaction")

    # Parse amount with context for error messages
    raw_amount = raw.get("amount")
    try:
        amount_cents = parse_amount_to_cents(raw_amount)
    except ValueError as e:
        txn_id = raw.get("id") or raw.get("transaction_id") or "unknown"
        raise ValueError(
            f"Failed to parse amount for transaction {txn_id}: {e}"
        ) from e

    currency = raw.get("currency", "USD")

    # Description/merchant fields vary; keep both if available.
    desc = raw.get("description") or raw.get("memo") or raw.get("name")
    merch = raw.get("payee") or raw.get("merchant") or raw.get("counterparty")

    source_txn_id = raw.get("id") or raw.get("transaction_id")

    # Check for pending status - SimpleFIN uses "pending" boolean
    pending = raw.get("pending", False)
    if isinstance(pending, str):
        pending = pending.lower() in ("true", "1", "yes")

    fp = fingerprint_txn(account_id, posted_at, amount_cents, _norm_text(merch), _norm_text(desc))

    return Transaction(
        account_id=account_id,
        posted_at=posted_at,
        amount_cents=amount_cents,
        currency=currency,
        description=desc,
        merchant=merch,
        source_txn_id=source_txn_id,
        fingerprint=fp,
        pending=pending,
    )


def sanitize_csv_field(value: Any) -> str:
    """
    Sanitize a value for safe CSV export to prevent formula injection.

    When opened in Excel/Sheets, cells starting with =, +, -, @, tab, or CR
    can be interpreted as formulas. Prefix with apostrophe to neutralize.

    Args:
        value: Any value to sanitize (will be converted to string)

    Returns:
        Sanitized string safe for CSV export
    """
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + s
    return s