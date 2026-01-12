import hashlib
from datetime import date
from typing import Any, Optional
from .models import Transaction

def _norm_text(s: Optional[str]) -> str:
    if not s:
        return ""
    return " ".join(s.strip().upper().split())

def fingerprint_txn(account_id: str, posted_at: date, amount_cents: int, merchant: str, desc: str) -> str:
    # fingerprint contains NO raw sensitive data; it’s a hash of normalized fields
    payload = f"{account_id}|{posted_at.isoformat()}|{amount_cents}|{merchant}|{desc}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def parse_amount_to_cents(amount: Any) -> int:
    # Generic: accept int cents, float dollars, or string dollars. You can harden later.
    if isinstance(amount, int):
        return amount
    if isinstance(amount, float):
        return int(round(amount * 100))
    if isinstance(amount, str):
        amt = float(amount)
        return int(round(amt * 100))
    raise ValueError("Unsupported amount type")

def normalize_simplefin_txn(raw: dict, account_id: str) -> Transaction:
    # SimpleFIN commonly uses "transacted_at" (epoch seconds). Fall back to ISO keys if present.
    if "transacted_at" in raw:
        posted_at = date.fromtimestamp(int(raw["transacted_at"]))
    elif "posted_at" in raw:
        posted_at = date.fromisoformat(raw["posted_at"])
    elif "date" in raw:
        posted_at = date.fromisoformat(raw["date"])
    else:
        raise KeyError("No recognizable date field in transaction")

    # Amount is typically in cents already under "amount" for SimpleFIN.
    amount_cents = parse_amount_to_cents(raw.get("amount"))

    currency = raw.get("currency", "USD")

    # Description/merchant fields vary; keep both if available.
    desc = raw.get("description") or raw.get("memo") or raw.get("name")
    merch = raw.get("payee") or raw.get("merchant") or raw.get("counterparty")

    source_txn_id = raw.get("id") or raw.get("transaction_id")

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
    )
