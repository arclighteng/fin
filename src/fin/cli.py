import logging
from datetime import date, timedelta

import typer
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
JAN_ANNUAL_BOOTSTRAP_LOOKBACK = 400  # pragmatic: run in January for annual subs discovery


def _require_simplefin(cfg):
    if not getattr(cfg, "simplefin_access_url", "").strip():
        raise typer.BadParameter("Missing SIMPLEFIN_ACCESS_URL (use .env; do not commit).")


@app.command()
def sync(
    lookback_days: int = typer.Option(DEFAULT_LOOKBACK_DAYS, help="How many days to pull (default 120)."),
    annual_bootstrap: bool = typer.Option(False, help="Use ~400-day lookback for annual subscription discovery."),
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
        raw = client.fetch_accounts()
        raw_accounts = raw.get("accounts", [])

        accounts = []
        for ra in raw_accounts:
            accounts.append(
                Account(
                    account_id=str(ra.get("id")),
                    institution=str(ra.get("org", {}).get("name", "UNKNOWN")),
                    name=str(ra.get("name", "UNKNOWN")),
                    type=ra.get("type"),
                    currency=ra.get("currency", "USD"),
                )
            )
        dbmod.upsert_accounts(conn, accounts)

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

        console.print(
            f"[green]sync complete[/green] accounts={len(accounts)} fetched={fetched} inserted={inserted} updated={updated}"
        )
    finally:
        client.close()
        conn.close()


@app.command("db-info")
def db_info():
    cfg = load_config()
    setup_logging(cfg)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)
    try:
        ac = conn.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()["c"]
        tc = conn.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()["c"]
        row = conn.execute("SELECT MAX(posted_at) AS maxd, MIN(posted_at) AS mind FROM transactions").fetchone()
        console.print(f"accounts={ac} transactions={tc} range={row['mind']}..{row['maxd']}")
    finally:
        conn.close()


@app.command()
def health():
    cfg = load_config()
    setup_logging(cfg)

    ok = True
    if not getattr(cfg, "simplefin_access_url", "").strip():
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


@app.command()
def report(
    days: int = typer.Option(120, help="Lookback window in days for reporting."),
    top: int = typer.Option(15, help="How many items to show per section."),
):
    """
    Local report: subscriptions + basic anomalies (new merchants, spikes, duplicates).
    """
    from collections import defaultdict
    from datetime import datetime
    import statistics

    cfg = load_config()
    setup_logging(cfg)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    def parse_date(s: str) -> date:
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

        hist = defaultdict(list)  # label -> list[(date, amount)]
        by_day = defaultdict(list)  # (label, date, amount) -> count helper
        for r in rows:
            d = parse_date(r["posted_at"])
            a = int(r["amount_cents"])
            label = str(r["label"])
            hist[label].append((d, a))
            by_day[(label, d, a)].append(1)

        # --------------------
        # Subscriptions
        # --------------------
        subs = []
        for label, items in hist.items():
            if len(items) < 3:
                continue
            items.sort(key=lambda x: x[0])
            dates = [d for d, _ in items]
            amts = [a for _, a in items]

            deltas = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
            if not deltas:
                continue

            md = median(deltas)
            cadence = None
            tol = None
            if 5 <= md <= 9:
                cadence, tol = 7, 2
                cadence_label = "weekly"
            elif 25 <= md <= 35:
                cadence, tol = 30, 5
                cadence_label = "monthly"
            elif 330 <= md <= 400:
                cadence, tol = 365, 20
                cadence_label = "annual"
            else:
                continue

            fit = sum(1 for d in deltas if abs(d - cadence) <= tol) / max(1, len(deltas))

            abs_amts = [abs(a) for a in amts]
            amt_med = int(median(abs_amts))
            amt_mad = mad(abs_amts)
            stable_band = max(200, int(abs(amt_med) * 0.05))  # $2 or 5%
            stable = amt_mad <= stable_band

            if fit < 0.66 or not stable:
                continue

            last_seen = dates[-1]
            next_expected = last_seen + timedelta(days=cadence)

            if cadence == 365:
                monthly_est = int(round(amt_med / 12))
            elif cadence == 30:
                monthly_est = amt_med
            else:
                monthly_est = int(round(amt_med * 4.33))

            confidence = 0.0
            confidence += 0.5 * fit
            confidence += 0.3 if stable else 0.0
            confidence += 0.2 if (date.today() - last_seen).days <= (cadence + tol) else 0.0

            subs.append(
                {
                    "label": label,
                    "count": len(items),
                    "cadence": cadence_label,
                    "amount_abs": amt_med,
                    "confidence": confidence,
                    "last": last_seen,
                    "next": next_expected,
                    "monthly": monthly_est,
                }
            )

        subs.sort(key=lambda x: (x["confidence"], x["monthly"]), reverse=True)

        # --------------------
        # Anomalies
        # --------------------
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

        spikes = []
        for label, items in hist.items():
            if len(items) < 6:
                continue
            items.sort(key=lambda x: x[0])
            latest_d, latest_a = items[-1]
            amts = [abs(a) for _, a in items[:-1]]
            m = median(amts)
            if m == 0:
                continue
            if abs(latest_a) > max(int(m * 2.5), 10000):  # >2.5x and >$100
                spikes.append((label, latest_d, latest_a, int(m)))
        spikes.sort(key=lambda x: abs(x[2]), reverse=True)

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
                console.print(
                    f"  • {s['label']}  "
                    f"[dim]({s['cadence']}, ~{cents_to_usd(s['amount_abs'])}, conf={s['confidence']:.2f}, "
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


@app.command("subs-pick")
def subs_pick(
    label_contains: str = typer.Argument(..., help="Case-insensitive substring to match payee_norm (e.g. netflix)"),
    note: str = typer.Option("", help="Optional note like 'cancel', 'audit', 'confirm annual'."),
):
    """
    Add a subscription/watchlist entry locally (data-safe, no secrets).
    Writes to ./exports/watchlist.csv (host) via /app/exports in container.
    """
    import csv
    import os

    cfg = load_config()
    setup_logging(cfg)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    try:
        rows = conn.execute(
            """
            SELECT
              TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), ''))) AS payee_norm,
              COUNT(*) AS occurrences,
              CAST(AVG(ABS(amount_cents)) AS INTEGER) AS avg_abs_amount_cents,
              MIN(posted_at) AS first_seen,
              MAX(posted_at) AS last_seen
            FROM transactions
            WHERE amount_cents < 0
              AND payee_norm LIKE ?
              AND payee_norm <> ''
            GROUP BY payee_norm
            ORDER BY occurrences DESC, avg_abs_amount_cents DESC
            """,
            (f"%{label_contains.strip().lower()}%",),
        ).fetchall()

        if not rows:
            console.print("[yellow]No matches found.[/yellow]")
            raise typer.Exit(code=1)

        pick = rows[0]
        entry = {
            "payee_norm": pick["payee_norm"],
            "occurrences": pick["occurrences"],
            "avg_abs_amount_cents": pick["avg_abs_amount_cents"],
            "first_seen": pick["first_seen"],
            "last_seen": pick["last_seen"],
            "note": note,
        }

        out_dir = "/app/exports"
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "watchlist.csv")

        exists = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(entry.keys()))
            if not exists:
                w.writeheader()
            w.writerow(entry)

        console.print(f"[green]watchlisted[/green] {entry['payee_norm']} (occurrences={entry['occurrences']})")
    finally:
        conn.close()


@app.command("watchlist-show")
def watchlist_show():
    import csv
    import os

    path = "/app/exports/watchlist.csv"
    if not os.path.exists(path):
        console.print("[yellow]No watchlist.csv found yet.[/yellow]")
        raise typer.Exit(code=1)

    with open(path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        console.print("[yellow]Watchlist is empty.[/yellow]")
        return

    for r in rows:
        console.print(
            f"• {r.get('payee_norm','')}  "
            f"[dim](occ={r.get('occurrences','')}, avg_abs_cents={r.get('avg_abs_amount_cents','')}, "
            f"last={r.get('last_seen','')}, note={r.get('note','')})[/dim]"
        )


@app.command("watchlist-done")
def watchlist_done(
    label_contains: str = typer.Argument(..., help="Substring match for payee_norm to mark done"),
):
    import csv
    import os
    from datetime import datetime

    path = "/app/exports/watchlist.csv"
    if not os.path.exists(path):
        console.print("[yellow]No watchlist.csv found.[/yellow]")
        raise typer.Exit(code=1)

    with open(path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        console.print("[yellow]Watchlist is empty.[/yellow]")
        return

    needle = label_contains.strip().lower()
    changed = 0
    now = datetime.utcnow().isoformat()

    for r in rows:
        r.setdefault("status", "")
        r.setdefault("handled_at", "")

    for r in rows:
        if needle in (r.get("payee_norm", "").lower()):
            r["status"] = "done"
            r["handled_at"] = now
            changed += 1

    if changed == 0:
        console.print("[yellow]No matching watchlist entries.[/yellow]")
        raise typer.Exit(code=1)

    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    console.print(f"[green]marked done[/green] matches={changed}")


@app.command("export-csv")
def export_csv(
    out: str = typer.Option("/app/exports", help="Output directory for CSV exports (container path)."),
    days: int = typer.Option(400, help="Lookback window in days to export."),
):
    """
    Export enriched transactions + rollups + recurring candidates + actions table (Sheets friendly).
    """
    import csv
    import os
    from collections import defaultdict
    import statistics
    from pathlib import Path
    from datetime import datetime

    cfg = load_config()
    setup_logging(cfg)

    os.makedirs(out, exist_ok=True)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    def _parse(d: str):
        return datetime.fromisoformat(d).date()

    def median(xs):
        return statistics.median(xs) if xs else 0

    def mad(xs):
        if not xs:
            return 0
        m = median(xs)
        return median([abs(x - m) for x in xs])

    # pragmatic keyword excludes (payments/transfers/big financing words)
    def is_noise(label: str) -> bool:
        s = label
        return (
            "payment" in s
            or "bill pay" in s
            or "autopay" in s
            or "transfer" in s
            or "ach transfer" in s
            or "zelle" in s
            or "venmo" in s
            or "cash app" in s
            or "mortgage" in s
            or "loan" in s
            or "escrow" in s
            or "principal" in s
            or "interest" in s
            or "credit card" in s
            or "cc payment" in s
        )

    try:
        since = (date.today() - timedelta(days=days)).isoformat()

        # ---- transactions.csv (enriched)
        tx = conn.execute(
            """
            SELECT
              posted_at,
              account_id,
              amount_cents,
              currency,
              TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), ''))) AS payee_norm,
              COALESCE(NULLIF(merchant,''), '') AS merchant,
              COALESCE(NULLIF(description,''), '') AS description
            FROM transactions
            WHERE posted_at >= ?
            ORDER BY posted_at ASC
            """,
            (since,),
        ).fetchall()

        tx_path = os.path.join(out, "transactions.csv")
        with open(tx_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "posted_at",
                    "account_id",
                    "direction",
                    "amount_usd",
                    "abs_amount_usd",
                    "amount_cents",
                    "currency",
                    "payee_norm",
                    "merchant",
                    "description",
                ]
            )
            for r in tx:
                cents = int(r["amount_cents"])
                direction = "outflow" if cents < 0 else ("inflow" if cents > 0 else "zero")
                w.writerow(
                    [
                        r["posted_at"],
                        r["account_id"],
                        direction,
                        f"{cents/100:.2f}",
                        f"{abs(cents)/100:.2f}",
                        cents,
                        r["currency"],
                        r["payee_norm"],
                        r["merchant"],
                        r["description"],
                    ]
                )

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

        # ---- weekly rollup
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

        # ---- subscription_candidates.csv (strict recurring candidates; UI-friendly)
        subs_path = os.path.join(out, "subscription_candidates.csv")

        raw = conn.execute(
            """
            SELECT
              posted_at,
              ABS(amount_cents) AS abs_amount_cents,
              TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), ''))) AS payee_norm
            FROM transactions
            WHERE posted_at >= ?
              AND amount_cents < 0
              AND ABS(amount_cents) <= 30000
            ORDER BY posted_at ASC
            """,
            (since,),
        ).fetchall()

        hist = defaultdict(list)  # payee_norm -> list[(date, abs_amount_cents)]
        for r in raw:
            label = (r["payee_norm"] or "").strip()
            if not label or is_noise(label):
                continue
            d = _parse(r["posted_at"])
            a = int(r["abs_amount_cents"])
            hist[label].append((d, a))

        candidates = []
        for label, items in hist.items():
            if len(items) < 3:
                continue
            items.sort(key=lambda x: x[0])
            dates = [d for d, _ in items]
            amts = [a for _, a in items]

            deltas = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
            if not deltas:
                continue

            md = median(deltas)

            cadence = None
            tol = None
            step = None
            if 5 <= md <= 9:
                cadence, tol, step = "weekly-ish", 2, 7
            elif 25 <= md <= 35:
                cadence, tol, step = "monthly-ish", 5, 30
            elif 330 <= md <= 400:
                cadence, tol, step = "annual-ish", 20, 365
            else:
                continue  # keep only clean recurring patterns

            fit = sum(1 for d in deltas if abs(d - step) <= tol) / max(1, len(deltas))

            amt_med = int(median(amts))
            amt_mad = mad(amts)
            stable_band = max(200, int(amt_med * 0.05))
            stable = amt_mad <= stable_band

            if fit < 0.66 or not stable:
                continue

            last_seen = dates[-1]
            next_expected = (last_seen + timedelta(days=step)).isoformat()

            if step == 365:
                monthly_est = int(round(amt_med / 12))
            elif step == 30:
                monthly_est = amt_med
            else:
                monthly_est = int(round(amt_med * 4.33))

            confidence = 0.0
            confidence += 0.55 * fit
            confidence += 0.35 if stable else 0.0
            confidence += 0.10 if (date.today() - last_seen).days <= (step + tol) else 0.0

            candidates.append(
                {
                    "label": label,
                    "occurrences": len(items),
                    "cadence_guess": cadence,
                    "monthly_est_cents": monthly_est,
                    "amount_median_cents": amt_med,
                    "amount_mad_cents": int(amt_mad),
                    "avg_delta_days": int(round(sum(deltas) / len(deltas))),
                    "confidence": round(confidence, 3),
                    "first_seen": dates[0].isoformat(),
                    "last_seen": last_seen.isoformat(),
                    "next_expected": next_expected,
                }
            )

        candidates.sort(key=lambda x: (x["monthly_est_cents"], x["confidence"]), reverse=True)

        with open(subs_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "label",
                    "occurrences",
                    "cadence_guess",
                    "monthly_est_cents",
                    "amount_median_cents",
                    "amount_mad_cents",
                    "avg_delta_days",
                    "confidence",
                    "first_seen",
                    "last_seen",
                    "next_expected",
                ]
            )
            for c in candidates:
                w.writerow(
                    [
                        c["label"],
                        c["occurrences"],
                        c["cadence_guess"],
                        c["monthly_est_cents"],
                        c["amount_median_cents"],
                        c["amount_mad_cents"],
                        c["avg_delta_days"],
                        c["confidence"],
                        c["first_seen"],
                        c["last_seen"],
                        c["next_expected"],
                    ]
                )

        # ---- actions.csv (merge recurring candidates + watchlist status)
        watch_path = Path("/app/exports/watchlist.csv")
        watch = {}
        if watch_path.exists():
            with watch_path.open("r", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    watch[r.get("payee_norm", "")] = r

        actions_path = os.path.join(out, "actions.csv")
        with open(actions_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "payee_norm",
                    "monthly_est_cents",
                    "cadence_guess",
                    "occurrences",
                    "amount_median_cents",
                    "avg_delta_days",
                    "confidence",
                    "last_seen",
                    "next_expected",
                    "status",
                    "note",
                    "handled_at",
                ]
            )
            for c in candidates:
                wl = watch.get(c["label"], {})
                w.writerow(
                    [
                        c["label"],
                        c["monthly_est_cents"],
                        c["cadence_guess"],
                        c["occurrences"],
                        c["amount_median_cents"],
                        c["avg_delta_days"],
                        c["confidence"],
                        c["last_seen"],
                        c["next_expected"],
                        wl.get("status", ""),
                        wl.get("note", ""),
                        wl.get("handled_at", ""),
                    ]
                )

        console.print(
            f"[green]export complete[/green] wrote: transactions.csv, daily_rollup.csv, weekly_rollup.csv, "
            f"monthly_rollup.csv, subscription_candidates.csv, actions.csv -> {out}"
        )
    finally:
        conn.close()


@app.command()
def web(
    port: int = typer.Option(8000, help="Port to serve on (mapped from host)."),
):
    """Run the local web UI."""
    import uvicorn

    uvicorn.run("fin.web:app", host="0.0.0.0", port=port, reload=False)
