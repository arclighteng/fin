from pydantic import BaseModel
from datetime import date, datetime
from typing import Optional

class Account(BaseModel):
    account_id: str
    institution: str
    name: str
    type: Optional[str] = None
    currency: str = "USD"

class Transaction(BaseModel):
    account_id: str
    posted_at: date
    amount_cents: int
    currency: str = "USD"
    description: Optional[str] = None
    merchant: Optional[str] = None
    source_txn_id: Optional[str] = None
    fingerprint: str
    pending: bool = False
