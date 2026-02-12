# classifier.py
"""
Canonical transaction classifier with strict type precedence.

TRUTH CONTRACT:
1. Every transaction is classified as exactly ONE of:
   INCOME, EXPENSE, TRANSFER, REFUND, CREDIT_OTHER

2. Positive amount ≠ income. Positive is a CREDIT until proven otherwise.

3. Classification precedence (highest wins):
   USER_OVERRIDE > TRANSFER_PAIR > REFUND_MATCH > TRANSFER_PATTERN >
   INCOME_PATTERN > CREDIT_OTHER

This module is the SINGLE SOURCE OF TRUTH for transaction classification.
"""
from dataclasses import dataclass, field
from datetime import date
from enum import Enum, auto
from typing import Optional

from .reporting_models import TransactionType, SpendingBucket, ClassificationReason


# ---------------------------------------------------------------------------
# Classification Evidence Codes
# ---------------------------------------------------------------------------
class EvidenceCode(Enum):
    """Codes indicating why a transaction was classified a certain way."""
    # User-provided (highest precedence)
    USER_OVERRIDE = auto()       # User explicitly set type
    USER_INCOME_RULE = auto()    # User marked merchant as income
    USER_NOT_INCOME_RULE = auto()  # User marked merchant as not-income

    # Transfer evidence
    TRANSFER_PAIR_MATCHED = auto()   # Both legs found and matched
    TRANSFER_PAIR_SUSPECTED = auto()  # Likely match, awaiting confirmation
    TRANSFER_BANK_PATTERN = auto()    # Strong bank name pattern (e.g., "CHASE TO WELLS FARGO")
    TRANSFER_CC_PAYMENT = auto()      # Credit card payment from checking

    # Refund evidence
    REFUND_MATCHED = auto()           # Matched to prior expense
    REFUND_KEYWORD = auto()           # Contains "refund", "credit", etc.

    # Income evidence
    INCOME_PAYROLL = auto()           # Payroll/direct deposit keywords
    INCOME_EMPLOYER = auto()          # Known employer name
    INCOME_RECURRING_DEPOSIT = auto()  # Regular positive deposits

    # Default
    CREDIT_UNCLASSIFIED = auto()      # Default for unclassified positives
    EXPENSE_DEFAULT = auto()          # Default for negatives


# ---------------------------------------------------------------------------
# Classification Result
# ---------------------------------------------------------------------------
@dataclass
class ClassificationResult:
    """Result of classifying a transaction."""
    txn_type: TransactionType
    reason: ClassificationReason
    spending_bucket: Optional[SpendingBucket] = None  # Only for EXPENSE

    @property
    def is_income(self) -> bool:
        return self.txn_type == TransactionType.INCOME

    @property
    def is_expense(self) -> bool:
        return self.txn_type == TransactionType.EXPENSE

    @property
    def is_transfer(self) -> bool:
        return self.txn_type == TransactionType.TRANSFER

    @property
    def is_refund(self) -> bool:
        return self.txn_type == TransactionType.REFUND

    @property
    def is_credit_other(self) -> bool:
        return self.txn_type == TransactionType.CREDIT_OTHER


# ---------------------------------------------------------------------------
# Override Registry
# ---------------------------------------------------------------------------
@dataclass
class UserOverride:
    """A user-provided classification override."""
    fingerprint: Optional[str] = None  # Specific transaction
    merchant_pattern: Optional[str] = None  # Merchant pattern match
    target_type: Optional[TransactionType] = None
    is_income: Optional[bool] = None  # True = force income, False = force not-income


class OverrideRegistry:
    """
    Registry of user-provided classification overrides.

    Overrides are checked in order:
    1. Fingerprint-specific override (exact transaction)
    2. Merchant pattern override (all transactions matching pattern)
    """

    def __init__(self) -> None:
        self._fingerprint_overrides: dict[str, UserOverride] = {}
        self._merchant_overrides: list[UserOverride] = []
        self._income_merchants: set[str] = set()
        self._excluded_merchants: set[str] = set()

    def add_fingerprint_override(
        self,
        fingerprint: str,
        target_type: TransactionType,
    ) -> None:
        """Override classification for a specific transaction."""
        self._fingerprint_overrides[fingerprint] = UserOverride(
            fingerprint=fingerprint,
            target_type=target_type,
        )

    def add_merchant_type_override(
        self,
        merchant_pattern: str,
        target_type: TransactionType,
    ) -> None:
        """Override classification for all transactions matching merchant pattern."""
        self._merchant_overrides.append(UserOverride(
            merchant_pattern=merchant_pattern.lower(),
            target_type=target_type,
        ))

    def add_income_merchant(self, merchant_pattern: str) -> None:
        """Mark a merchant as an income source."""
        self._income_merchants.add(merchant_pattern.lower())

    def add_excluded_merchant(self, merchant_pattern: str) -> None:
        """Mark a merchant as NOT an income source."""
        self._excluded_merchants.add(merchant_pattern.lower())

    def load_from_db(self, conn) -> None:
        """Load all overrides from database."""
        # Load income/not-income rules from merchant_rules
        rows = conn.execute(
            "SELECT merchant_pattern, rule_type FROM merchant_rules "
            "WHERE rule_type IN ('income', 'not_income')"
        ).fetchall()
        for row in rows:
            pattern = (row[0] if isinstance(row, (list, tuple)) else row["merchant_pattern"]).lower()
            rule_type = row[1] if isinstance(row, (list, tuple)) else row["rule_type"]
            if rule_type == "income":
                self._income_merchants.add(pattern)
            elif rule_type == "not_income":
                self._excluded_merchants.add(pattern)

        # Load transaction type overrides (fingerprint + merchant pattern)
        override_rows = conn.execute(
            "SELECT fingerprint, merchant_pattern, target_type FROM txn_type_overrides"
        ).fetchall()
        for row in override_rows:
            fp = row[0] if isinstance(row, (list, tuple)) else row["fingerprint"]
            mp = row[1] if isinstance(row, (list, tuple)) else row["merchant_pattern"]
            tt = row[2] if isinstance(row, (list, tuple)) else row["target_type"]
            target_type = TransactionType[tt]
            if fp:
                self._fingerprint_overrides[fp] = UserOverride(
                    fingerprint=fp,
                    target_type=target_type,
                )
            elif mp:
                self._merchant_overrides.append(UserOverride(
                    merchant_pattern=mp.lower(),
                    target_type=target_type,
                ))

    def get_override(
        self,
        fingerprint: str,
        merchant_norm: str,
    ) -> Optional[UserOverride]:
        """
        Get applicable override for a transaction.

        Returns None if no override applies.
        """
        # 1. Check fingerprint-specific override
        if fingerprint in self._fingerprint_overrides:
            return self._fingerprint_overrides[fingerprint]

        # 2. Check merchant pattern overrides
        for override in self._merchant_overrides:
            if override.merchant_pattern and override.merchant_pattern in merchant_norm:
                return override

        # 3. Check income/excluded merchants (returns income hint, not full override)
        for pattern in self._income_merchants:
            if pattern in merchant_norm:
                return UserOverride(
                    merchant_pattern=pattern,
                    is_income=True,
                )

        for pattern in self._excluded_merchants:
            if pattern in merchant_norm:
                return UserOverride(
                    merchant_pattern=pattern,
                    is_income=False,
                )

        return None


# ---------------------------------------------------------------------------
# Pattern Detection Results
# ---------------------------------------------------------------------------
@dataclass
class MerchantPattern:
    """Pattern detected for a merchant."""
    merchant_norm: str
    occurrence_count: int
    is_subscription: bool = False    # Regular cadence (Netflix, rent)
    is_habitual: bool = False        # Frequent but irregular (groceries)
    is_transfer: bool = False        # Transfer pattern
    avg_amount_cents: int = 0
    cadence_days: Optional[float] = None


# ---------------------------------------------------------------------------
# Income Detection Patterns
# ---------------------------------------------------------------------------
PAYROLL_KEYWORDS = frozenset([
    "payroll", "salary", "wages", "direct dep", "direct deposit",
    "pay from", "paycheck", "adp", "paychex", "gusto", "workday",
    "quickbooks payroll", "zenefits",
])

EMPLOYER_PATTERNS = frozenset([
    "inc", "llc", "corp", "company", "co.", "ltd",
])


def _is_strong_income(merchant_norm: str) -> bool:
    """Check if merchant has strong income indicators."""
    for keyword in PAYROLL_KEYWORDS:
        if keyword in merchant_norm:
            return True
    return False


# ---------------------------------------------------------------------------
# Transfer Detection Patterns
# ---------------------------------------------------------------------------
TRANSFER_KEYWORDS = frozenset([
    "transfer", "xfer", "ach", "wire", "zelle", "venmo", "paypal",
    "cash app", "cashapp", "apple cash", "square cash",
])

BANK_KEYWORDS = frozenset([
    "chase", "wells fargo", "bank of america", "bofa", "citi", "citibank",
    "capital one", "us bank", "pnc", "td bank", "ally", "discover",
    "american express", "amex", "barclays", "synchrony",
])

CC_PAYMENT_KEYWORDS = frozenset([
    "payment thank you", "autopay", "online payment", "payment received",
    "credit card payment", "cc payment",
])


def _is_transfer_pattern(merchant_norm: str) -> bool:
    """Check if merchant matches transfer patterns."""
    for keyword in TRANSFER_KEYWORDS:
        if keyword in merchant_norm:
            return True
    return False


def _is_bank_to_bank(merchant_norm: str) -> bool:
    """Check if this looks like a bank-to-bank transfer."""
    bank_matches = sum(1 for bank in BANK_KEYWORDS if bank in merchant_norm)
    return bank_matches >= 1 and any(kw in merchant_norm for kw in ["transfer", "to", "from"])


def _is_cc_payment(merchant_norm: str, is_credit_card_account: bool) -> bool:
    """Check if this is a credit card payment."""
    if is_credit_card_account:
        return False  # CC account receiving payment = transfer in

    for keyword in CC_PAYMENT_KEYWORDS:
        if keyword in merchant_norm:
            return True
    return False


# ---------------------------------------------------------------------------
# Refund Detection
# ---------------------------------------------------------------------------
REFUND_KEYWORDS = frozenset([
    "refund", "credit", "return", "reversal", "chargeback",
    "adjustment", "rebate", "cashback",
])


def _has_refund_keywords(merchant_norm: str) -> bool:
    """Check if merchant/description suggests a refund."""
    for keyword in REFUND_KEYWORDS:
        if keyword in merchant_norm:
            return True
    return False


# ---------------------------------------------------------------------------
# Main Classification Function
# ---------------------------------------------------------------------------
def classify_transaction(
    amount_cents: int,
    merchant_norm: str,
    is_credit_card_account: bool = False,
    pattern: Optional[MerchantPattern] = None,
    override_registry: Optional[OverrideRegistry] = None,
    fingerprint: Optional[str] = None,
    is_transfer_paired: bool = False,
    matched_refund_of: Optional[str] = None,
) -> ClassificationResult:
    """
    Classify a transaction with strict precedence.

    PRECEDENCE ORDER (highest wins):
    1. User override (fingerprint or merchant pattern)
    2. Transfer pair match (both legs identified)
    3. Refund match (matched to prior expense)
    4. Transfer pattern (bank names, P2P, CC payment)
    5. Income pattern (payroll, employer keywords)
    6. Default: CREDIT_OTHER for positive, EXPENSE for negative

    Args:
        amount_cents: Transaction amount (positive = credit, negative = debit)
        merchant_norm: Normalized merchant name (lowercase, trimmed)
        is_credit_card_account: Whether the account is a credit card
        pattern: Detected merchant pattern (for recurring detection)
        override_registry: User-provided classification overrides
        fingerprint: Transaction fingerprint for override lookup
        is_transfer_paired: Whether this transaction is part of a matched pair
        matched_refund_of: Fingerprint of expense this refunds (if matched)

    Returns:
        ClassificationResult with type, reason, and optional spending bucket
    """
    evidence: list[str] = []

    # =========================================================================
    # STEP 1: CHECK USER OVERRIDE (highest precedence)
    # =========================================================================
    if override_registry and fingerprint:
        override = override_registry.get_override(fingerprint, merchant_norm)
        if override:
            if override.target_type is not None:
                # Explicit type override
                return ClassificationResult(
                    txn_type=override.target_type,
                    reason=ClassificationReason(
                        primary_code=EvidenceCode.USER_OVERRIDE.name,
                        confidence=1.0,
                        evidence=[f"User override: {override.target_type.name}"],
                    ),
                )
            elif override.is_income is not None:
                # Income/not-income hint (used in later steps)
                if override.is_income:
                    evidence.append(f"User marked as income: {override.merchant_pattern}")
                else:
                    evidence.append(f"User marked as not-income: {override.merchant_pattern}")

    # =========================================================================
    # STEP 2: CHECK TRANSFER PAIR (second highest)
    # =========================================================================
    if is_transfer_paired:
        return ClassificationResult(
            txn_type=TransactionType.TRANSFER,
            reason=ClassificationReason(
                primary_code=EvidenceCode.TRANSFER_PAIR_MATCHED.name,
                confidence=1.0,
                evidence=["Matched transfer pair across accounts"],
            ),
        )

    # =========================================================================
    # STEP 3: CHECK REFUND MATCH
    # =========================================================================
    if matched_refund_of and amount_cents > 0:
        return ClassificationResult(
            txn_type=TransactionType.REFUND,
            reason=ClassificationReason(
                primary_code=EvidenceCode.REFUND_MATCHED.name,
                confidence=1.0,
                evidence=[f"Matched refund of expense {matched_refund_of[:8]}..."],
                matched_txn_id=matched_refund_of,
            ),
        )

    # =========================================================================
    # POSITIVE AMOUNTS
    # =========================================================================
    if amount_cents > 0:
        # Check user override hints
        user_marked_income = any("User marked as income" in e for e in evidence)
        user_marked_not_income = any("User marked as not-income" in e for e in evidence)

        # 4a. User explicitly excluded from income → CREDIT_OTHER
        if user_marked_not_income:
            return ClassificationResult(
                txn_type=TransactionType.CREDIT_OTHER,
                reason=ClassificationReason(
                    primary_code=EvidenceCode.USER_NOT_INCOME_RULE.name,
                    confidence=1.0,
                    evidence=evidence,
                ),
            )

        # 4b. User explicitly marked as income → INCOME
        if user_marked_income:
            return ClassificationResult(
                txn_type=TransactionType.INCOME,
                reason=ClassificationReason(
                    primary_code=EvidenceCode.USER_INCOME_RULE.name,
                    confidence=1.0,
                    evidence=evidence,
                ),
            )

        # 5. Transfer patterns (bank transfers, P2P, CC payment on CC account)
        if pattern and pattern.is_transfer:
            return ClassificationResult(
                txn_type=TransactionType.TRANSFER,
                reason=ClassificationReason(
                    primary_code=EvidenceCode.TRANSFER_BANK_PATTERN.name,
                    confidence=0.9,
                    evidence=["Pattern marked as transfer"],
                ),
            )

        if _is_transfer_pattern(merchant_norm):
            return ClassificationResult(
                txn_type=TransactionType.TRANSFER,
                reason=ClassificationReason(
                    primary_code=EvidenceCode.TRANSFER_BANK_PATTERN.name,
                    confidence=0.8,
                    evidence=[f"Transfer keyword in: {merchant_norm}"],
                ),
            )

        if _is_bank_to_bank(merchant_norm):
            return ClassificationResult(
                txn_type=TransactionType.TRANSFER,
                reason=ClassificationReason(
                    primary_code=EvidenceCode.TRANSFER_BANK_PATTERN.name,
                    confidence=0.85,
                    evidence=["Bank-to-bank pattern detected"],
                ),
            )

        # CC account receiving positive = payment received (transfer)
        if is_credit_card_account:
            return ClassificationResult(
                txn_type=TransactionType.TRANSFER,
                reason=ClassificationReason(
                    primary_code=EvidenceCode.TRANSFER_CC_PAYMENT.name,
                    confidence=0.95,
                    evidence=["Positive on credit card = payment received"],
                ),
            )

        # 6. Refund keywords (but not matched)
        if _has_refund_keywords(merchant_norm):
            return ClassificationResult(
                txn_type=TransactionType.REFUND,
                reason=ClassificationReason(
                    primary_code=EvidenceCode.REFUND_KEYWORD.name,
                    confidence=0.7,
                    evidence=[f"Refund keyword in: {merchant_norm}"],
                ),
            )

        # 7. Strong income patterns (payroll, direct deposit)
        if _is_strong_income(merchant_norm):
            return ClassificationResult(
                txn_type=TransactionType.INCOME,
                reason=ClassificationReason(
                    primary_code=EvidenceCode.INCOME_PAYROLL.name,
                    confidence=0.9,
                    evidence=[f"Payroll keyword in: {merchant_norm}"],
                ),
            )

        # 8. DEFAULT: Positive amount is CREDIT_OTHER
        #    We do NOT assume positive = income
        return ClassificationResult(
            txn_type=TransactionType.CREDIT_OTHER,
            reason=ClassificationReason(
                primary_code=EvidenceCode.CREDIT_UNCLASSIFIED.name,
                confidence=0.5,
                evidence=["Unclassified positive - needs user review"],
            ),
        )

    # =========================================================================
    # NEGATIVE AMOUNTS (EXPENSES)
    # =========================================================================
    else:
        # 1. CC payment from checking = transfer
        if _is_cc_payment(merchant_norm, is_credit_card_account):
            return ClassificationResult(
                txn_type=TransactionType.TRANSFER,
                reason=ClassificationReason(
                    primary_code=EvidenceCode.TRANSFER_CC_PAYMENT.name,
                    confidence=0.95,
                    evidence=["Credit card payment from checking"],
                ),
            )

        # 2. Transfer patterns
        if pattern and pattern.is_transfer:
            return ClassificationResult(
                txn_type=TransactionType.TRANSFER,
                reason=ClassificationReason(
                    primary_code=EvidenceCode.TRANSFER_BANK_PATTERN.name,
                    confidence=0.9,
                    evidence=["Pattern marked as transfer"],
                ),
            )

        if _is_transfer_pattern(merchant_norm):
            return ClassificationResult(
                txn_type=TransactionType.TRANSFER,
                reason=ClassificationReason(
                    primary_code=EvidenceCode.TRANSFER_BANK_PATTERN.name,
                    confidence=0.8,
                    evidence=[f"Transfer keyword in: {merchant_norm}"],
                ),
            )

        # 3. Determine spending bucket
        spending_bucket = SpendingBucket.DISCRETIONARY  # Default

        if pattern:
            if pattern.is_subscription:
                # Predictable cadence = fixed obligation
                spending_bucket = SpendingBucket.FIXED_OBLIGATIONS
            elif pattern.is_habitual:
                # Frequent but irregular = variable essential
                spending_bucket = SpendingBucket.VARIABLE_ESSENTIALS

        # 4. Default: EXPENSE
        return ClassificationResult(
            txn_type=TransactionType.EXPENSE,
            reason=ClassificationReason(
                primary_code=EvidenceCode.EXPENSE_DEFAULT.name,
                confidence=0.8,
                evidence=["Default expense classification"],
            ),
            spending_bucket=spending_bucket,
        )
