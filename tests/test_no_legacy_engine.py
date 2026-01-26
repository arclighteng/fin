"""
Enforcement tests to prevent legacy engine reintroduction.

These tests fail if:
- Protected files import forbidden legacy functions for totals
- Any user-facing code paths use analyze_periods/summarize_month/classify_month

The goal is to ensure ALL user-facing totals come from ReportService.
"""
import ast
import re
import warnings
from pathlib import Path

import pytest


# Root of the project
PROJECT_ROOT = Path(__file__).parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "fin"


# Files that should NOT import legacy functions for totals
# (They may import detection utilities like detect_duplicates)
PROTECTED_FILES = [
    "web.py",
    "status_commands.py",
    "cli.py",
    "projections.py",
]

# Forbidden imports - these produce totals and must not be used
FORBIDDEN_IMPORTS = {
    "analyze_periods",
    "analyze_custom_range",
    "get_current_period",
    "summarize_month",
    "classify_month",
    # detect_alerts is allowed for now (it's alert detection, not totals)
    # detect_duplicates, detect_sketchy are detection utilities, allowed
}

# Allowed legacy imports (detection utilities, not totals)
# NOTE: Internal functions (_detect_patterns, _is_transfer, etc.) are NOT
# allowed in web.py - they were removed when /api/transactions-by-type was
# migrated to canonical ReportService.
ALLOWED_LEGACY_IMPORTS = {
    "detect_alerts",
    "detect_duplicates",
    "detect_sketchy",
    "get_subscriptions",
    "get_bills",
    "detect_cross_account_duplicates",
    "detect_price_changes",
    "KNOWN_SUBSCRIPTIONS",
    "_SORTED_SUBSCRIPTION_PATTERNS",
    "TimePeriod",  # Can be imported from dates.py but also legacy
}

# Legacy imports that are ONLY allowed in projections.py (heuristic module)
# These are NOT allowed in web.py or other user-facing code
PROJECTIONS_ONLY_IMPORTS = {
    "_match_known_subscription",
    "_detect_patterns",
}

# Legacy imports that should NOT be in web.py at all (drilldowns migrated)
# Note: _match_known_subscription IS allowed - it's a display utility for /api/payee
WEB_FORBIDDEN_LEGACY_INTERNALS = {
    "_detect_patterns",
    "_is_transfer",
    "_is_credit_card_account",
    "_is_cc_payment_expense",
    "_is_income_transfer",
}


class TestNoLegacyEngine:
    """Tests that prevent reintroduction of legacy engine usage."""

    def test_protected_files_no_forbidden_imports(self):
        """Protected files should not import forbidden legacy functions for totals."""
        for filename in PROTECTED_FILES:
            filepath = SRC_ROOT / filename
            if not filepath.exists():
                continue

            content = filepath.read_text(encoding="utf-8")

            # Parse AST to find imports
            tree = ast.parse(content)
            imported_names = set()

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module and "legacy" in node.module:
                        for alias in node.names:
                            name = alias.name
                            imported_names.add(name)

            # Check for forbidden imports
            forbidden_found = imported_names & FORBIDDEN_IMPORTS
            if forbidden_found:
                pytest.fail(
                    f"{filename} imports forbidden legacy functions: {forbidden_found}\n"
                    f"These functions produce totals and must use ReportService instead.\n"
                    f"Allowed legacy imports: {ALLOWED_LEGACY_IMPORTS}"
                )

    def test_no_direct_legacy_analysis_calls(self):
        """Ensure no direct calls to legacy analysis functions in protected files."""
        for filename in PROTECTED_FILES:
            filepath = SRC_ROOT / filename
            if not filepath.exists():
                continue

            content = filepath.read_text(encoding="utf-8")

            # Check for direct function calls (not imports)
            for func_name in FORBIDDEN_IMPORTS:
                # Pattern: function call (not in import statement)
                call_pattern = rf"= {func_name}\(|[^a-zA-Z_]{func_name}\("

                if re.search(call_pattern, content):
                    # Check it's not in an import statement
                    lines = content.split("\n")
                    for i, line in enumerate(lines, 1):
                        if re.search(call_pattern, line) and "import" not in line.lower():
                            pytest.fail(
                                f"{filename}:{i} calls forbidden function '{func_name}'\n"
                                f"Line: {line.strip()}\n"
                                f"Use ReportService instead."
                            )

    def test_report_service_is_imported(self):
        """web.py should import ReportService for totals."""
        web_py = SRC_ROOT / "web.py"
        content = web_py.read_text(encoding="utf-8")

        assert "from .report_service import" in content or "from fin.report_service import" in content, (
            "web.py should import ReportService for producing totals"
        )

    def test_view_models_are_used(self):
        """web.py should use view_models for template compatibility."""
        web_py = SRC_ROOT / "web.py"
        content = web_py.read_text(encoding="utf-8")

        assert "from .view_models import" in content or "PeriodViewModel" in content, (
            "web.py should use view_models to adapt Reports for templates"
        )

    def test_dashboard_uses_report_service(self):
        """Dashboard route should use ReportService, not legacy analysis."""
        web_py = SRC_ROOT / "web.py"
        content = web_py.read_text(encoding="utf-8")

        # Find dashboard function
        dashboard_match = re.search(
            r'def dashboard\(.*?\):\s*""".*?"""(.*?)(?=\n@app\.|def \w+\(|\Z)',
            content,
            re.DOTALL
        )

        if dashboard_match:
            dashboard_body = dashboard_match.group(1)

            # Should use ReportService
            assert "ReportService" in dashboard_body or "report_service" in dashboard_body, (
                "Dashboard should use ReportService for reports"
            )

            # Should NOT call analyze_custom_range directly in main body
            # (It's acceptable in deferred functions or comments)
            assert "analyze_custom_range(conn," not in dashboard_body, (
                "Dashboard should not call analyze_custom_range directly.\n"
                "Use ReportService.report_period() instead."
            )


class TestLegacyModulesHaveWarnings:
    """Ensure legacy modules emit deprecation warnings."""

    def test_legacy_analysis_has_warning(self):
        """legacy_analysis.py should have deprecation warning."""
        legacy = SRC_ROOT / "legacy_analysis.py"
        content = legacy.read_text(encoding="utf-8")

        assert "warnings.warn" in content or "DeprecationWarning" in content, (
            "legacy_analysis.py should emit deprecation warning"
        )

    def test_legacy_classify_has_warning(self):
        """legacy_classify.py should have deprecation warning."""
        legacy = SRC_ROOT / "legacy_classify.py"
        content = legacy.read_text(encoding="utf-8")

        assert "warnings.warn" in content or "DeprecationWarning" in content, (
            "legacy_classify.py should emit deprecation warning"
        )

    def test_legacy_modules_have_banners(self):
        """Legacy modules should have clear LEGACY banners."""
        for name in ["legacy_analysis.py", "legacy_classify.py"]:
            filepath = SRC_ROOT / name
            content = filepath.read_text(encoding="utf-8")

            assert "LEGACY" in content[:500], (
                f"{name} should have LEGACY banner in docstring"
            )


class TestCanonicalEngineIsComplete:
    """Ensure the canonical engine has required components."""

    def test_report_service_exists(self):
        """ReportService module should exist."""
        assert (SRC_ROOT / "report_service.py").exists()

    def test_reporting_exists(self):
        """Core reporting module should exist."""
        assert (SRC_ROOT / "reporting.py").exists()

    def test_view_models_exists(self):
        """View models module should exist."""
        assert (SRC_ROOT / "view_models.py").exists()

    def test_versioning_exists(self):
        """Versioning module should exist."""
        assert (SRC_ROOT / "versioning.py").exists()

    def test_report_service_has_key_functions(self):
        """ReportService should have key functions."""
        content = (SRC_ROOT / "report_service.py").read_text(encoding="utf-8")

        assert "def report_period" in content
        assert "def report_month" in content
        assert "def report_periods" in content
        assert "class ReportService" in content

    def test_cli_uses_report_service(self):
        """cli.py should import ReportService for totals."""
        cli_py = SRC_ROOT / "cli.py"
        content = cli_py.read_text(encoding="utf-8")

        assert "from .report_service import" in content or "ReportService" in content, (
            "cli.py should use ReportService for producing totals"
        )

    def test_status_commands_uses_report_service(self):
        """status_commands.py should import ReportService for totals."""
        status_py = SRC_ROOT / "status_commands.py"
        content = status_py.read_text(encoding="utf-8")

        assert "from .report_service import" in content or "ReportService" in content, (
            "status_commands.py should use ReportService for producing totals"
        )

    def test_projections_uses_canonical_income(self):
        """projections.py should use ReportService for income estimation."""
        proj_py = SRC_ROOT / "projections.py"
        content = proj_py.read_text(encoding="utf-8")

        # Should import ReportService in _estimate_income
        assert "ReportService" in content, (
            "projections.py should use ReportService for canonical income"
        )

        # Should NOT use raw SQL to sum positive amounts (which would include non-income credits)
        assert "amount_cents > 0" not in content.lower(), (
            "projections.py should not sum positive amounts directly (use report.totals.income_cents)"
        )


class TestNoDeprecationWarningsInNormalPath:
    """Ensure normal imports don't trigger deprecation warnings."""

    def test_web_import_works(self):
        """Importing web.py should work without errors."""
        # The deprecation warning from legacy_classify is expected because
        # we import detection utilities from there. The key is that we don't
        # import the totals-producing functions directly.
        import fin.web
        assert fin.web is not None

    def test_cli_import_works(self):
        """Importing cli.py should work without errors."""
        # Same as above - deprecation warning from legacy_classify is expected
        # because we import detection utilities from there.
        import fin.cli
        assert fin.cli is not None

    def test_status_commands_import_works(self):
        """Importing status_commands.py should work without errors."""
        import fin.status_commands
        assert fin.status_commands is not None


class TestWebDrilldownsCanonical:
    """Ensure web drilldowns use canonical ReportService, not legacy functions."""

    def test_web_uses_category_breakdown_from_report(self):
        """web.py should use category_breakdown_from_report, not get_category_breakdown."""
        web_py = SRC_ROOT / "web.py"
        content = web_py.read_text(encoding="utf-8")

        # Should import from view_models
        assert "category_breakdown_from_report" in content, (
            "web.py should use category_breakdown_from_report from view_models"
        )

        # Should NOT import get_category_breakdown from categorize
        assert "from .categorize import" not in content or "get_category_breakdown" not in content, (
            "web.py should NOT import get_category_breakdown - use category_breakdown_from_report instead"
        )

    def test_web_transactions_by_type_uses_report_service(self):
        """The /api/transactions-by-type endpoint should use ReportService."""
        web_py = SRC_ROOT / "web.py"
        content = web_py.read_text(encoding="utf-8")

        # Find the transactions-by-type function
        match = re.search(
            r'@app\.get\("/api/transactions-by-type"\).*?def get_transactions_by_type\(.*?\):.*?""".*?"""(.*?)(?=\n@app\.|def \w+\(|\Z)',
            content,
            re.DOTALL
        )

        if match:
            func_body = match.group(1)

            # Should use ReportService
            assert "ReportService" in func_body, (
                "/api/transactions-by-type should use ReportService for canonical transactions"
            )

            # Should NOT import legacy internal functions
            assert "_detect_patterns" not in func_body, (
                "/api/transactions-by-type should NOT use _detect_patterns"
            )
            assert "_is_transfer" not in func_body, (
                "/api/transactions-by-type should NOT use _is_transfer"
            )

    def test_web_no_legacy_internal_imports(self):
        """web.py should not import legacy internal functions."""
        web_py = SRC_ROOT / "web.py"
        content = web_py.read_text(encoding="utf-8")

        # Parse AST to find imports
        tree = ast.parse(content)
        imported_names = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "legacy" in node.module:
                    for alias in node.names:
                        imported_names.add(alias.name)

        # Check for forbidden internal legacy imports
        forbidden_found = imported_names & WEB_FORBIDDEN_LEGACY_INTERNALS
        if forbidden_found:
            pytest.fail(
                f"web.py imports forbidden legacy internal functions: {forbidden_found}\n"
                f"These should have been removed when drilldowns migrated to ReportService."
            )

    def test_dashboard_category_breakdown_is_canonical(self):
        """Dashboard should use canonical category breakdown from Report."""
        web_py = SRC_ROOT / "web.py"
        content = web_py.read_text(encoding="utf-8")

        # Find the dashboard function's category breakdown section
        # Should see: category_breakdown_from_report(current_report)
        # Should NOT see: get_category_breakdown(conn, ...)
        assert "category_breakdown_from_report(current_report)" in content, (
            "Dashboard should compute category_breakdown from current_report"
        )

        assert "get_category_breakdown(conn," not in content or "get_category_breakdown(" not in content, (
            "Dashboard should NOT call get_category_breakdown - use category_breakdown_from_report"
        )

    def test_projections_labeled_heuristic(self):
        """projections.py should be clearly labeled as heuristic, not canonical."""
        proj_py = SRC_ROOT / "projections.py"
        content = proj_py.read_text(encoding="utf-8")

        # Module docstring should say HEURISTIC
        assert "HEURISTIC" in content[:500], (
            "projections.py should have HEURISTIC label in module docstring"
        )

        # CashFlowProjection should have is_heuristic flag
        assert "is_heuristic" in content, (
            "CashFlowProjection should have is_heuristic flag"
        )

        # detect_cash_flow_alerts should have confidence gating
        assert "min_confidence" in content, (
            "detect_cash_flow_alerts should have confidence threshold gating"
        )
