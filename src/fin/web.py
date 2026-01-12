from datetime import date, timedelta
from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

from .config import load_config
from . import db as dbmod

app = FastAPI()

def _conn():
    cfg = load_config()
    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)
    return conn

def _rows_to_table(rows, cols):
    th = "".join([f"<th>{c}</th>" for c in cols])
    trs = []
    for r in rows:
        tds = "".join([f"<td>{(r.get(c) if isinstance(r, dict) else r[c])}</td>" for c in cols])
        trs.append(f"<tr>{tds}</tr>")
    return f"<table border='1' cellspacing='0' cellpadding='6'><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(
        "<h2>fin</h2>"
        "<ul>"
        "<li><a href='/subs'>Recurring candidates</a></li>"
        "<li><a href='/watchlist'>Watchlist</a></li>"
        "<li><a href='/anomalies'>Anomalies (basic)</a></li>"
        "</ul>"
    )

@app.get("/subs", response_class=HTMLResponse)
def subs(days: int = 400, limit: int = 50):
    conn = _conn()
    try:
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """
            SELECT
              TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), ''))) AS payee_norm,
              COUNT(*) AS occurrences,
              CAST(AVG(ABS(amount_cents)) AS INTEGER) AS avg_abs_amount_cents,
              MIN(posted_at) AS first_seen,
              MAX(posted_at) AS last_seen
            FROM transactions
            WHERE posted_at >= ?
              AND amount_cents < 0
              AND payee_norm <> ''
            GROUP BY payee_norm
            HAVING occurrences >= 3
            ORDER BY avg_abs_amount_cents DESC, occurrences DESC
            LIMIT ?
            """,
            (since, limit),
        ).fetchall()
        cols = ["payee_norm", "occurrences", "avg_abs_amount_cents", "first_seen", "last_seen"]
        return HTMLResponse("<h3>Recurring candidates</h3>" + _rows_to_table(rows, cols) + "<p><a href='/'>home</a></p>")
    finally:
        conn.close()

@app.get("/watchlist", response_class=HTMLResponse)
def watchlist():
    path = Path("/app/exports/watchlist.csv")
    if not path.exists():
        return HTMLResponse("<h3>Watchlist</h3><p>No watchlist.csv found.</p><p><a href='/'>home</a></p>")

    import csv
    rows = []
    with path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append(row)

    if not rows:
        return HTMLResponse("<h3>Watchlist</h3><p>Empty.</p><p><a href='/'>home</a></p>")

    cols = list(rows[0].keys())
    return HTMLResponse("<h3>Watchlist</h3>" + _rows_to_table(rows, cols) + "<p><a href='/'>home</a></p>")

@app.get("/anomalies", response_class=HTMLResponse)
def anomalies(days: int = 60, limit: int = 50):
    conn = _conn()
    try:
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """
            SELECT
              posted_at,
              TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), ''))) AS payee_norm,
              amount_cents
            FROM transactions
            WHERE posted_at >= ?
            ORDER BY ABS(amount_cents) DESC
            LIMIT ?
            """,
            (since, limit),
        ).fetchall()
        cols = ["posted_at", "payee_norm", "amount_cents"]
        return HTMLResponse("<h3>Largest transactions (proxy anomalies)</h3>" + _rows_to_table(rows, cols) + "<p><a href='/'>home</a></p>")
    finally:
        conn.close()

def main():
    # localhost only
    uvicorn.run("fin.web:app", host="0.0.0.0", port=8000, reload=False)

if __name__ == "__main__":
    main()
