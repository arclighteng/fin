# reporting_models.py
"""
Core reporting models implementing the Global Truth Contract.

TRUTH CONTRACT (NON-NEGOTIABLE):
1. Transaction types are mutually exclusive: INCOME, EXPENSE, TRANSFER, REFUND, CREDIT_OTHER
2. Positive amount ≠ income. Positive is a CREDIT until proven:
   - INCOME only via user rule or strong payroll evidence
   - REFUND only via match to prior expense
   - TRANSFER only via transfer pairing (preferred) or very strong transfer evidence
   - otherwise CREDIT_OTHER
3. Transfers do not affect net spend/income; matched transfers net to 0.
4. Pending is excluded from posted totals by default; can be shown separately.
5. All internal date ranges are end-exclusive: start <= posted_at < end_exclusive.
6. All money parsing uses Decimal with explicit ROUND_HALF_UP.
7. Web/CLI/Exports must use ONE canonical report engine; never recompute totals separately.
8. Advice/recommendations are gated by integrity score; if integrity is low, show resolution tasks.
"""
from dataclasses import dataclass, field
from datetime import date
from enum import Enum, auto
from typing import Optional


class TransactionType(Enum):
    """
    Mutually exclusive transaction classification.

    Every transaction is exactly ONE of these types.
    """
    INCOME = auto()         # Proven income: payroll, user-marked income
    EXPENSE = auto()        # Outflow spending (negative amount)
    TRANSFER = auto()       # Matched internal transfer (both legs identified)
    REFUND = auto()         # Matched refund to prior expense
    CREDIT_OTHER = auto()   # Unclassified positive - NOT assumed income


class TransferStatus(Enum):
    """
    Status of a transfer transaction.
    """
    MATCHED = auto()        # Both legs identified and paired
    UNMATCHED = auto()      # Only one leg found, needs resolution
    PENDING_MATCH = auto()  # Potential match, awaiting confirmation


class PendingStatus(Enum):
    """
    Posted vs pending transaction status.
    """
    POSTED = auto()         # Confirmed/settled transaction
    PENDING = auto()        # Authorized but not yet posted


class IntegrityFlag(Enum):
    """
    Flags indicating data quality issues that need resolution.
    """
    UNMATCHED_TRANSFER = auto()      # Transfer with only one leg
    UNCLASSIFIED_CREDIT = auto()     # CREDIT_OTHER needing resolution
    DUPLICATE_SUSPECTED = auto()      # Potential duplicate transaction
    RECONCILIATION_FAILED = auto()    # Statement doesn't match
    FUTURE_DATA_LEAK = auto()         # Pattern detection used future data
    PENDING_IN_TOTALS = auto()        # Pending mixed with posted


class SpendingBucket(Enum):
    """
    Spending categorization for financial planning.

    These are NOT the same as "recurring" in the old sense.
    """
    FIXED_OBLIGATIONS = auto()    # Subscription/utility cadence ONLY (predictable)
    VARIABLE_ESSENTIALS = auto()  # Habitual (groceries/gas) - irregular but necessary
    DISCRETIONARY = auto()        # One-off or optional spending
    ONE_OFFS = auto()             # Truly one-time (annual fee, large purchase)


@dataclass
class ClassificationReason:
    """
    Audit trail for why a transaction was classified.
    """
    primary_code: str           # e.g., "USER_OVERRIDE", "PAYROLL_PATTERN", "TRANSFER_PAIR"
    confidence: float           # 0.0 to 1.0
    evidence: list[str] = field(default_factory=list)  # Supporting details
    matched_txn_id: Optional[str] = None  # For refunds/transfers


@dataclass
class ClassifiedTransaction:
    """
    A transaction with full classification metadata.
    """
    fingerprint: str
    account_id: str
    posted_at: date
    amount_cents: int
    merchant_norm: str
    raw_description: str

    # Classification
    txn_type: TransactionType
    spending_bucket: Optional[SpendingBucket]  # Only for EXPENSE
    category_id: Optional[str]                  # Expense category if applicable

    # Metadata
    pending_status: PendingStatus
    reason: ClassificationReason

    # Transfer/refund linking
    transfer_group_id: Optional[str] = None
    transfer_status: Optional[TransferStatus] = None
    matched_refund_of: Optional[str] = None  # Fingerprint of expense being refunded


@dataclass
class PeriodTotals:
    """
    Totals for a reporting period.

    All amounts are in integer cents.
    """
    # Income
    income_cents: int = 0

    # Expenses by bucket
    fixed_obligations_cents: int = 0
    variable_essentials_cents: int = 0
    discretionary_cents: int = 0
    one_offs_cents: int = 0

    # Credits (NOT income)
    refunds_cents: int = 0      # Matched to prior expense
    credits_other_cents: int = 0  # Unclassified positive

    # Transfers (should net to 0 for matched)
    transfers_in_cents: int = 0
    transfers_out_cents: int = 0

    # Derived
    @property
    def total_expenses_cents(self) -> int:
        """Total outflow (not including transfers)."""
        return (
            self.fixed_obligations_cents +
            self.variable_essentials_cents +
            self.discretionary_cents +
            self.one_offs_cents
        )

    @property
    def net_spend_cents(self) -> int:
        """Total expenses minus refunds."""
        return self.total_expenses_cents - self.refunds_cents

    @property
    def net_cents(self) -> int:
        """Income + refunds - expenses."""
        return self.income_cents + self.refunds_cents - self.total_expenses_cents

    @property
    def transfer_balance_cents(self) -> int:
        """Should be 0 for balanced transfers."""
        return self.transfers_in_cents - self.transfers_out_cents


@dataclass
class IntegrityReport:
    """
    Data quality assessment for a report.
    """
    flags: list[IntegrityFlag] = field(default_factory=list)
    unmatched_transfer_count: int = 0
    unclassified_credit_count: int = 0
    unclassified_credit_cents: int = 0
    duplicate_suspect_count: int = 0
    reconciliation_delta_cents: int = 0

    @property
    def score(self) -> float:
        """
        Integrity score from 0.0 (broken) to 1.0 (perfect).

        Below 0.8 should gate recommendations.
        """
        if not self.flags:
            return 1.0

        # Each flag reduces score
        penalties = {
            IntegrityFlag.UNMATCHED_TRANSFER: 0.05,
            IntegrityFlag.UNCLASSIFIED_CREDIT: 0.10,
            IntegrityFlag.DUPLICATE_SUSPECTED: 0.05,
            IntegrityFlag.RECONCILIATION_FAILED: 0.20,
            IntegrityFlag.FUTURE_DATA_LEAK: 0.15,
            IntegrityFlag.PENDING_IN_TOTALS: 0.10,
        }
        total_penalty = sum(penalties.get(f, 0.05) for f in self.flags)
        return max(0.0, 1.0 - total_penalty)

    @property
    def is_actionable(self) -> bool:
        """Whether recommendations should be shown."""
        return self.score >= 0.8


@dataclass
class Report:
    """
    Canonical report for a period.

    This is the ONLY structure used by web, CLI, and exports.
    """
    # Period metadata
    period_label: str
    start_date: date
    end_date: date  # Exclusive

    # Totals
    totals: PeriodTotals

    # Transaction details
    transactions: list[ClassifiedTransaction] = field(default_factory=list)

    # Data quality
    integrity: IntegrityReport = field(default_factory=IntegrityReport)

    # Versioning (for reproducibility)
    classifier_version: str = "1.0.0"
    report_version: str = "1.0.0"
    report_hash: Optional[str] = None  # SHA256 of canonical JSON

    # Counts
    transaction_count: int = 0
    pending_count: int = 0  # Excluded from totals

    @property
    def has_unresolved_issues(self) -> bool:
        """Whether there are issues needing user attention."""
        return bool(self.integrity.flags)


@dataclass
class ResolutionTask:
    """
    A task for the user to resolve data issues.
    """
    task_type: str          # "CLASSIFY_CREDIT", "MATCH_TRANSFER", "RECONCILE"
    description: str
    priority: int           # 1 = highest
    affected_cents: int     # Financial impact
    affected_txn_ids: list[str] = field(default_factory=list)


@dataclass
class Recommendation:
    """
    A gated financial recommendation.

    Only shown when integrity score >= 0.8.
    """
    title: str
    evidence_summary: str
    impact_cents_per_month: int
    confidence: float
    next_steps: list[str] = field(default_factory=list)
    supporting_txn_ids: list[str] = field(default_factory=list)
