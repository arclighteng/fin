# money.py
"""
Canonical money handling with ROUND_HALF_UP.

TRUTH CONTRACT:
- All money uses Decimal with ROUND_HALF_UP (standard financial rounding)
- No float arithmetic for money
- All storage in integer cents
- 0.5 always rounds UP (away from zero), never banker's rounding

This is the ONLY module that should perform money parsing and formatting.
"""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Union


# Quantizer for cents (no decimal places)
CENTS_QUANTIZER = Decimal("1")

# Sanity limit for dollar amounts (flag likely-already-cents values)
MAX_DOLLAR_AMOUNT = Decimal("1_000_000")


class MoneyParseError(ValueError):
    """Raised when money parsing fails."""
    pass


def parse_to_cents(amount: Any, *, allow_large: bool = False) -> int:
    """
    Convert a dollar amount to integer cents using ROUND_HALF_UP.

    This is the canonical money parsing function. Uses Decimal to avoid
    floating-point precision errors.

    Args:
        amount: Dollar amount as int, float, or string (e.g., -12.99)
        allow_large: If True, skip the $1M sanity check

    Returns:
        Integer cents (e.g., -1299 for -$12.99)

    Raises:
        MoneyParseError: If amount cannot be parsed or exceeds limits

    Examples:
        >>> parse_to_cents(12.99)
        1299
        >>> parse_to_cents(-12.99)
        -1299
        >>> parse_to_cents("100.00")
        10000
        >>> parse_to_cents(50)
        5000
        >>> parse_to_cents(0.125)  # 12.5 cents rounds UP to 13
        13
        >>> parse_to_cents(-0.125)  # -12.5 cents rounds DOWN (away from zero) to -13
        -13
    """
    if amount is None:
        raise MoneyParseError("Amount cannot be None")

    try:
        if isinstance(amount, str):
            # Handle string amounts: strip whitespace, remove commas
            clean = amount.strip().replace(",", "")
            if not clean:
                raise MoneyParseError("Amount string is empty")
            dollars = Decimal(clean)
        elif isinstance(amount, float):
            # Convert float to string first to preserve displayed precision
            # Avoids issues like 12.99 becoming 12.98999999...
            dollars = Decimal(str(amount))
        elif isinstance(amount, int):
            dollars = Decimal(amount)
        elif isinstance(amount, Decimal):
            dollars = amount
        else:
            raise MoneyParseError(f"Unsupported amount type: {type(amount).__name__}")
    except InvalidOperation as e:
        raise MoneyParseError(f"Cannot parse amount '{amount}': {e}") from e

    # Sanity check for likely-already-cents values
    if not allow_large and abs(dollars) > MAX_DOLLAR_AMOUNT:
        raise MoneyParseError(
            f"Amount {dollars} exceeds ${MAX_DOLLAR_AMOUNT:,} sanity limit. "
            f"If this is intentional, use parse_to_cents(..., allow_large=True). "
            f"If the source provides cents, divide by 100 first."
        )

    # Multiply by 100 and round using ROUND_HALF_UP
    # This is standard financial rounding: 0.5 always rounds away from zero
    cents = (dollars * 100).quantize(CENTS_QUANTIZER, rounding=ROUND_HALF_UP)
    return int(cents)


def cents_to_dollars(cents: int) -> Decimal:
    """
    Convert integer cents to Decimal dollars.

    Args:
        cents: Integer cents (e.g., -1299)

    Returns:
        Decimal dollars with 2 decimal places (e.g., Decimal("-12.99"))
    """
    return Decimal(cents) / 100


def format_usd(cents: int, *, show_sign: bool = False) -> str:
    """
    Format cents as a USD string.

    Args:
        cents: Integer cents (e.g., -1299)
        show_sign: If True, always show + or - sign

    Returns:
        Formatted string (e.g., "$12.99", "-$12.99", "+$12.99")

    Examples:
        >>> format_usd(1299)
        '$12.99'
        >>> format_usd(-1299)
        '-$12.99'
        >>> format_usd(1299, show_sign=True)
        '+$12.99'
    """
    dollars = abs(cents) / 100
    negative = cents < 0
    positive = cents > 0

    # Format with commas and 2 decimal places
    formatted = f"${dollars:,.2f}"

    if negative:
        return f"-{formatted}"
    elif show_sign and positive:
        return f"+{formatted}"
    else:
        return formatted


def format_usd_compact(cents: int) -> str:
    """
    Format cents as compact USD (no decimal for whole dollars).

    Args:
        cents: Integer cents

    Returns:
        Formatted string (e.g., "$12", "$12.99")
    """
    dollars = abs(cents) / 100
    negative = cents < 0

    if cents % 100 == 0:
        formatted = f"${int(dollars):,}"
    else:
        formatted = f"${dollars:,.2f}"

    return f"-{formatted}" if negative else formatted


def add_cents(*amounts: int) -> int:
    """
    Add multiple cent amounts safely.

    Simple wrapper for clarity - integer addition is already safe.

    Args:
        *amounts: Integer cent amounts

    Returns:
        Sum of all amounts
    """
    return sum(amounts)


def subtract_cents(a: int, b: int) -> int:
    """
    Subtract cent amounts safely.

    Args:
        a: First amount in cents
        b: Second amount in cents

    Returns:
        a - b
    """
    return a - b


def multiply_cents(cents: int, factor: Union[int, float, Decimal]) -> int:
    """
    Multiply cents by a factor, rounding with ROUND_HALF_UP.

    Useful for calculating percentages or pro-rating.

    Args:
        cents: Amount in cents
        factor: Multiplier (e.g., 0.5 for half, 12/52 for weekly to monthly)

    Returns:
        Result in cents, rounded

    Examples:
        >>> multiply_cents(1000, 0.5)
        500
        >>> multiply_cents(100, 12/52)  # Weekly to monthly
        23
    """
    result = Decimal(cents) * Decimal(str(factor))
    return int(result.quantize(CENTS_QUANTIZER, rounding=ROUND_HALF_UP))


def divide_cents(cents: int, divisor: Union[int, float, Decimal]) -> int:
    """
    Divide cents by a divisor, rounding with ROUND_HALF_UP.

    Args:
        cents: Amount in cents
        divisor: Divisor (must be non-zero)

    Returns:
        Result in cents, rounded

    Raises:
        MoneyParseError: If divisor is zero
    """
    if divisor == 0:
        raise MoneyParseError("Cannot divide by zero")

    result = Decimal(cents) / Decimal(str(divisor))
    return int(result.quantize(CENTS_QUANTIZER, rounding=ROUND_HALF_UP))


def percent_of(cents: int, percentage: Union[int, float, Decimal]) -> int:
    """
    Calculate a percentage of a cent amount.

    Args:
        cents: Base amount in cents
        percentage: Percentage (e.g., 15 for 15%)

    Returns:
        Percentage amount in cents

    Examples:
        >>> percent_of(10000, 15)  # 15% of $100
        1500
        >>> percent_of(1599, 20)  # 20% of $15.99
        320
    """
    return multiply_cents(cents, Decimal(str(percentage)) / 100)


def compare_within_threshold(
    a_cents: int,
    b_cents: int,
    threshold_cents: int = 0,
    threshold_percent: float = 0.0,
) -> bool:
    """
    Check if two amounts are within a threshold of each other.

    Useful for matching refunds, transfers, etc.

    Args:
        a_cents: First amount
        b_cents: Second amount
        threshold_cents: Maximum absolute difference allowed
        threshold_percent: Maximum percentage difference allowed

    Returns:
        True if amounts are within threshold
    """
    diff = abs(a_cents - b_cents)

    # Check absolute threshold
    if threshold_cents > 0 and diff <= threshold_cents:
        return True

    # Check percentage threshold
    if threshold_percent > 0:
        base = max(abs(a_cents), abs(b_cents), 1)  # Avoid division by zero
        percent_diff = (diff / base) * 100
        if percent_diff <= threshold_percent:
            return True

    # If both thresholds are 0, require exact match
    if threshold_cents == 0 and threshold_percent == 0:
        return a_cents == b_cents

    return False
