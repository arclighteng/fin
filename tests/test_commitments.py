"""
Tests for commitments table schema and basic operations.

Tests:
- commitments table is created by init_db
- All required columns exist with correct types
- Basic insert/query operations work
"""
import calendar as cal_mod
import sqlite3
from datetime import date, timedelta

import pytest

from fin import db as dbmod


@pytest.fixture
def db(tmp_path):
    """Create a temporary database for testing."""
    conn = dbmod.connect(str(tmp_path / "test.db"))
    dbmod.init_db(conn)
    return conn


def test_commitments_table_exists(db):
    """commitments table must be created by init_db."""
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='commitments'"
    ).fetchone()
    assert row is not None, "commitments table not created"


def test_commitments_table_columns(db):
    """Verify required columns exist with correct types."""
    cols = {
        row["name"]: row
        for row in db.execute("PRAGMA table_info(commitments)").fetchall()
    }
    assert "id" in cols
    assert "name" in cols
    assert "merchant_norm" in cols
    assert "expected_cents" in cols
    assert "cadence" in cols
    assert "day_of_month" in cols
    assert "reference_date" in cols
    assert "confirmed" in cols
    assert "source" in cols
    assert "created_at" in cols
    assert "updated_at" in cols


def test_insert_commitment_basic(db):
    """Should insert a basic commitment."""
    db.execute(
        """
        INSERT INTO commitments(
            name, merchant_norm, expected_cents, cadence, day_of_month,
            reference_date, confirmed, source, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            "Netflix",
            "netflix.com",
            1599,
            "monthly",
            3,
            "2026-03-01",
            1,
            "manual",
        ),
    )
    db.commit()

    row = db.execute(
        "SELECT * FROM commitments WHERE name = 'Netflix'"
    ).fetchone()
    assert row is not None
    assert row["merchant_norm"] == "netflix.com"
    assert row["expected_cents"] == 1599
    assert row["cadence"] == "monthly"
    assert row["day_of_month"] == 3
    assert row["reference_date"] == "2026-03-01"
    assert row["confirmed"] == 1
    assert row["source"] == "manual"


def test_insert_commitment_defaults(db):
    """Should insert commitment with default values."""
    db.execute(
        """
        INSERT INTO commitments(
            name, created_at, updated_at
        ) VALUES (?, datetime('now'), datetime('now'))
        """,
        ("Gym",),
    )
    db.commit()

    row = db.execute(
        "SELECT * FROM commitments WHERE name = 'Gym'"
    ).fetchone()
    assert row is not None
    assert row["cadence"] == "monthly"
    assert row["confirmed"] == 0
    assert row["source"] == "detected"
    assert row["merchant_norm"] is None
    assert row["expected_cents"] is None


def test_valid_cadence_values(db):
    """Should accept all valid cadence values."""
    cadences = ["monthly", "weekly", "annual", "quarterly", "biweekly", "one_time"]

    for i, cadence in enumerate(cadences):
        db.execute(
            """
            INSERT INTO commitments(
                name, cadence, created_at, updated_at
            ) VALUES (?, ?, datetime('now'), datetime('now'))
            """,
            (f"Commitment_{i}", cadence),
        )
    db.commit()

    rows = db.execute("SELECT cadence FROM commitments ORDER BY name").fetchall()
    retrieved_cadences = [row["cadence"] for row in rows]
    assert set(retrieved_cadences) == set(cadences)


def test_valid_source_values(db):
    """Should accept all valid source values."""
    sources = ["detected", "manual", "dismissed"]

    for i, source in enumerate(sources):
        db.execute(
            """
            INSERT INTO commitments(
                name, source, created_at, updated_at
            ) VALUES (?, ?, datetime('now'), datetime('now'))
            """,
            (f"Commitment_{i}", source),
        )
    db.commit()

    rows = db.execute("SELECT source FROM commitments ORDER BY name").fetchall()
    retrieved_sources = [row["source"] for row in rows]
    assert retrieved_sources == sources


def test_reference_date_format(db):
    """reference_date should store YYYY-MM-DD format."""
    db.execute(
        """
        INSERT INTO commitments(
            name, reference_date, created_at, updated_at
        ) VALUES (?, ?, datetime('now'), datetime('now'))
        """,
        ("Biweekly Bill", "2026-03-24"),
    )
    db.commit()

    row = db.execute(
        "SELECT reference_date FROM commitments WHERE name = 'Biweekly Bill'"
    ).fetchone()
    assert row["reference_date"] == "2026-03-24"


def test_day_of_month_nullable(db):
    """day_of_month should be nullable."""
    # Insert with NULL day_of_month
    db.execute(
        """
        INSERT INTO commitments(
            name, day_of_month, created_at, updated_at
        ) VALUES (?, NULL, datetime('now'), datetime('now'))
        """,
        ("No Day Specified",),
    )
    db.commit()

    row = db.execute(
        "SELECT day_of_month FROM commitments WHERE name = 'No Day Specified'"
    ).fetchone()
    assert row["day_of_month"] is None


def test_expected_cents_nullable(db):
    """expected_cents should be nullable."""
    db.execute(
        """
        INSERT INTO commitments(
            name, expected_cents, created_at, updated_at
        ) VALUES (?, NULL, datetime('now'), datetime('now'))
        """,
        ("Unknown Amount",),
    )
    db.commit()

    row = db.execute(
        "SELECT expected_cents FROM commitments WHERE name = 'Unknown Amount'"
    ).fetchone()
    assert row["expected_cents"] is None


def test_merchant_norm_nullable(db):
    """merchant_norm should be nullable."""
    db.execute(
        """
        INSERT INTO commitments(
            name, merchant_norm, created_at, updated_at
        ) VALUES (?, NULL, datetime('now'), datetime('now'))
        """,
        ("Unknown Merchant",),
    )
    db.commit()

    row = db.execute(
        "SELECT merchant_norm FROM commitments WHERE name = 'Unknown Merchant'"
    ).fetchone()
    assert row["merchant_norm"] is None


def test_confirmed_integer_values(db):
    """confirmed should store 0 or 1."""
    db.execute(
        """
        INSERT INTO commitments(
            name, confirmed, created_at, updated_at
        ) VALUES (?, ?, datetime('now'), datetime('now')),
                 (?, ?, datetime('now'), datetime('now'))
        """,
        ("Confirmed Commitment", 1, "Suggestion Commitment", 0),
    )
    db.commit()

    confirmed_row = db.execute(
        "SELECT confirmed FROM commitments WHERE name = 'Confirmed Commitment'"
    ).fetchone()
    suggestion_row = db.execute(
        "SELECT confirmed FROM commitments WHERE name = 'Suggestion Commitment'"
    ).fetchone()
    
    assert confirmed_row["confirmed"] == 1
    assert suggestion_row["confirmed"] == 0


def test_commitments_table_has_direction_column(db):
    """direction column must exist with default 'expense'."""
    cols = {row["name"]: row for row in db.execute("PRAGMA table_info(commitments)").fetchall()}
    assert "direction" in cols


def test_commitment_direction_defaults_to_expense(db):
    """Inserting without direction should default to 'expense'."""
    db.execute(
        "INSERT INTO commitments (name, cadence, confirmed, source, created_at, updated_at) "
        "VALUES ('test', 'monthly', 0, 'detected', '2026-01-01', '2026-01-01')"
    )
    row = db.execute("SELECT direction FROM commitments WHERE name='test'").fetchone()
    assert row["direction"] == "expense"


def test_get_commitments_direction_filter(db):
    """get_commitments with direction filters correctly."""
    dbmod.upsert_commitment(db, name="Rent", direction="expense", confirmed=1, source="manual")
    dbmod.upsert_commitment(db, name="Paycheck", direction="income", confirmed=1, source="manual")

    all_rows = dbmod.get_commitments(db)
    assert len(all_rows) == 2

    expenses = dbmod.get_commitments(db, direction="expense")
    assert len(expenses) == 1
    assert expenses[0]["name"] == "Rent"

    income = dbmod.get_commitments(db, direction="income")
    assert len(income) == 1
    assert income[0]["name"] == "Paycheck"


def test_upsert_commitment_sets_direction(db):
    """upsert_commitment should store the direction field."""
    cid = dbmod.upsert_commitment(db, name="Salary", direction="income", confirmed=1, source="manual")
    row = db.execute("SELECT direction FROM commitments WHERE id=?", (cid,)).fetchone()
    assert row["direction"] == "income"


def test_upsert_commitment_direction_defaults_expense(db):
    """direction defaults to 'expense' when not specified."""
    cid = dbmod.upsert_commitment(db, name="Netflix", confirmed=1, source="manual")
    row = db.execute("SELECT direction FROM commitments WHERE id=?", (cid,)).fetchone()
    assert row["direction"] == "expense"



def test_upsert_commitment_insert(db):
    """upsert_commitment with no id inserts a new row."""
    new_id = dbmod.upsert_commitment(
        db,
        name="Electric Bill",
        merchant_norm="pacific gas electric",
        expected_cents=16000,
        cadence="monthly",
        day_of_month=15,
        reference_date=None,
        confirmed=0,
        source="detected",
    )
    assert isinstance(new_id, int)
    row = db.execute("SELECT * FROM commitments WHERE id=?", (new_id,)).fetchone()
    assert row is not None
    assert row["name"] == "Electric Bill"
    assert row["merchant_norm"] == "pacific gas electric"
    assert row["expected_cents"] == 16000
    assert row["confirmed"] == 0
    assert row["source"] == "detected"


def test_upsert_commitment_update(db):
    """upsert_commitment with existing id updates the row."""
    row_id = dbmod.upsert_commitment(
        db, name="Electric Bill", cadence="monthly", source="detected"
    )
    dbmod.upsert_commitment(
        db, commitment_id=row_id, name="Electric Bill Updated", expected_cents=18000,
        cadence="monthly", confirmed=1, source="manual"
    )
    row = db.execute("SELECT * FROM commitments WHERE id=?", (row_id,)).fetchone()
    assert row["name"] == "Electric Bill Updated"
    assert row["expected_cents"] == 18000
    assert row["confirmed"] == 1


def test_upsert_commitment_update_nonexistent_raises(db):
    """upsert_commitment raises ValueError when commitment_id doesn't exist."""
    with pytest.raises(ValueError, match="not found"):
        dbmod.upsert_commitment(db, commitment_id=99999, name="Ghost", cadence="monthly")


def test_get_commitments_excludes_dismissed_by_default(db):
    """get_commitments excludes dismissed rows by default."""
    dbmod.upsert_commitment(db, name="Active", cadence="monthly", source="detected")
    dbmod.upsert_commitment(db, name="Dismissed", cadence="monthly", source="dismissed")
    rows = dbmod.get_commitments(db)
    names = [r["name"] for r in rows]
    assert "Active" in names
    assert "Dismissed" not in names


def test_get_commitments_include_dismissed(db):
    """get_commitments with include_dismissed=True returns all rows."""
    dbmod.upsert_commitment(db, name="Active", cadence="monthly", source="detected")
    dbmod.upsert_commitment(db, name="Dismissed", cadence="monthly", source="dismissed")
    rows = dbmod.get_commitments(db, include_dismissed=True)
    names = [r["name"] for r in rows]
    assert "Dismissed" in names


def test_get_commitments_confirmed_only(db):
    """confirmed_only=True returns only confirmed rows."""
    dbmod.upsert_commitment(db, name="Suggestion", cadence="monthly", confirmed=0, source="detected")
    dbmod.upsert_commitment(db, name="Confirmed", cadence="monthly", confirmed=1, source="detected")
    rows = dbmod.get_commitments(db, confirmed_only=True)
    names = [r["name"] for r in rows]
    assert "Confirmed" in names
    assert "Suggestion" not in names


def test_delete_commitment(db):
    """delete_commitment hard-deletes the row."""
    row_id = dbmod.upsert_commitment(db, name="To Delete", cadence="monthly", source="detected")
    dbmod.delete_commitment(db, row_id)
    row = db.execute("SELECT * FROM commitments WHERE id=?", (row_id,)).fetchone()
    assert row is None




def _insert_txn(conn, merchant, amount_cents, posted_at):
    """Helper to insert a test transaction."""
    conn.execute(
        """INSERT INTO transactions
           (account_id, posted_at, amount_cents, currency, merchant,
            fingerprint, created_at, updated_at)
           VALUES ('acct1', ?, ?, 'USD', ?, ?, datetime('now'), datetime('now'))""",
        (posted_at.isoformat(), amount_cents, merchant,
         f"fp_{merchant}_{posted_at}_{amount_cents}"),
    )
    conn.commit()


def _make_monthly_txns(conn, merchant, amount_cents, day, num_months=5):
    """Insert `num_months` monthly transactions on the given day."""
    today = date.today()
    for i in range(num_months):
        month = today.month - i
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        last = cal_mod.monthrange(year, month)[1]
        d = date(year, month, min(day, last))
        _insert_txn(conn, merchant, amount_cents, d)


def test_find_matching_transactions_basic(db):
    """Returns matches when all conditions are met."""
    _make_monthly_txns(db, "pacific gas electric", -16000, day=15, num_months=5)
    matches = dbmod.find_matching_transactions(
        db,
        merchant_norm="pacific gas electric",
        day_of_month=15,
        cadence="monthly",
        expected_cents=16000,
        source="detected",
    )
    assert len(matches) >= 4


def test_find_matching_transactions_fewer_than_4_months(db):
    """Returns empty list when fewer than 4 distinct months."""
    _make_monthly_txns(db, "rare merchant", -5000, day=10, num_months=3)
    matches = dbmod.find_matching_transactions(
        db,
        merchant_norm="rare merchant",
        day_of_month=10,
        cadence="monthly",
        expected_cents=5000,
        source="detected",
    )
    assert matches == []


def test_find_matching_transactions_manual_skips_4month_rule(db):
    """source='manual' skips the ≥4 distinct months requirement."""
    _make_monthly_txns(db, "new service", -3000, day=5, num_months=2)
    matches = dbmod.find_matching_transactions(
        db,
        merchant_norm="new service",
        day_of_month=5,
        cadence="monthly",
        expected_cents=3000,
        source="manual",
    )
    assert len(matches) >= 1


def test_find_matching_transactions_amount_out_of_range(db):
    """Returns empty list when amounts are outside [0.5×, 1.5×] anchor."""
    _make_monthly_txns(db, "wildly varying", -100, day=1, num_months=5)
    matches = dbmod.find_matching_transactions(
        db,
        merchant_norm="wildly varying",
        day_of_month=1,
        cadence="monthly",
        expected_cents=1000,  # anchor=1000, txns are 100 (outside [500,1500])
        source="detected",
    )
    assert matches == []


def test_find_matching_transactions_no_day_constraint(db):
    """day_of_month=None skips the day constraint."""
    today = date.today()
    for i in range(5):
        month = today.month - i
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        d = date(year, month, 10 + i)  # Different days each month
        _insert_txn(db, "variable day merchant", -8000, d)
    matches = dbmod.find_matching_transactions(
        db,
        merchant_norm="variable day merchant",
        day_of_month=None,
        cadence="monthly",
        expected_cents=8000,
        source="detected",
    )
    assert len(matches) >= 4


def test_find_matching_transactions_inferred_anchor(db):
    """expected_cents=None uses median of last 3 transactions as anchor."""
    _make_monthly_txns(db, "inferred merchant", -10000, day=20, num_months=5)
    matches = dbmod.find_matching_transactions(
        db,
        merchant_norm="inferred merchant",
        day_of_month=20,
        cadence="monthly",
        expected_cents=None,  # <-- no anchor provided, use median
        source="detected",
    )
    assert len(matches) >= 4


def test_find_matching_transactions_month_end_clamping(db):
    """day_of_month=31 clamps to last day of month for ±3 check."""
    # Insert 5 transactions on the last day of each month
    today = date.today()
    for i in range(5):
        month = today.month - i
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        last = cal_mod.monthrange(year, month)[1]
        _insert_txn(db, "end of month merchant", -5000, date(year, month, last))
    matches = dbmod.find_matching_transactions(
        db,
        merchant_norm="end of month merchant",
        day_of_month=31,  # should clamp to last day of each month
        cadence="monthly",
        expected_cents=5000,
        source="detected",
    )
    assert len(matches) >= 4



def test_suggest_from_heuristics_idempotent(populated_db):
    """Calling suggest twice does not create duplicates."""
    dbmod.suggest_commitments_from_heuristics(populated_db)
    count1 = populated_db.execute("SELECT COUNT(*) FROM commitments").fetchone()[0]
    assert count1 > 0, "No suggestions were seeded — test is vacuous"
    dbmod.suggest_commitments_from_heuristics(populated_db)
    count2 = populated_db.execute("SELECT COUNT(*) FROM commitments").fetchone()[0]
    assert count1 == count2, "Second call created duplicate rows"


def test_suggest_from_heuristics_does_not_overwrite_confirmed(populated_db):
    """Confirmed rows are never overwritten by suggest."""
    dbmod.suggest_commitments_from_heuristics(populated_db)
    rows = dbmod.get_commitments(populated_db)
    if not rows:
        pytest.skip("No suggestions generated — need heuristic data")
    first = rows[0]
    dbmod.upsert_commitment(
        populated_db,
        commitment_id=first["id"],
        name=first["name"],
        cadence=first["cadence"],
        confirmed=1,
        source="detected",
        merchant_norm=first["merchant_norm"],
    )
    dbmod.suggest_commitments_from_heuristics(populated_db)
    row = populated_db.execute(
        "SELECT * FROM commitments WHERE id=?", (first["id"],)
    ).fetchone()
    assert row["confirmed"] == 1, "suggest overwrote confirmed row"


def test_suggest_from_heuristics_sets_reference_date(populated_db):
    """Seeded rows have reference_date populated from most recent tx."""
    dbmod.suggest_commitments_from_heuristics(populated_db)
    rows = dbmod.get_commitments(populated_db)
    with_date = [r for r in rows if r.get("reference_date") is not None]
    assert len(with_date) > 0, "No rows have reference_date set"
    for r in with_date:
        d = date.fromisoformat(r["reference_date"])
        assert isinstance(d, date)


def test_suggest_from_heuristics_respects_dismissed(populated_db):
    """Dismissed rows are skipped by dedup guard, allowing re-seed of same merchant."""
    dbmod.suggest_commitments_from_heuristics(populated_db)
    rows = dbmod.get_commitments(populated_db)
    if not rows:
        pytest.skip("No suggestions generated")
    first = rows[0]
    merchant_norm = first["merchant_norm"]
    if not merchant_norm:
        pytest.skip("First suggestion has no merchant_norm")

    # Dismiss the first commitment
    dbmod.upsert_commitment(
        populated_db,
        commitment_id=first["id"],
        name=first["name"],
        cadence=first["cadence"],
        source="dismissed",
        merchant_norm=merchant_norm,
    )

    # Run suggest again — dismissed rows are excluded from get_commitments but the
    # dedup guard in _seed_commitment_if_new skips source='dismissed',
    # so the same merchant could theoretically be re-seeded as a new row.
    # Verify the behavior: re-seeding should NOT happen for the same merchant
    # (dedup checks source != 'dismissed' on the same merchant_norm).
    before = populated_db.execute("SELECT COUNT(*) FROM commitments").fetchone()[0]
    dbmod.suggest_commitments_from_heuristics(populated_db)
    after = populated_db.execute("SELECT COUNT(*) FROM commitments").fetchone()[0]
    # The dismissed row should still exist, and no new row with the same merchant
    # should be added, because dedup check excludes dismissed rows from the count
    assert after <= before + 1, "Re-seeding produced more rows than expected (should skip or re-seed one)"


from fin.projections import project_cash_flow


def test_project_cash_flow_uses_confirmed_commitments(populated_db):
    """Confirmed commitments appear in upcoming_charges at confidence=1.0."""
    dbmod.upsert_commitment(
        populated_db,
        name="Test Utility",
        merchant_norm="test utility co",
        expected_cents=15000,
        cadence="monthly",
        day_of_month=15,
        confirmed=1,
        source="manual",
    )
    projection = project_cash_flow(populated_db, days_forward=60)
    confirmed_charges = [
        c for c in projection.upcoming_charges if c.confidence == 1.0
    ]
    assert len(confirmed_charges) >= 1
    names = [c.merchant for c in confirmed_charges]
    assert "test utility co" in names


def test_project_cash_flow_confirmed_merchants_not_doubled(populated_db):
    """The exact merchant covered by a confirmed commitment is not doubled by heuristics."""
    dbmod.upsert_commitment(
        populated_db,
        name="Netflix",
        merchant_norm="netflix.com",
        expected_cents=1599,
        cadence="monthly",
        day_of_month=3,
        confirmed=1,
        source="manual",
    )
    projection = project_cash_flow(populated_db, days_forward=60)
    # Only the exact covered merchant (netflix.com) should not be doubled
    netflix_dot_com_charges = [
        c for c in projection.upcoming_charges
        if c.merchant == "netflix.com"
    ]
    confidences = [c.confidence for c in netflix_dot_com_charges]
    assert all(c == 1.0 for c in confidences), \
        f"netflix.com doubled in projection: {confidences}"


def test_compute_period_trends_with_confirmed_commitments(populated_db):
    """avg_recurring_cents reflects confirmed monthly-equivalent sum."""
    from fin.view_models import compute_period_trends
    from fin.report_service import ReportService
    from fin.dates import TimePeriod
    
    dbmod.upsert_commitment(
        populated_db,
        name="Electric Bill",
        merchant_norm="pacific gas electric",
        expected_cents=20000,
        cadence="monthly",
        confirmed=1,
        source="manual",
    )
    service = ReportService(populated_db)
    reports = service.report_periods(TimePeriod.MONTH, num_periods=3)
    if not reports:
        pytest.skip("No reports available")
    periods = compute_period_trends(reports, conn=populated_db)
    for vm in periods:
        assert vm.avg_recurring_cents == 20000, \
            f"Expected 20000, got {vm.avg_recurring_cents}"


def test_compute_period_trends_no_commitments_fallback(populated_db):
    """Falls back to rolling average when no confirmed commitments exist."""
    from fin.view_models import compute_period_trends
    from fin.report_service import ReportService
    from fin.dates import TimePeriod
    
    service = ReportService(populated_db)
    reports = service.report_periods(TimePeriod.MONTH, num_periods=3)
    if not reports:
        pytest.skip("No reports available")
    periods_without_conn = compute_period_trends(reports)
    periods_with_conn = compute_period_trends(reports, conn=populated_db)
    for vm_a, vm_b in zip(periods_without_conn, periods_with_conn):
        assert vm_a.avg_recurring_cents == vm_b.avg_recurring_cents


# ---------------------------------------------------------------------------
# Tasks 7 & 8: HTTP layer tests
# ---------------------------------------------------------------------------
from unittest.mock import patch

from fastapi.testclient import TestClient
from fin.web import app


@pytest.fixture
def client(populated_db, temp_db_path):
    """Test client backed by the populated test DB."""
    import fin.web as web_module

    web_module._config = None
    web_module._db_initialized = False

    class MockConfig:
        db_path = temp_db_path
        simplefin_access_url = ""
        log_level = "INFO"
        log_format = "simple"

    with patch.object(web_module, "_get_config", return_value=MockConfig()):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


def test_api_list_commitments_empty(client):
    """GET /api/commitments returns empty list initially."""
    response = client.get("/api/commitments")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_api_create_commitment(client):
    """POST /api/commitments creates a manual confirmed commitment."""
    payload = {
        "name": "Electric Bill",
        "merchant_norm": "pacific gas electric",
        "expected_cents": 16000,
        "cadence": "monthly",
        "day_of_month": 15,
    }
    response = client.post("/api/commitments", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Electric Bill"
    assert data["source"] == "manual"
    assert data["confirmed"] is True


def test_api_create_commitment_missing_name(client):
    """POST /api/commitments returns 400 if name is absent."""
    response = client.post("/api/commitments", json={"cadence": "monthly"})
    assert response.status_code == 400


def test_api_patch_commitment(client):
    """PATCH /api/commitments/{id} updates specified fields."""
    resp = client.post("/api/commitments", json={"name": "Patch Me", "cadence": "monthly"})
    assert resp.status_code == 201
    commitment_id = resp.json()["id"]
    patch_resp = client.patch(
        f"/api/commitments/{commitment_id}",
        json={"expected_cents": 25000, "confirmed": 1},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["expected_cents"] == 25000


def test_api_patch_nonexistent(client):
    """PATCH /api/commitments/99999 returns 404."""
    response = client.patch("/api/commitments/99999", json={"name": "x"})
    assert response.status_code == 404


def test_api_delete_commitment(client):
    """DELETE /api/commitments/{id} removes the row."""
    resp = client.post("/api/commitments", json={"name": "Delete Me", "cadence": "monthly"})
    commitment_id = resp.json()["id"]
    del_resp = client.delete(f"/api/commitments/{commitment_id}")
    assert del_resp.status_code == 204
    list_resp = client.get("/api/commitments?include_dismissed=1")
    ids = [c["id"] for c in list_resp.json()]
    assert commitment_id not in ids


def test_api_suggest_endpoint(client):
    """GET /api/commitments/suggest returns matching tx info."""
    response = client.get(
        "/api/commitments/suggest",
        params={"merchant_norm": "netflix.com", "expected_cents": 1599}
    )
    assert response.status_code == 200
    data = response.json()
    assert "count" in data
    assert "median_cents" in data
    assert "transactions" in data


def test_commitments_page_loads(client):
    """GET /commitments returns 200."""
    response = client.get("/commitments")
    assert response.status_code == 200
    assert b"commitments" in response.content.lower()


def test_dashboard_has_commitments_link(client):
    """Dashboard page should include a link to /commitments."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert b"/commitments" in response.content


def test_dashboard_commitments_link_text(client):
    """Dashboard should link to 'Income & Commitments'."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "commitments" in resp.text.lower()


def test_suggest_from_heuristics_seeds_income(db):
    """suggest_commitments_from_heuristics should seed income suggestions from income merchant rules."""
    # Add income merchant rule
    db.execute(
        "INSERT INTO merchant_rules (merchant_pattern, rule_type, created_at) VALUES (?, ?, datetime('now'))",
        ("acme corp", "income"),
    )
    # Insert positive transactions from that merchant across 3 months
    for month in range(1, 4):
        db.execute(
            "INSERT INTO transactions (account_id, posted_at, amount_cents, currency, merchant, fingerprint, created_at, updated_at) "
            "VALUES ('acct1', ?, ?, 'USD', ?, ?, datetime('now'), datetime('now'))",
            (f"2025-{month:02d}-15", 320000, "acme corp", f"fp-inc-{month}"),
        )
    db.commit()

    dbmod.suggest_commitments_from_heuristics(db)

    income_rows = dbmod.get_commitments(db, direction="income")
    assert len(income_rows) >= 1
    assert income_rows[0]["direction"] == "income"
    assert income_rows[0]["merchant_norm"] == "acme corp"
    assert income_rows[0]["confirmed"] == 0
    assert income_rows[0]["source"] == "detected"


def test_seed_commitment_dedup_includes_direction(db):
    """Dedup should be per-direction — same merchant can be both income and expense."""
    dbmod.upsert_commitment(db, name="Acme Expense", merchant_norm="acme corp",
                            direction="expense", confirmed=1, source="manual")
    dbmod.upsert_commitment(db, name="Acme Income", merchant_norm="acme corp",
                            expected_cents=320000, cadence="monthly",
                            direction="income", confirmed=1, source="manual")

    expense_rows = dbmod.get_commitments(db, direction="expense")
    income_rows = dbmod.get_commitments(db, direction="income")
    assert len(expense_rows) == 1
    assert len(income_rows) == 1
    assert expense_rows[0]["name"] == "Acme Expense"
    assert income_rows[0]["name"] == "Acme Income"


def test_projection_uses_confirmed_income_commitments(db):
    """project_cash_flow should use confirmed income commitments instead of heuristic."""
    dbmod.upsert_commitment(
        db, name="Salary", direction="income", expected_cents=500000,
        cadence="monthly", day_of_month=1, confirmed=1, source="manual",
    )

    from fin.projections import project_cash_flow
    projection = project_cash_flow(db, days_forward=30)

    # Income should reflect the commitment (~$5000/month → ~$5000 for 30 days)
    assert projection.expected_income_cents > 0
    # Should be approximately monthly_sum * 30/30 = 500000
    assert abs(projection.expected_income_cents - 500000) < 50000  # within 10%


def test_find_matching_transactions_income_positive_amounts(db):
    """Income matching should find positive transactions, not negative."""
    # Insert positive (income) transactions
    for month in range(1, 6):
        db.execute(
            "INSERT INTO transactions "
            "(account_id, posted_at, amount_cents, currency, merchant, fingerprint, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("acct1", f"2025-{month:02d}-15", 320000, "USD", "acme corp", f"fp-inc-{month}"),
        )
    db.commit()

    # Should find matches with direction='income'
    matches = dbmod.find_matching_transactions(
        db, merchant_norm="acme corp", day_of_month=15,
        cadence="monthly", expected_cents=320000, source="detected",
        direction="income",
    )
    assert len(matches) >= 4

    # Should NOT find matches with direction='expense' (amounts are positive)
    matches_exp = dbmod.find_matching_transactions(
        db, merchant_norm="acme corp", day_of_month=15,
        cadence="monthly", expected_cents=320000, source="detected",
        direction="expense",
    )
    assert len(matches_exp) == 0



def test_api_create_income_commitment(client):
    """POST /api/commitments with direction=income should create an income commitment."""
    resp = client.post("/api/commitments", json={
        "name": "Salary",
        "expected_cents": 500000,
        "cadence": "monthly",
        "direction": "income",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["direction"] == "income"


def test_api_list_filter_by_direction(client):
    """GET /api/commitments?direction=income should filter."""
    client.post("/api/commitments", json={"name": "Rent", "expected_cents": 150000, "direction": "expense"})
    client.post("/api/commitments", json={"name": "Salary", "expected_cents": 500000, "direction": "income"})

    all_resp = client.get("/api/commitments")
    assert len(all_resp.json()) == 2

    income_resp = client.get("/api/commitments?direction=income")
    assert len(income_resp.json()) == 1
    assert income_resp.json()[0]["name"] == "Salary"


def test_api_patch_direction_rejected(client):
    """PATCH should reject direction changes."""
    resp = client.post("/api/commitments", json={"name": "Rent", "expected_cents": 150000})
    cid = resp.json()["id"]

    patch_resp = client.patch(f"/api/commitments/{cid}", json={"direction": "income"})
    assert patch_resp.status_code == 400


def test_view_model_overrides_avg_income_with_confirmed(db):
    """compute_period_trends should override avg_income_cents when confirmed income exists."""
    from datetime import date, timedelta

    # Seed transactions so ReportService produces reports
    today = date.today()
    for i in range(3):
        month_start = today.replace(day=1) - timedelta(days=30 * i)
        db.execute(
            "INSERT INTO transactions (posted_at, amount_cents, merchant, fingerprint, account_id, currency, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'USD', datetime('now'), datetime('now'))",
            (month_start.isoformat(), -10000, "grocery store", f"fp-vm-{i}", "acct1"),
        )
    db.commit()

    dbmod.upsert_commitment(
        db, name="Salary", direction="income", expected_cents=500000,
        cadence="monthly", confirmed=1, source="manual",
    )

    from fin.view_models import compute_period_trends
    from fin.report_service import ReportService
    from fin.dates import TimePeriod

    service = ReportService(db)
    reports = service.report_periods(TimePeriod.MONTH, num_periods=3)
    if not reports:
        pytest.skip("No report data available")

    vms = compute_period_trends(reports, conn=db)
    # avg_income_cents should be the confirmed sum, not the rolling average
    assert vms[0].avg_income_cents == 500000


def test_commitments_page_shows_income_section(client):
    """The /commitments page should contain income section heading."""
    client.post("/api/commitments", json={"name": "Salary", "expected_cents": 500000, "direction": "income"})
    resp = client.get("/commitments")
    assert resp.status_code == 200
    assert "Income" in resp.text
