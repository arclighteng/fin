"""
csv_import.py — shared CSV import logic for both CLI and web API.

Extracted from cli.py's import_csv command so the web API can reuse
the same parsing and insertion logic without duplicating code.
"""
import csv
import hashlib
import io
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import sqlite3

# ---------------------------------------------------------------------------
# Bank format definitions — maps a bank slug to expected column names.
# Detection: check which bank's required columns are a subset of CSV headers.
# ---------------------------------------------------------------------------
BANK_FORMATS: dict[str, dict[str, str]] = {
    "chase": {
        "date": "Transaction Date",
        "amount": "Amount",
        "description": "Description",
        "merchant": "",
    },
    "bofa": {
        "date": "Date",
        "amount": "Amount",
        "description": "Description",
        "merchant": "",
    },
    "amex": {
        "date": "Date",
        "amount": "Amount",
        "description": "Description",
        "merchant": "",
    },
    "wellsfargo": {
        "date": "Date",
        "amount": "Amount",
        "description": "Description",
        "merchant": "",
    },
    "capitalone": {
        "date": "Transaction Date",
        "amount": "Debit",
        "description": "Description",
        "merchant": "",
    },
}

# Human-readable bank names for UI display
BANK_DISPLAY_NAMES: dict[str, str] = {
    "chase": "Chase",
    "bofa": "Bank of America",
    "amex": "American Express",
    "wellsfargo": "Wells Fargo",
    "capitalone": "Capital One",
}


def detect_bank_format(headers: list[str]) -> Optional[str]:
    """
    Detect which bank format matches the CSV headers.

    Checks required columns (date, amount, description) — merchant col
    is optional and not used for detection. Returns bank slug or None.
    """
    headers_lower = {h.lower() for h in headers}
    for bank_slug, fmt in BANK_FORMATS.items():
        required = {v.lower() for k, v in fmt.items() if v and k != "merchant"}
        if required and required.issubset(headers_lower):
            return bank_slug
    return None


def _map_header(target: str, headers: list[str]) -> Optional[str]:
    """Case-insensitive lookup: find the actual header matching target."""
    target_lower = target.lower()
    for h in headers:
        if h.lower() == target_lower:
            return h
    return None


def import_csv_file(
    csv_content: str,
    conn: sqlite3.Connection,
    account_id: str = "csv-import",
    date_col: str = "date",
    amount_col: str = "amount",
    description_col: str = "description",
    merchant_col: Optional[str] = None,
    date_format: str = "%Y-%m-%d",
    dry_run: bool = False,
) -> dict:
    """
    Parse a CSV string and insert transactions into the database.

    Parameters
    ----------
    csv_content : str
        Full CSV text content.
    conn : sqlite3.Connection
        Open database connection. Caller is responsible for closing.
    account_id : str
        Account ID to assign imported transactions.
    date_col : str
        Column name containing the transaction date.
    amount_col : str
        Column name containing the amount (negative = expense).
    description_col : str
        Column name for description/memo text.
    merchant_col : str | None
        Optional separate merchant column; falls back to description_col.
    date_format : str
        strptime format string for the date column.
    dry_run : bool
        If True, parse only — do not write to DB.

    Returns
    -------
    dict with keys:
        imported : int — rows inserted
        skipped  : int — rows skipped (duplicates)
        errors   : list[str] — parse error messages
        transactions : list[dict] — all parsed rows (for preview/dry_run)
    """
    # Parse CSV
    try:
        sample = csv_content[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(io.StringIO(csv_content), dialect=dialect)
        rows = list(reader)
    except Exception as exc:
        return {"imported": 0, "skipped": 0, "errors": [f"CSV parse error: {exc}"], "transactions": []}

    if not rows:
        return {"imported": 0, "skipped": 0, "errors": ["CSV file is empty or has no data rows."], "transactions": []}

    available_cols = list(rows[0].keys())

    # Case-insensitive column resolution
    actual_date_col = _map_header(date_col, available_cols) or date_col
    actual_amount_col = _map_header(amount_col, available_cols) or amount_col
    actual_desc_col = _map_header(description_col, available_cols) or description_col
    actual_merchant_col = _map_header(merchant_col, available_cols) if merchant_col else None

    transactions: list[dict] = []
    errors: list[str] = []

    for i, row in enumerate(rows, start=2):  # Row 1 is header
        try:
            # Parse date
            date_str = row.get(actual_date_col, "").strip()
            posted_date = None
            # Try the specified format first, then common fallbacks
            for fmt in [date_format, "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%Y/%m/%d"]:
                try:
                    posted_date = datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    continue
            if posted_date is None:
                raise ValueError(f"Cannot parse date '{date_str}'")

            # Parse amount — strip $, commas, handle accounting parens
            amount_str = row.get(actual_amount_col, "0").strip()
            amount_str = amount_str.replace("$", "").replace(",", "").replace(" ", "")
            if amount_str.startswith("(") and amount_str.endswith(")"):
                amount_str = "-" + amount_str[1:-1]
            if not amount_str:
                amount_str = "0"
            amount = Decimal(amount_str)
            amount_cents = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

            # Get description/merchant
            description = row.get(actual_desc_col, "").strip() if actual_desc_col in row else ""
            merchant = row.get(actual_merchant_col, "").strip() if actual_merchant_col and actual_merchant_col in row else ""

            if not merchant and not description:
                raise ValueError("No description or merchant value found")

            # Fingerprint for deduplication (same logic as CLI)
            fp_data = f"{posted_date.isoformat()}|{amount_cents}|{merchant or description}|{account_id}"
            fingerprint = "csv_" + hashlib.sha256(fp_data.encode()).hexdigest()[:32]

            transactions.append({
                "account_id": account_id,
                "posted_at": posted_date.isoformat(),
                "amount_cents": amount_cents,
                "currency": "USD",
                "description": description,
                "merchant": merchant if merchant else description,
                "fingerprint": fingerprint,
            })

        except Exception as exc:
            errors.append(f"Row {i}: {exc}")

    if dry_run or not transactions:
        return {
            "imported": 0,
            "skipped": 0,
            "errors": errors,
            "transactions": transactions,
        }

    # Ensure the account record exists
    conn.execute(
        """
        INSERT OR IGNORE INTO accounts (account_id, institution, name, type, currency, last_seen_at)
        VALUES (?, 'Manual Import', ?, 'checking', 'USD', datetime('now'))
        """,
        (account_id, account_id),
    )

    inserted = 0
    skipped = 0

    for txn in transactions:
        existing = conn.execute(
            "SELECT 1 FROM transactions WHERE fingerprint = ?",
            (txn["fingerprint"],),
        ).fetchone()

        if existing:
            skipped += 1
            continue

        conn.execute(
            """
            INSERT INTO transactions (
                account_id, posted_at, amount_cents, currency,
                description, merchant, fingerprint, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                txn["account_id"],
                txn["posted_at"],
                txn["amount_cents"],
                txn["currency"],
                txn["description"],
                txn["merchant"],
                txn["fingerprint"],
            ),
        )
        inserted += 1

    conn.commit()

    return {
        "imported": inserted,
        "skipped": skipped,
        "errors": errors,
        "transactions": transactions,
    }


def preview_csv(
    csv_content: str,
    max_preview_rows: int = 5,
) -> dict:
    """
    Parse a CSV and return a preview without writing to the database.

    Returns a dict suitable for the /api/import/csv/preview JSON response.
    """
    try:
        sample = csv_content[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(io.StringIO(csv_content), dialect=dialect)
        rows = list(reader)
    except Exception as exc:
        return {"error": f"CSV parse error: {exc}"}

    if not rows:
        return {"error": "CSV file is empty or has no data rows."}

    headers = list(rows[0].keys())

    # Detect bank
    detected_bank = detect_bank_format(headers)

    if detected_bank:
        fmt = BANK_FORMATS[detected_bank]
        # Resolve actual header names via case-insensitive match
        column_mapping = {}
        for role, col_name in fmt.items():
            if col_name:
                actual = _map_header(col_name, headers)
                if actual:
                    column_mapping[role] = actual
    else:
        # Best-effort generic mapping
        column_mapping = {}
        for role in ("date", "amount", "description", "merchant"):
            match = _map_header(role, headers)
            if match:
                column_mapping[role] = match

    # Parse preview rows using detected mapping
    date_col = column_mapping.get("date", "")
    amount_col = column_mapping.get("amount", "")
    desc_col = column_mapping.get("description", "")

    preview_rows = []
    all_dates = []

    for row in rows:
        # Date
        date_str = row.get(date_col, "").strip() if date_col else ""
        parsed_date = None
        for fmt_str in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%Y/%m/%d"]:
            try:
                parsed_date = datetime.strptime(date_str, fmt_str).date()
                break
            except ValueError:
                continue

        if parsed_date:
            all_dates.append(parsed_date.isoformat())

        # Amount
        amount_str = row.get(amount_col, "").strip() if amount_col else ""
        try:
            amount_str_clean = amount_str.replace("$", "").replace(",", "").replace(" ", "")
            if amount_str_clean.startswith("(") and amount_str_clean.endswith(")"):
                amount_str_clean = "-" + amount_str_clean[1:-1]
            amount_cents = int((Decimal(amount_str_clean) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) if amount_str_clean else 0
        except Exception:
            amount_cents = 0

        description = row.get(desc_col, "").strip() if desc_col else ""

        if len(preview_rows) < max_preview_rows:
            preview_rows.append({
                "date": parsed_date.isoformat() if parsed_date else date_str,
                "amount": amount_cents / 100 if amount_cents else 0,
                "description": description,
            })

    date_range = {}
    if all_dates:
        date_range = {"from": min(all_dates), "to": max(all_dates)}

    return {
        "detected_bank": detected_bank,
        "bank_display_name": BANK_DISPLAY_NAMES.get(detected_bank, None) if detected_bank else None,
        "column_mapping": column_mapping,
        "headers": headers,
        "row_count": len(rows),
        "preview": preview_rows,
        "date_range": date_range,
    }
