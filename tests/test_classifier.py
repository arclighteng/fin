"""
Tests for classifier.py - canonical transaction classifier.

TRUTH CONTRACT verification:
- Every transaction is exactly ONE type
- Positive amount ≠ income (default to CREDIT_OTHER)
- Strict precedence: USER_OVERRIDE > TRANSFER > REFUND > INCOME > CREDIT_OTHER
"""
import pytest

from fin.classifier import (
    classify_transaction,
    ClassificationResult,
    OverrideRegistry,
    MerchantPattern,
    EvidenceCode,
)
from fin.reporting_models import TransactionType, SpendingBucket


class TestClassificationPrecedence:
    """Test that precedence order is respected."""

    def test_user_override_beats_everything(self):
        """User override should win over all other signals."""
        registry = OverrideRegistry()
        registry.add_fingerprint_override("fp123", TransactionType.INCOME)

        # Even with transfer keywords, user override wins
        result = classify_transaction(
            amount_cents=1000,
            merchant_norm="transfer from savings",
            fingerprint="fp123",
            override_registry=registry,
        )

        assert result.txn_type == TransactionType.INCOME
        assert EvidenceCode.USER_OVERRIDE.name in result.reason.primary_code

    def test_transfer_pair_beats_income_pattern(self):
        """Matched transfer pair should beat income patterns."""
        result = classify_transaction(
            amount_cents=5000_00,  # $5000 positive
            merchant_norm="direct deposit payroll",
            is_transfer_paired=True,
        )

        assert result.txn_type == TransactionType.TRANSFER
        assert "TRANSFER_PAIR" in result.reason.primary_code

    def test_refund_match_beats_credit_other(self):
        """Matched refund should not be classified as CREDIT_OTHER."""
        result = classify_transaction(
            amount_cents=1599,  # $15.99 refund
            merchant_norm="amazon",
            matched_refund_of="expense_fp_12345",
        )

        assert result.txn_type == TransactionType.REFUND
        assert result.reason.matched_txn_id == "expense_fp_12345"


class TestPositiveAmountClassification:
    """Test positive amount (credit) classification."""

    def test_positive_not_assumed_income(self):
        """Positive amount should NOT default to income."""
        result = classify_transaction(
            amount_cents=1000,
            merchant_norm="random merchant",
        )

        assert result.txn_type == TransactionType.CREDIT_OTHER
        assert "CREDIT_UNCLASSIFIED" in result.reason.primary_code

    def test_payroll_is_income(self):
        """Payroll keywords should classify as income."""
        result = classify_transaction(
            amount_cents=3000_00,
            merchant_norm="acme corp payroll direct deposit",
        )

        assert result.txn_type == TransactionType.INCOME
        assert "PAYROLL" in result.reason.primary_code

    def test_direct_deposit_is_income(self):
        """Direct deposit should classify as income."""
        result = classify_transaction(
            amount_cents=2500_00,
            merchant_norm="employer inc direct dep",
        )

        assert result.txn_type == TransactionType.INCOME

    def test_cc_positive_is_transfer(self):
        """Positive on credit card account is a payment (transfer)."""
        result = classify_transaction(
            amount_cents=500_00,
            merchant_norm="online payment thank you",
            is_credit_card_account=True,
        )

        assert result.txn_type == TransactionType.TRANSFER
        assert "CC_PAYMENT" in result.reason.primary_code

    def test_transfer_keyword_is_transfer(self):
        """Transfer keywords should classify as transfer."""
        for keyword in ["zelle", "venmo", "paypal", "transfer"]:
            result = classify_transaction(
                amount_cents=100_00,
                merchant_norm=f"{keyword} from friend",
            )
            assert result.txn_type == TransactionType.TRANSFER, f"Failed for {keyword}"

    def test_refund_keyword_is_refund(self):
        """Refund keywords should classify as refund."""
        result = classify_transaction(
            amount_cents=25_00,
            merchant_norm="amazon refund",
        )

        assert result.txn_type == TransactionType.REFUND
        assert "REFUND_KEYWORD" in result.reason.primary_code

    def test_user_income_merchant_is_income(self):
        """User-marked income merchant should be income."""
        registry = OverrideRegistry()
        registry.add_income_merchant("employer inc")

        result = classify_transaction(
            amount_cents=3000_00,
            merchant_norm="employer inc",
            fingerprint="fp456",
            override_registry=registry,
        )

        assert result.txn_type == TransactionType.INCOME

    def test_user_excluded_merchant_is_credit_other(self):
        """User-excluded merchant should be CREDIT_OTHER."""
        registry = OverrideRegistry()
        registry.add_excluded_merchant("cashback")

        result = classify_transaction(
            amount_cents=50_00,
            merchant_norm="cashback reward",
            fingerprint="fp789",
            override_registry=registry,
        )

        assert result.txn_type == TransactionType.CREDIT_OTHER


class TestNegativeAmountClassification:
    """Test negative amount (expense) classification."""

    def test_expense_default(self):
        """Negative amount should default to EXPENSE."""
        result = classify_transaction(
            amount_cents=-50_00,
            merchant_norm="random store",
        )

        assert result.txn_type == TransactionType.EXPENSE
        assert result.spending_bucket == SpendingBucket.DISCRETIONARY

    def test_cc_payment_is_transfer(self):
        """Credit card payment from checking is transfer."""
        result = classify_transaction(
            amount_cents=-500_00,
            merchant_norm="chase credit card payment thank you",
            is_credit_card_account=False,
        )

        assert result.txn_type == TransactionType.TRANSFER

    def test_subscription_is_fixed_obligation(self):
        """Subscription pattern should be FIXED_OBLIGATIONS bucket."""
        pattern = MerchantPattern(
            merchant_norm="netflix",
            occurrence_count=6,
            is_subscription=True,
            avg_amount_cents=1599,
            cadence_days=30.5,
        )

        result = classify_transaction(
            amount_cents=-1599,
            merchant_norm="netflix",
            pattern=pattern,
        )

        assert result.txn_type == TransactionType.EXPENSE
        assert result.spending_bucket == SpendingBucket.FIXED_OBLIGATIONS

    def test_habitual_is_variable_essential(self):
        """Habitual spending should be VARIABLE_ESSENTIALS bucket."""
        pattern = MerchantPattern(
            merchant_norm="grocery store",
            occurrence_count=12,
            is_habitual=True,
            avg_amount_cents=8500,
        )

        result = classify_transaction(
            amount_cents=-85_00,
            merchant_norm="grocery store",
            pattern=pattern,
        )

        assert result.txn_type == TransactionType.EXPENSE
        assert result.spending_bucket == SpendingBucket.VARIABLE_ESSENTIALS

    def test_transfer_pattern_is_transfer(self):
        """Transfer pattern should be TRANSFER."""
        pattern = MerchantPattern(
            merchant_norm="savings transfer",
            occurrence_count=3,
            is_transfer=True,
        )

        result = classify_transaction(
            amount_cents=-1000_00,
            merchant_norm="savings transfer",
            pattern=pattern,
        )

        assert result.txn_type == TransactionType.TRANSFER


class TestOverrideRegistry:
    """Test user override functionality."""

    def test_fingerprint_override(self):
        """Fingerprint-specific override should work."""
        registry = OverrideRegistry()
        registry.add_fingerprint_override("fp123", TransactionType.REFUND)

        override = registry.get_override("fp123", "amazon")
        assert override is not None
        assert override.target_type == TransactionType.REFUND

    def test_merchant_pattern_override(self):
        """Merchant pattern override should work."""
        registry = OverrideRegistry()
        registry.add_merchant_type_override("internal transfer", TransactionType.TRANSFER)

        override = registry.get_override("fp999", "internal transfer to savings")
        assert override is not None
        assert override.target_type == TransactionType.TRANSFER

    def test_fingerprint_beats_merchant(self):
        """Fingerprint override should beat merchant pattern."""
        registry = OverrideRegistry()
        registry.add_fingerprint_override("fp123", TransactionType.INCOME)
        registry.add_merchant_type_override("amazon", TransactionType.EXPENSE)

        override = registry.get_override("fp123", "amazon purchase")
        assert override.target_type == TransactionType.INCOME

    def test_income_merchant_hint(self):
        """Income merchant should return is_income hint."""
        registry = OverrideRegistry()
        registry.add_income_merchant("employer")

        override = registry.get_override("fp456", "employer inc payroll")
        assert override is not None
        assert override.is_income is True

    def test_excluded_merchant_hint(self):
        """Excluded merchant should return is_income=False hint."""
        registry = OverrideRegistry()
        registry.add_excluded_merchant("rewards")

        override = registry.get_override("fp789", "credit card rewards")
        assert override is not None
        assert override.is_income is False


class TestClassificationResult:
    """Test ClassificationResult properties."""

    def test_is_income_property(self):
        """is_income should return True only for INCOME type."""
        result = classify_transaction(
            amount_cents=3000_00,
            merchant_norm="payroll direct deposit",
        )
        assert result.is_income is True
        assert result.is_expense is False
        assert result.is_transfer is False

    def test_is_expense_property(self):
        """is_expense should return True only for EXPENSE type."""
        result = classify_transaction(
            amount_cents=-50_00,
            merchant_norm="coffee shop",
        )
        assert result.is_expense is True
        assert result.is_income is False

    def test_is_transfer_property(self):
        """is_transfer should return True only for TRANSFER type."""
        result = classify_transaction(
            amount_cents=-500_00,
            merchant_norm="zelle to friend",
        )
        assert result.is_transfer is True
        assert result.is_expense is False


class TestConfidenceScoring:
    """Test confidence scores in classification."""

    def test_user_override_high_confidence(self):
        """User overrides should have confidence 1.0."""
        registry = OverrideRegistry()
        registry.add_fingerprint_override("fp123", TransactionType.INCOME)

        result = classify_transaction(
            amount_cents=1000,
            merchant_norm="random",
            fingerprint="fp123",
            override_registry=registry,
        )

        assert result.reason.confidence == 1.0

    def test_transfer_pair_high_confidence(self):
        """Transfer pairs should have high confidence."""
        result = classify_transaction(
            amount_cents=1000_00,
            merchant_norm="random",
            is_transfer_paired=True,
        )

        assert result.reason.confidence == 1.0

    def test_unclassified_low_confidence(self):
        """Unclassified credits should have low confidence."""
        result = classify_transaction(
            amount_cents=100_00,
            merchant_norm="unknown merchant",
        )

        assert result.txn_type == TransactionType.CREDIT_OTHER
        assert result.reason.confidence < 1.0


class TestMutualExclusivity:
    """Test that types are mutually exclusive."""

    def test_only_one_type(self):
        """Each classification should have exactly one type."""
        test_cases = [
            (1000, "payroll deposit"),      # Income
            (-5000, "amazon purchase"),      # Expense
            (500, "zelle from friend"),      # Transfer
            (-50000, "venmo payment"),       # Transfer
            (1599, "refund credit"),         # Refund
            (100, "mysterious deposit"),     # Credit other
        ]

        for amount, merchant in test_cases:
            result = classify_transaction(
                amount_cents=amount,
                merchant_norm=merchant,
            )

            # Count how many type properties are True
            type_count = sum([
                result.is_income,
                result.is_expense,
                result.is_transfer,
                result.is_refund,
                result.is_credit_other,
            ])

            assert type_count == 1, f"Expected exactly 1 type for ({amount}, {merchant}), got {type_count}"
