import typer
from datetime import date, timedelta
import logging
from rich.console import Console

from .config import load_config
from .log import setup_logging
from . import db as dbmod
from .simplefin_client import SimpleFinClient
from .models import Account
from .normalize import normalize_simplefin_txn

app = typer.Typer(no_args_is_help=True)
console = Console()
log = logging.getLogger("fin")

DEFAULT_LOOKBACK_DAYS = 120
JAN_ANNUAL_BOOTSTRAP_LOOKBACK = 400  # pragmatic: run in January for annual subs


def _require_simplefin(cfg):
    if not cfg.simplefin_access_url:
        raise typer.BadParameter("Missing SIMPLEFIN_ACCESS_URL (use .env; do not commit).")


@app.command()
def sync(
    lookback_days: int = typer.Option(DEFAULT_LOOKBACK_DAYS, help="How many days to pull (default 120)."),
    annual_bootstrap: bool = typer.Option(False, help="Use ~400-day lookback for annual subscription discovery (recommended in January)."),
):
    """
    Pull accounts + transactions, normalize, upsert to SQLite, record run.
    """
    cfg = load_config()
    setup_logging(cfg)
    _require_simplefin(cfg)

    effective_lookback = JAN_ANNUAL_BOOTSTRAP_LOOKBACK if annual_bootstrap else lookback_days
    start = date.today() - timedelta(days=effective_lookback)
    end_exclusive = date.today() + timedelta(days=1)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    client = SimpleFinClient(cfg)
    try:
        # Accounts payload shape: {"errors":[], "accounts":[...]}
        raw = client.fetch_accounts()
        raw_accounts = raw.get("accounts", [])

        accounts = []
        for ra in raw_accounts:
            accounts.append(Account(
                account_id=str(ra.get("id")),
                institution=str(ra.get("org", {}).get("name", "UNKNOWN")),
                name=str(ra.get("name", "UNKNOWN")),
                type=ra.get("type"),
                currency=ra.get("currency", "USD"),
            ))
        dbmod.upsert_accounts(conn, accounts)

        # Transactions: fetch ranged account set (client chunks internally), then read acct["transactions"]
        acctset = client.fetch_account_set_range(start_date=start, end_date_exclusive=end_exclusive)
        acct_list = acctset.get("accounts", [])
        tx_by_account = {str(a.get("id")): (a.get("transactions") or []) for a in acct_list}

        fetched = 0
        normalized = []
        for a in accounts:
            raw_txns = tx_by_account.get(a.account_id, [])
            fetched += len(raw_txns)
            for rt in raw_txns:
                normalized.append(normalize_simplefin_txn(rt, a.account_id))

        inserted, updated = dbmod.upsert_transactions(conn, normalized)
        dbmod.record_run(conn, effective_lookback, fetched, inserted, updated)

        console.print(f"[green]sync complete[/green] accounts={len(accounts)} fetched={fetched} inserted={inserted} updated={updated}")
    finally:
        client.close()
        conn.close()


@app.command()
def db_info():
    cfg = load_config()
    setup_logging(cfg)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)
    try:
        cur = conn.execute("SELECT COUNT(*) AS c FROM accounts")
        ac = cur.fetchone()["c"]
        cur = conn.execute("SELECT COUNT(*) AS c FROM transactions")
        tc = cur.fetchone()["c"]
        cur = conn.execute("SELECT MAX(posted_at) AS maxd, MIN(posted_at) AS mind FROM transactions")
        row = cur.fetchone()
        console.print(f"accounts={ac} transactions={tc} range={row['mind']}..{row['maxd']}")
    finally:
        conn.close()

@app.command()
def report(
    days: int = typer.Option(120, help="Lookback window in days for reporting."),
    top: int = typer.Option(15, help="How many items to show per section."),
):
    """
    Local report: subscriptions + basic anomalies (new merchants, spikes, duplicates).
    """
    from collections import defaultdict, Counter
    from datetime import datetime, timedelta
    import statistics

    cfg = load_config()
    setup_logging(cfg)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    def parse_date(s: str) -> datetime.date:
        return datetime.fromisoformat(s).date()

    def cents_to_usd(c: int) -> str:
        return f"${c/100:,.2f}"

    def median(xs):
        return statistics.median(xs) if xs else 0

    def mad(xs):
        if not xs:
            return 0
        m = median(xs)
        return median([abs(x - m) for x in xs])

    try:
        since = (date.today() - timedelta(days=days)).isoformat()

        rows = conn.execute(
            """
            SELECT posted_at, amount_cents,
                   COALESCE(NULLIF(merchant,''), NULLIF(description,'')) AS label
            FROM transactions
            WHERE posted_at >= ?
              AND label IS NOT NULL
              AND label <> ''
            """,
            (since,),
        ).fetchall()

        if not rows:
            console.print("[yellow]No transactions found in window.[/yellow]")
            return

        # Build per-merchant history
        hist = defaultdict(list)  # label -> list[(date, amount)]
        by_day = defaultdict(list)  # (label, posted_at, amount) -> count helper
        all_dates = []
        for r in rows:
            d = parse_date(r["posted_at"])
            a = int(r["amount_cents"])
            label = str(r["label"])
            hist[label].append((d, a))
            by_day[(label, d, a)].append(1)
            all_dates.append(d)

        # --------------------
        # Subscriptions
        # --------------------
        subs = []
        for label, items in hist.items():
            if len(items) < 3:
                continue
            items.sort(key=lambda x: x[0])
            dates = [d for d, _ in items]
            amts = [a for _, a in items if a < 0]
            if len(amts) < 3:
                continue

            deltas = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))]
            if not deltas:
                continue

            md = median(deltas)
            # Identify cadence
            cadence = None
            tol = None
            if 5 <= md <= 9:
                cadence, tol = 7, 2
            elif 25 <= md <= 35:
                cadence, tol = 30, 5
            elif 330 <= md <= 400:
                cadence, tol = 365, 20
            else:
                continue

            # How well do deltas fit?
            fit = sum(1 for d in deltas if abs(d - cadence) <= tol) / max(1, len(deltas))

            amt_med = int(median(amts))
            amt_mad = mad(amts)
            # stable if within ~$2 or within 5% of median (whichever larger)
            stable_band = max(200, int(abs(amt_med) * 0.05))
            stable = amt_mad <= stable_band

            if fit < 0.66 or not stable:
                continue

            last_seen = dates[-1]
            next_expected = last_seen + timedelta(days=cadence)
            if cadence == 365:
                monthly_est = int(round(abs(amt_med) / 12))
            elif cadence == 30:
                monthly_est = abs(amt_med)
            else:  # weekly-ish
                monthly_est = int(round(abs(amt_med) * 4.33))

            # Confidence (simple and explainable)
            confidence = 0.0
            confidence += 0.5 * fit
            confidence += 0.3 if stable else 0.0
            confidence += 0.2 if (date.today() - last_seen).days <= (cadence + tol) else 0.0

            subs.append({
                "label": label,
                "count": len(items),
                "cadence": cadence,
                "amount": amt_med,
                "confidence": confidence,
                "last": last_seen,
                "next": next_expected,
                "monthly": monthly_est,
            })

        subs.sort(key=lambda x: (x["confidence"], x["monthly"]), reverse=True)

        # --------------------
        # Anomalies
        # --------------------
        # New merchants: seen in last 14d but not in prior 90d (within the available window)
        today = date.today()
        recent_cut = today - timedelta(days=14)
        prior_cut = today - timedelta(days=104)

        recent_labels = set()
        prior_labels = set()
        for label, items in hist.items():
            for d, _ in items:
                if d >= recent_cut:
                    recent_labels.add(label)
                elif prior_cut <= d < recent_cut:
                    prior_labels.add(label)

        new_merchants = sorted(list(recent_labels - prior_labels))

        # Spikes: latest txn > 2.5x median for that label and > $100
        spikes = []
        for label, items in hist.items():
            if len(items) < 6:
                continue
            items.sort(key=lambda x: x[0])
            latest_d, latest_a = items[-1]
            amts = [a for _, a in items[:-1]]
            m = median(amts)
            if m == 0:
                continue
            if abs(latest_a) > max(int(abs(m) * 2.5), 10000):
                spikes.append((label, latest_d, latest_a, int(m)))
        spikes.sort(key=lambda x: abs(x[2]), reverse=True)

        # Duplicate-like: same label/date/amount appears more than once
        dups = []
        for (label, d, a), ones in by_day.items():
            if len(ones) > 1:
                dups.append((label, d, a, len(ones)))
        dups.sort(key=lambda x: x[3], reverse=True)

        # --------------------
        # Print
        # --------------------
        console.print(f"[bold]Report window:[/bold] last {days} days (since {since})")
        console.print("")

        console.print("[bold green]Subscription candidates[/bold green]")
        if not subs:
            console.print("  (none found)")
        else:
            for s in subs[:top]:
                cadence_label = "weekly" if s["cadence"] == 7 else ("monthly" if s["cadence"] == 30 else "annual")
                console.print(
                    f"  • {s['label']}  "
                    f"[dim]({cadence_label}, ~{cents_to_usd(s['amount'])}, conf={s['confidence']:.2f}, "
                    f"last={s['last']}, next≈{s['next']}, est/mo={cents_to_usd(s['monthly'])})[/dim]"
                )

        console.print("")
        console.print("[bold yellow]Anomalies[/bold yellow]")

        console.print("  [bold]New merchants (last 14d, not seen prior)[/bold]")
        if not new_merchants:
            console.print("    (none)")
        else:
            for m in new_merchants[:top]:
                console.print(f"    • {m}")

        console.print("  [bold]Amount spikes (latest vs baseline)[/bold]")
        if not spikes:
            console.print("    (none)")
        else:
            for label, d, a, med in spikes[:top]:
                console.print(f"    • {label} on {d}: {cents_to_usd(a)} vs baseline {cents_to_usd(med)}")

        console.print("  [bold]Duplicate-like[/bold]")
        if not dups:
            console.print("    (none)")
        else:
            for label, d, a, n in dups[:top]:
                console.print(f"    • {label} on {d}: {cents_to_usd(a)} × {n}")

    finally:
        conn.close()

@app.command()
def export_csv(
    out: str = typer.Option("/app/exports", help="Output directory for CSV exports."),
    days: int = typer.Option(400, help="Lookback window in days to export."),
):
    """
    Export normalized transactions + daily/weekly/monthly rollups (Google Sheets friendly).
    """
    import csv
    import os
    from datetime import datetime, timedelta

    cfg = load_config()
    setup_logging(cfg)

    os.makedirs(out, exist_ok=True)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    try:
        since = (date.today() - timedelta(days=days)).isoformat()

        tx = conn.execute(
            """
            SELECT
              posted_at,
              account_id,
              amount_cents,
              currency,
              COALESCE(NULLIF(merchant,''), '') AS merchant,
              COALESCE(NULLIF(description,''), '') AS description
            FROM transactions
            WHERE posted_at >= ?
            ORDER BY posted_at ASC
            """,
            (since,),
        ).fetchall()

        # ---- transactions.csv
        tx_path = os.path.join(out, "transactions.csv")
        with open(tx_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["posted_at", "account_id", "amount_cents", "currency", "merchant", "description"])
            for r in tx:
                w.writerow([r["posted_at"], r["account_id"], r["amount_cents"], r["currency"], r["merchant"], r["description"]])

        # ---- subscription_candidates.csv (simple heuristic export)
        subs_path = os.path.join(out, "subscription_candidates.csv")
        rows = conn.execute(
            """
            SELECT
            COALESCE(NULLIF(merchant,''), NULLIF(description,'')) AS label,
            COUNT(*) AS occurrences,
            MIN(posted_at) AS first_seen,
            MAX(posted_at) AS last_seen,
            CAST(AVG(ABS(amount_cents)) AS INTEGER) AS avg_abs_amount_cents
            FROM transactions
            WHERE posted_at >= ?
            AND label IS NOT NULL
            AND label <> ''
            AND amount_cents < 0
            GROUP BY label
            HAVING occurrences >= 3
            ORDER BY occurrences DESC, avg_abs_amount_cents DESC
            """,
            (since,),
        ).fetchall()

        with open(subs_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["label", "occurrences", "first_seen", "last_seen", "avg_abs_amount_cents"])
            for r in rows:
                w.writerow([r["label"], r["occurrences"], r["first_seen"], r["last_seen"], r["avg_abs_amount_cents"]])


        # ---- daily rollup
        daily = conn.execute(
            """
            SELECT posted_at,
                   SUM(amount_cents) AS net_cents,
                   SUM(CASE WHEN amount_cents < 0 THEN -amount_cents ELSE 0 END) AS outflow_cents,
                   SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END) AS inflow_cents,
                   COUNT(*) AS txn_count
            FROM transactions
            WHERE posted_at >= ?
            GROUP BY posted_at
            ORDER BY posted_at ASC
            """,
            (since,),
        ).fetchall()

        daily_path = os.path.join(out, "daily_rollup.csv")
        with open(daily_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "net_cents", "outflow_cents", "inflow_cents", "txn_count"])
            for r in daily:
                w.writerow([r["posted_at"], r["net_cents"], r["outflow_cents"], r["inflow_cents"], r["txn_count"]])

        # ---- weekly rollup (Mon-start)
        weekly = conn.execute(
            """
            SELECT
              strftime('%Y-%W', posted_at) AS year_week,
              MIN(posted_at) AS week_start_date,
              SUM(amount_cents) AS net_cents,
              SUM(CASE WHEN amount_cents < 0 THEN -amount_cents ELSE 0 END) AS outflow_cents,
              SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END) AS inflow_cents,
              COUNT(*) AS txn_count
            FROM transactions
            WHERE posted_at >= ?
            GROUP BY year_week
            ORDER BY year_week ASC
            """,
            (since,),
        ).fetchall()

        weekly_path = os.path.join(out, "weekly_rollup.csv")
        with open(weekly_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["year_week", "week_start_date", "net_cents", "outflow_cents", "inflow_cents", "txn_count"])
            for r in weekly:
                w.writerow([r["year_week"], r["week_start_date"], r["net_cents"], r["outflow_cents"], r["inflow_cents"], r["txn_count"]])

        # ---- monthly rollup
        monthly = conn.execute(
            """
            SELECT
              strftime('%Y-%m', posted_at) AS year_month,
              SUM(amount_cents) AS net_cents,
              SUM(CASE WHEN amount_cents < 0 THEN -amount_cents ELSE 0 END) AS outflow_cents,
              SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END) AS inflow_cents,
              COUNT(*) AS txn_count
            FROM transactions
            WHERE posted_at >= ?
            GROUP BY year_month
            ORDER BY year_month ASC
            """,
            (since,),
        ).fetchall()

        monthly_path = os.path.join(out, "monthly_rollup.csv")
        with open(monthly_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["year_month", "net_cents", "outflow_cents", "inflow_cents", "txn_count"])
            for r in monthly:
                w.writerow([r["year_month"], r["net_cents"], r["outflow_cents"], r["inflow_cents"], r["txn_count"]])

        console.print(f"[green]export complete[/green] wrote: transactions.csv, daily_rollup.csv, weekly_rollup.csv, monthly_rollup.csv -> {out}")

    finally:
        conn.close()

@app.command()
def subs(
    days: int = typer.Option(400, help="Lookback window in days."),
    top: int = typer.Option(25, help="How many items to show."),
):
    """
    Print subscription candidates sorted by estimated monthly cost.
    """
    from collections import defaultdict
    import statistics

    cfg = load_config()
    setup_logging(cfg)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    def parse_date(s: str):
        from datetime import datetime
        return datetime.fromisoformat(s).date()

    def median(xs):
        return statistics.median(xs) if xs else 0

    def mad(xs):
        if not xs:
            return 0
        m = median(xs)
        return median([abs(x - m) for x in xs])

    def cents_to_usd(c: int) -> str:
        return f"${c/100:,.2f}"

    try:
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """
            SELECT posted_at, amount_cents,
                   COALESCE(NULLIF(merchant,''), NULLIF(description,'')) AS label
            FROM transactions
            WHERE posted_at >= ?
              AND label IS NOT NULL
              AND label <> ''
            """,
            (since,),
        ).fetchall()

        hist = defaultdict(list)
        for r in rows:
            hist[str(r["label"])].append((parse_date(r["posted_at"]), int(r["amount_cents"])))

        subs = []
        for label, items in hist.items():
            if len(items) < 3:
                continue
            items.sort(key=lambda x: x[0])
            dates = [d for d, _ in items]
            amts = [a for _, a in items]
            deltas = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))]
            if not deltas:
                continue
            md = median(deltas)

            cadence = None
            tol = None
            if 5 <= md <= 9:
                cadence, tol = 7, 2
            elif 25 <= md <= 35:
                cadence, tol = 30, 5
            elif 330 <= md <= 400:
                cadence, tol = 365, 20
            else:
                continue

            fit = sum(1 for d in deltas if abs(d - cadence) <= tol) / max(1, len(deltas))

            amt_med = int(median([abs(a) for a in amts]))
            amt_mad = mad([abs(a) for a in amts])
            stable_band = max(200, int(abs(amt_med) * 0.05))
            stable = amt_mad <= stable_band
            if fit < 0.66 or not stable:
                continue

            if cadence == 365:
                monthly_est = int(round(amt_med / 12))
            elif cadence == 30:
                monthly_est = amt_med
            else:
                monthly_est = int(round(amt_med * 4.33))

            subs.append((monthly_est, label, cadence, amt_med, fit))

        subs.sort(reverse=True, key=lambda x: x[0])

        for monthly_est, label, cadence, amt_med, fit in subs[:top]:
            cadence_label = "weekly" if cadence == 7 else ("monthly" if cadence == 30 else "annual")
            console.print(f"• {label}  [dim]({cadence_label}, ~{cents_to_usd(amt_med)}, est/mo={cents_to_usd(monthly_est)}, fit={fit:.2f})[/dim]")

    finally:
        conn.close()

@app.command()
def health():
    cfg = load_config()
    setup_logging(cfg)

    ok = True
    if not cfg.simplefin_access_url:
        console.print("[red]missing SIMPLEFIN_ACCESS_URL[/red]")
        ok = False

    try:
        conn = dbmod.connect(cfg.db_path)
        dbmod.init_db(conn)
        conn.close()
        console.print("[green]db ok[/green]")
    except Exception as e:
        console.print(f"[red]db failed[/red] {type(e).__name__}")
        ok = False

    try:
        client = SimpleFinClient(cfg)
        raw = client.fetch_accounts()
        n = len(raw.get("accounts", []))
        client.close()
        console.print(f"[green]simplefin ok[/green] accounts={n}")
    except Exception as e:
        console.print(f"[red]simplefin failed[/red] {type(e).__name__}")
        ok = False

    raise typer.Exit(code=0 if ok else 1)



