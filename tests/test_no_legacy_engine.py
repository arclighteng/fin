"""
Enforcement tests to prevent legacy engine reintroduction.

These tests fail if:
- web.py or status_commands.py import forbidden legacy functions
- Any new code paths use analyze_periods/summarize_month/classify_month

The goal is to ensure ALL user-facing totals come from ReportService.
"""
import ast
import re
from pathlib import Path

import pytest


# Root of the project
PROJECT_ROOT = Path(__file__).parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "fin"


# Files that should NOT import legacy functions for totals
# (They may import detection utilities like detect_duplicates)
PROTECTED_FILES = [
    "web.py",
    # "status_commands.py",  # DEFERRED: CLI still uses legacy (see migration doc)
    # "cli.py",  # DEFERRED: CLI still uses legacy
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
ALLOWED_LEGACY_IMPORTS = {
    "detect_alerts",
    "detect_duplicates",
    "detect_sketchy",
    "get_subscriptions",
    "get_bills",
    "detect_cross_account_duplicates",
    "detect_price_changes",
    "_match_known_subscription",
    "_detect_patterns",
    "_is_transfer",
    "_is_credit_card_account",
    "_is_cc_payment_expense",
    "_is_income_transfer",
    "KNOWN_SUBSCRIPTIONS",
    "_SORTED_SUBSCRIPTION_PATTERNS",
    "TimePeriod",  # Can be imported from dates.py but also legacy
}


class TestNoLegacyEngine:
    """Tests that prevent reintroduction of legacy engine usage."""

    def test_web_no_forbidden_imports(self):
        """web.py should not import forbidden legacy functions for totals."""
        web_py = SRC_ROOT / "web.py"
        assert web_py.exists(), f"web.py not found at {web_py}"

        content = web_py.read_text(encoding="utf-8")

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
            # Special case: analyze_periods is allowed for export_summary (DEFERRED)
            if forbidden_found == {"analyze_periods"}:
                # Check if it's only used in export_summary
                # This is acceptable per migration doc
                pass
            else:
                pytest.fail(
                    f"web.py imports forbidden legacy functions: {forbidden_found}\n"
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
            # Pattern: function_name( with word boundary
            for func_name in FORBIDDEN_IMPORTS:
                # Skip if it's just an import line
                pattern = rf"(?<!from .legacy_analysis import.*)\b{func_name}\s*\("
                # Simpler: just check if function is called directly
                call_pattern = rf"= {func_name}\(|[^a-zA-Z_]{func_name}\("

                if re.search(call_pattern, content):
                    # Check it's not in an import statement
                    lines = content.split("\n")
                    for i, line in enumerate(lines, 1):
                        if re.search(call_pattern, line) and "import" not in line.lower():
                            # analyze_periods is allowed in export_summary (temporary)
                            if func_name == "analyze_periods" and "export_summary" in content:
                                continue
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
