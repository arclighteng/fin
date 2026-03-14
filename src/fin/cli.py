import csv
import logging
import os
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from .status_commands import status_command, drill_command, trend_command

import filelock
import typer
from rich.console import Console

from . import db as dbmod
from . import dates as dates_mod
from .dates import TimePeriod
from .report_service import ReportService
from .view_models import PeriodViewModel, compute_period_trends
from .legacy_classify import detect_duplicates, detect_sketchy, get_subscriptions  # Detection utilities only
from .config import load_config
from .log import setup_logging
from .models import Account
from .normalize import normalize_simplefin_txn, sanitize_csv_field
from .simplefin_client import SimpleFinClient

app = typer.Typer(no_args_is_help=True)
app.command("status")(status_command)
app.command("drill")(drill_command)
app.command("trend")(trend_command)
console = Console()
log = logging.getLogger("fin")

# Sync lookback periods:
# - Quick (14 days): For daily syncs, catches new transactions and pending corrections
# - Default (30 days): Standard sync, covers statement cycle + correction window
# - Full (120 days): For catching up after extended absence or initial setup
# - Annual (400 days): For annual subscription discovery, run once in January
DEFAULT_LOOKBACK_DAYS = 30
FULL_LOOKBACK_DAYS = 120
JAN_ANNUAL_BOOTSTRAP_LOOKBACK = 400

# Watchlist lock path
WATCHLIST_LOCK_PATH = "/app/exports/.watchlist.csv.lock"
WATCHLIST_PATH = "/app/exports/watchlist.csv"


def _require_simplefin(cfg):
    if not getattr(cfg, "simplefin_access_url", "").strip():
        raise typer.BadParameter("Missing SIMPLEFIN_ACCESS_URL (use .env; do not commit).")


def _acquire_watchlist_lock(timeout: int = 10) -> filelock.FileLock:
    """Acquire exclusive lock for watchlist operations."""
    os.makedirs(os.path.dirname(WATCHLIST_LOCK_PATH), exist_ok=True)
    return filelock.FileLock(WATCHLIST_LOCK_PATH, timeout=timeout)


@app.command()
def setup(
    setup_token: str = typer.Argument(..., help="Base64 setup token from SimpleFIN Bridge"),
    store_in_keyring: bool = typer.Option(True, "--keyring/--no-keyring", help="Store in system keyring (recommended)"),
):
    """
    Exchange a SimpleFIN setup token for a permanent access URL.

    SimpleFIN uses a two-step authentication process:
    1. You get a Setup Token from SimpleFIN Bridge (base64 encoded)
    2. Run this command to exchange it for your Access URL

    By default, credentials are stored in your system keyring (secure).
    Use --no-keyring to just print the URL for manual .env setup.

    NOTE: Setup tokens can only be claimed once. If you've already claimed it,
    you'll need to generate a new one from SimpleFIN Bridge.
    """
    from .simplefin_client import claim_access_url
    from . import credentials

    console.print("[bold]Claiming SimpleFIN access...[/bold]")

    try:
        access_url = claim_access_url(setup_token)

        console.print()
        console.print("[green]Success![/green] Access URL claimed.")

        # Try to store in keyring if requested
        if store_in_keyring and credentials.is_keyring_available():
            if credentials.set_simplefin_url(access_url):
                console.print()
                console.print("[green]Credentials stored in system keyring.[/green]")
                console.print()
                console.print("You're all set! Run [cyan]fin sync[/cyan] to pull your transactions.")
                return

        # Fall back to showing URL for manual setup
        console.print()
        console.print("Your SimpleFIN Access URL:")
        console.print()
        console.print(f"[bold]{access_url}[/bold]")
        console.print()
        console.print("Add this to your .env file:")
        console.print(f"[cyan]SIMPLEFIN_ACCESS_URL={access_url}[/cyan]")
        console.print()
        console.print("[yellow]Keep this URL secret - it contains your credentials![/yellow]")

    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Failed to claim access URL:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Credentials management (keyring)
# ---------------------------------------------------------------------------
credentials_app = typer.Typer(help="Manage credentials in system keyring.")
app.add_typer(credentials_app, name="credentials")


@credentials_app.command("set")
def credentials_set(
    url: str = typer.Option(None, "--url", "-u", help="SimpleFIN Access URL (or prompted if not provided)"),
):
    """
    Store SimpleFIN credentials in system keyring.

    Uses the OS secure credential store:
    - Windows: Credential Manager
    - macOS: Keychain
    - Linux: Secret Service (GNOME Keyring, KWallet)

    Example:
        fin credentials set
        fin credentials set --url "https://user:pass@..."
    """
    from . import credentials

    if not credentials.is_keyring_available():
        console.print("[red]Error:[/red] System keyring not available.")
        console.print()
        console.print("On Linux, ensure you have a secrets service running:")
        console.print("  - GNOME: gnome-keyring")
        console.print("  - KDE: KWallet")
        console.print("  - Or install: [cyan]apt install gnome-keyring[/cyan]")
        raise typer.Exit(1)

    # Prompt for URL if not provided
    if not url:
        url = typer.prompt("SimpleFIN Access URL", hide_input=True)

    url = url.strip()
    if not url:
        console.print("[red]Error:[/red] URL cannot be empty.")
        raise typer.Exit(1)

    if not url.startswith("http"):
        console.print("[red]Error:[/red] URL must start with http:// or https://")
        raise typer.Exit(1)

    if credentials.set_simplefin_url(url):
        console.print("[green]Credentials stored in system keyring.[/green]")
        console.print()
        console.print("[dim]You can now remove SIMPLEFIN_ACCESS_URL from your .env file.[/dim]")
    else:
        console.print("[red]Failed to store credentials in keyring.[/red]")
        raise typer.Exit(1)


@credentials_app.command("clear")
def credentials_clear():
    """
    Remove SimpleFIN credentials from system keyring.
    """
    from . import credentials

    if not credentials.is_keyring_available():
        console.print("[yellow]System keyring not available.[/yellow]")
        raise typer.Exit(0)

    if credentials.clear_simplefin_url():
        console.print("[green]Credentials removed from keyring.[/green]")
    else:
        console.print("[red]Failed to remove credentials.[/red]")
        raise typer.Exit(1)


@credentials_app.command("status")
def credentials_status():
    """
    Show where credentials are being loaded from.
    """
    from . import credentials

    source = credentials.get_credential_source()
    keyring_available = credentials.is_keyring_available()

    console.print("[bold]Credential Status[/bold]")
    console.print()

    if keyring_available:
        console.print(f"  Keyring: [green]Available[/green]")
    else:
        console.print(f"  Keyring: [yellow]Not available[/yellow]")

    console.print()
    if source == "keyring":
        console.print(f"  SimpleFIN URL: [green]Stored in keyring[/green] (most secure)")
    elif source == "env":
        console.print(f"  SimpleFIN URL: [yellow]Loaded from .env file[/yellow]")
        if keyring_available:
            console.print()
            console.print("  [dim]Tip: Run 'fin credentials set' to move to keyring[/dim]")
    else:
        console.print(f"  SimpleFIN URL: [red]Not configured[/red]")
        console.print()
        console.print("  Configure with:")
        if keyring_available:
            console.print("    [cyan]fin credentials set[/cyan]  (recommended)")
        console.print("    Or add SIMPLEFIN_ACCESS_URL to .env file")


@app.command()
def sync(
    lookback_days: int = typer.Option(DEFAULT_LOOKBACK_DAYS, help="Days to pull (default 30, use --full for 120)."),
    quick: bool = typer.Option(False, help="Quick sync: 14 days (daily use)."),
    full: bool = typer.Option(False, help="Full sync: 120 days (catch up after absence)."),
    annual_bootstrap: bool = typer.Option(False, help="Annual sync: 400 days (yearly subscription discovery)."),
):
    """
    Pull accounts + transactions from SimpleFIN and sync to local database.

    Recommended usage:
    - Daily: fin sync --quick (14 days)
    - Weekly: fin sync (30 days, default)
    - After absence: fin sync --full (120 days)
    - January: fin sync --annual-bootstrap (400 days for annual subs)
    """
    cfg = load_config()
    setup_logging(cfg)
    _require_simplefin(cfg)

    # Determine effective lookback
    if annual_bootstrap:
        effective_lookback = JAN_ANNUAL_BOOTSTRAP_LOOKBACK
    elif full:
        effective_lookback = FULL_LOOKBACK_DAYS
    elif quick:
        effective_lookback = 14
    else:
        effective_lookback = lookback_days
    start = dates_mod.today() - timedelta(days=effective_lookback)
    end_exclusive = dates_mod.today() + timedelta(days=1)

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
        since = (dates_mod.today() - timedelta(days=days)).isoformat()

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
            confidence += 0.2 if (dates_mod.today() - last_seen).days <= (cadence + tol) else 0.0

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
        today = dates_mod.today()
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
                    f"  * {s['label']}  "
                    f"[dim]({s['cadence']}, ~{cents_to_usd(s['amount_abs'])}, conf={s['confidence']:.2f}, "
                    f"last={s['last']}, next~{s['next']}, est/mo={cents_to_usd(s['monthly'])})[/dim]"
                )

        console.print("")
        console.print("[bold yellow]Anomalies[/bold yellow]")

        console.print("  [bold]New merchants (last 14d, not seen prior)[/bold]")
        if not new_merchants:
            console.print("    (none)")
        else:
            for m in new_merchants[:top]:
                console.print(f"    * {m}")

        console.print("  [bold]Amount spikes (latest vs baseline)[/bold]")
        if not spikes:
            console.print("    (none)")
        else:
            for label, d, a, med in spikes[:top]:
                console.print(f"    * {label} on {d}: {cents_to_usd(a)} vs baseline {cents_to_usd(med)}")

        console.print("  [bold]Duplicate-like[/bold]")
        if not dups:
            console.print("    (none)")
        else:
            for label, d, a, n in dups[:top]:
                console.print(f"    * {label} on {d}: {cents_to_usd(a)} x {n}")

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
            "payee_norm": sanitize_csv_field(pick["payee_norm"]),
            "occurrences": pick["occurrences"],
            "avg_abs_amount_cents": pick["avg_abs_amount_cents"],
            "first_seen": pick["first_seen"],
            "last_seen": pick["last_seen"],
            "note": sanitize_csv_field(note),
        }

        out_dir = "/app/exports"
        os.makedirs(out_dir, exist_ok=True)
        path = WATCHLIST_PATH

        lock = _acquire_watchlist_lock()
        try:
            with lock:
                exists = os.path.exists(path)
                with open(path, "a", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=list(entry.keys()))
                    if not exists:
                        w.writeheader()
                    w.writerow(entry)
        except filelock.Timeout:
            console.print("[red]Could not acquire lock on watchlist.csv (another process may be writing)[/red]")
            raise typer.Exit(code=1)

        console.print(f"[green]watchlisted[/green] {entry['payee_norm']} (occurrences={entry['occurrences']})")
    finally:
        conn.close()


@app.command("watchlist-show")
def watchlist_show():
    path = WATCHLIST_PATH
    if not os.path.exists(path):
        console.print("[yellow]No watchlist.csv found yet.[yellow]")
        raise typer.Exit(code=1)

    with open(path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        console.print("[yellow]Watchlist is empty.[/yellow]")
        return

    for r in rows:
        console.print(
            f"* {r.get('payee_norm','')}  "
            f"[dim](occ={r.get('occurrences','')}, avg_abs_cents={r.get('avg_abs_amount_cents','')}, "
            f"last={r.get('last_seen','')}, note={r.get('note','')})[/dim]"
        )


@app.command("watchlist-done")
def watchlist_done(
    label_contains: str = typer.Argument(..., help="Substring match for payee_norm to mark done"),
):
    path = WATCHLIST_PATH
    if not os.path.exists(path):
        console.print("[yellow]No watchlist.csv found.[/yellow]")
        raise typer.Exit(code=1)

    lock = _acquire_watchlist_lock()
    try:
        with lock:
            with open(path, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

            if not rows:
                console.print("[yellow]Watchlist is empty.[/yellow]")
                return

            needle = label_contains.strip().lower()
            changed = 0
            now = datetime.now(timezone.utc).isoformat()

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

    except filelock.Timeout:
        console.print("[red]Could not acquire lock on watchlist.csv (another process may be writing)[/red]")
        raise typer.Exit(code=1)

    console.print(f"[green]marked done[/green] matches={changed}")


@app.command("audit-subs")
def audit_subs(
    days: int = typer.Option(400, help="Lookback window in days."),
    show_all: bool = typer.Option(False, "--all", "-a", help="Show all detected, not just known services."),
):
    """
    Audit detected subscriptions - verify pattern matching.

    Shows which merchants are being detected as subscriptions and why.
    Use this to verify that the known subscription patterns are working
    correctly and not producing false positives.

    By default, shows only KNOWN service matches (Netflix, Spotify, etc.).
    Use --all to see all detected subscriptions including frequency-based.
    """
    from rich.table import Table

    cfg = load_config()
    setup_logging(cfg)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    try:
        subs = get_subscriptions(conn, days=days)

        # Filter to known services unless --all
        if not show_all:
            subs = [s for s in subs if s[7]]  # index 7 = is_known_service

        if not subs:
            if show_all:
                console.print("[dim]No subscriptions detected.[/dim]")
            else:
                console.print("[dim]No known service matches found.[/dim]")
                console.print("[dim]Use --all to see all detected subscriptions.[/dim]")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("Merchant (normalized)", style="dim")
        table.add_column("Display Name")
        table.add_column("Known?", justify="center")
        table.add_column("Cadence")
        table.add_column("Amount", justify="right")
        table.add_column("Type")

        # Sort: known services first, then by merchant name
        subs_sorted = sorted(subs, key=lambda x: (not x[7], x[0].lower()))

        for sub in subs_sorted:
            merchant = sub[0]
            monthly_cents = sub[1]
            cadence = sub[2]
            is_known = sub[7]
            display_name = sub[8] or merchant
            txn_type = sub[6]

            known_marker = "[green]Yes[/green]" if is_known else "[dim]No[/dim]"
            amount = f"${abs(monthly_cents) / 100:.2f}/mo"

            table.add_row(
                merchant[:30],  # truncate long names
                display_name,
                known_marker,
                cadence,
                amount,
                txn_type,
            )

        console.print()
        console.print(f"[bold]Subscription Audit[/bold] (last {days} days)")
        console.print()
        console.print(table)
        console.print()

        known_count = sum(1 for s in subs if s[7])
        pattern_count = len(subs) - known_count
        console.print(f"[dim]Known services: {known_count}, Pattern-detected: {pattern_count}[/dim]")

    finally:
        conn.close()


@app.command("export-csv")
def export_csv(
    out: str = typer.Option("/app/exports", help="Output directory for CSV exports (container path)."),
    days: int = typer.Option(400, help="Lookback window in days to export."),
):
    """
    Export enriched transactions + rollups + recurring candidates + actions table (Sheets friendly).
    """
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

    def classify_family(label: str) -> tuple[str, str]:
        s = (label or "").lower()

        # Amazon
        if "amazon" in s or "prime" in s:
            if "tip" in s:
                return ("amazon", "tip")
            if "prime video" in s:
                return ("amazon", "prime_video")
            if "prime" in s:
                return ("amazon", "prime")
            return ("amazon", "amazon_misc")

        # Google / YouTube
        if "youtube" in s or "google" in s:
            if "fiber" in s:
                return ("google", "fiber")
            if "premium" in s:
                return ("google", "youtube_premium")
            if "member" in s or "membership" in s:
                return ("google", "youtube_membership")
            if "youtube" in s:
                return ("google", "youtube_misc")
            return ("google", "google_misc")

        # Disney bundle
        if "disney" in s or "hulu" in s or "espn" in s:
            if "disney" in s and "hulu" in s:
                return ("disney_bundle", "bundle")
            if "disney" in s:
                return ("disney_bundle", "disney")
            if "hulu" in s:
                return ("disney_bundle", "hulu")
            return ("disney_bundle", "espn")

        return ("other", "other")

    try:
        since = (dates_mod.today() - timedelta(days=days)).isoformat()

        # ---- transactions.csv (enriched + family/subtype)
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
                    "family",
                    "subtype",
                    "merchant",
                    "description",
                ]
            )
            for r in tx:
                cents = int(r["amount_cents"])
                direction = "outflow" if cents < 0 else ("inflow" if cents > 0 else "zero")
                family, subtype = classify_family(r["payee_norm"])
                w.writerow(
                    [
                        r["posted_at"],
                        r["account_id"],
                        direction,
                        f"{cents/100:.2f}",
                        f"{abs(cents)/100:.2f}",
                        cents,
                        r["currency"],
                        sanitize_csv_field(r["payee_norm"]),
                        family,
                        subtype,
                        sanitize_csv_field(r["merchant"]),
                        sanitize_csv_field(r["description"]),
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
            confidence += 0.10 if (dates_mod.today() - last_seen).days <= (step + tol) else 0.0

            family, subtype = classify_family(label)

            candidates.append(
                {
                    "label": label,
                    "family": family,
                    "subtype": subtype,
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
                    "family",
                    "subtype",
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
                        sanitize_csv_field(c["label"]),
                        c["family"],
                        c["subtype"],
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
                    "family",
                    "subtype",
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
                        sanitize_csv_field(c["label"]),
                        c["family"],
                        c["subtype"],
                        c["monthly_est_cents"],
                        c["cadence_guess"],
                        c["occurrences"],
                        c["amount_median_cents"],
                        c["avg_delta_days"],
                        c["confidence"],
                        c["last_seen"],
                        c["next_expected"],
                        wl.get("status", ""),
                        sanitize_csv_field(wl.get("note", "")),
                        wl.get("handled_at", ""),
                    ]
                )

        # ---- sketchy_charges.csv (new RocketMoney-like alerts)
        sketchy_path = os.path.join(out, "sketchy_charges.csv")
        alerts = detect_sketchy(conn, days=60)
        with open(sketchy_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["posted_at", "merchant", "amount_usd", "pattern_type", "severity", "detail"])
            for alert in alerts:
                w.writerow([
                    alert.posted_at.isoformat(),
                    sanitize_csv_field(alert.merchant_norm),
                    f"{alert.amount_cents / 100:.2f}",
                    alert.pattern_type,
                    alert.severity,
                    sanitize_csv_field(alert.detail),
                ])

        # ---- duplicates.csv (duplicate subscription groups)
        duplicates_path = os.path.join(out, "duplicates.csv")
        duplicates = detect_duplicates(conn, days=days)
        with open(duplicates_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["group_type", "merchants", "monthly_total_usd", "severity", "detail"])
            for dup in duplicates:
                w.writerow([
                    dup.group_type,
                    sanitize_csv_field("; ".join(dup.merchants)),
                    f"{dup.total_monthly_cents / 100:.2f}",
                    dup.severity,
                    sanitize_csv_field(dup.detail),
                ])

        # ---- monthly_summary.csv (income vs spend with rolling averages)
        # Using canonical ReportService for all totals
        summary_path = os.path.join(out, "monthly_summary.csv")
        service = ReportService(conn)
        reports = service.report_periods(TimePeriod.MONTH, num_periods=12)
        periods = compute_period_trends(reports, avg_window=3)
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "period", "start_date", "end_date",
                "income_usd", "recurring_usd", "discretionary_usd", "net_usd",
                "avg_income_usd", "avg_recurring_usd", "avg_discretionary_usd",
                "income_trend", "recurring_trend", "discretionary_trend",
                "transaction_count",
            ])
            for p in periods:
                w.writerow([
                    p.period_label,
                    p.start_date.isoformat(),
                    p.end_date.isoformat(),
                    f"{p.income_cents / 100:.2f}",
                    f"{p.recurring_cents / 100:.2f}",
                    f"{p.discretionary_cents / 100:.2f}",
                    f"{p.net_cents / 100:.2f}",
                    f"{p.avg_income_cents / 100:.2f}",
                    f"{p.avg_recurring_cents / 100:.2f}",
                    f"{p.avg_discretionary_cents / 100:.2f}",
                    p.income_trend,
                    p.recurring_trend,
                    p.discretionary_trend,
                    p.transaction_count,
                ])

        console.print(
            f"[green]export complete[/green] wrote: transactions.csv, daily_rollup.csv, weekly_rollup.csv, "
            f"monthly_rollup.csv, subscription_candidates.csv, actions.csv, sketchy_charges.csv, "
            f"duplicates.csv, monthly_summary.csv -> {out}"
        )
    finally:
        conn.close()


@app.command("bundle-check")
def bundle_check(
    days: int = typer.Option(400, help="Lookback window in days."),
    window_days: int = typer.Option(3, help="Charges within this many days count as 'nearby'."),
):
    """
    Heuristic: flag possible duplicate subscriptions / bundles.
    Looks for:
      1) same vendor family keywords (disney/hulu/espn, apple, amazon, google, microsoft, etc.)
      2) multiple recurring candidates in same family
      3) charges occurring on nearby dates (suggests separate subs)
    """
    cfg = load_config()
    setup_logging(cfg)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    def parse_date(s: str):
        return datetime.fromisoformat(s).date()

    families = {
        "disney_bundle": ["disney", "hulu", "espn"],
        "apple": ["apple", "icloud", "app store"],
        "amazon": ["amazon", "prime"],
        "google": ["google", "youtube"],
        "microsoft": ["microsoft", "xbox", "office"],
        "netflix": ["netflix"],
        "spotify": ["spotify"],
    }

    try:
        since = (dates_mod.today() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """
            SELECT
              posted_at,
              ABS(amount_cents) AS abs_amount_cents,
              TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), ''))) AS payee_norm
            FROM transactions
            WHERE posted_at >= ?
              AND amount_cents < 0
              AND payee_norm <> ''
            ORDER BY posted_at ASC
            """,
            (since,),
        ).fetchall()

        fam_tx = defaultdict(list)  # fam -> list[(date, payee_norm, cents)]
        for r in rows:
            label = r["payee_norm"]
            d = parse_date(r["posted_at"])
            cents = int(r["abs_amount_cents"])

            matched = None
            for fam, keys in families.items():
                if any(k in label for k in keys):
                    matched = fam
                    break
            if matched:
                fam_tx[matched].append((d, label, cents))

        flagged = []
        for fam, items in fam_tx.items():
            if len(items) < 2:
                continue
            items.sort(key=lambda x: x[0])

            labels = sorted(set(l for _, l, _ in items))
            if len(labels) < 2:
                continue

            nearby = 0
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    di, li, _ = items[i]
                    dj, lj, _ = items[j]
                    if (dj - di).days > window_days:
                        break
                    if li != lj:
                        nearby += 1

            flagged.append((fam, len(labels), len(items), nearby, labels))

        if not flagged:
            console.print("[green]No bundle/duplicate signals found with current heuristics.[/green]")
            return

        flagged.sort(key=lambda x: (x[3], x[1], x[2]), reverse=True)

        console.print("[bold]Bundle / duplicate signals[/bold]")
        for fam, uniq_labels, total, nearby, labels in flagged:
            console.print(f"* {fam}  [dim](labels={uniq_labels}, charges={total}, nearby_pairs={nearby})[/dim]")
            for l in labels[:10]:
                console.print(f"   - {l}")

        # --- Export: actionable, per-label bundle rows (Sheets-friendly) ---
        # Responsible reporting: aggregated label stats only (no raw memos / per-tx detail).
        export_dir = Path("./exports")
        export_dir.mkdir(parents=True, exist_ok=True)
        out_path = export_dir / "bundle_items.csv"

        def _usd(cents: int) -> str:
            return f"{cents/100:.2f}"

        def _median_interval_days(dates):
            if len(dates) < 2:
                return ""
            ds = sorted(dates)
            intervals = [(ds[i] - ds[i - 1]).days for i in range(1, len(ds))]
            intervals = [x for x in intervals if x > 0]
            if not intervals:
                return ""
            return int(statistics.median(intervals))

        def _typical_bill_day(dates):
            if not dates:
                return ""
            days = [d.day for d in dates]
            try:
                return int(statistics.median(days))
            except statistics.StatisticsError:
                return ""

        with out_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "family",
                    "label",
                    "charge_count",
                    "first_date",
                    "last_date",
                    "median_amount_usd",
                    "min_amount_usd",
                    "max_amount_usd",
                    "median_interval_days",
                    "typical_bill_day",
                    "suggested_role",
                    "suggested_action",
                ],
            )
            w.writeheader()

            for fam, items in fam_tx.items():
                if len(items) < 2:
                    continue

                by_label = {}
                for d, label, cents in items:
                    by_label.setdefault(label, []).append((d, cents))

                if len(by_label) < 2:
                    continue

                # Primary candidate = most occurrences (ties broken by most recent)
                ranked = sorted(
                    by_label.items(),
                    key=lambda kv: (len(kv[1]), max(x[0] for x in kv[1])),
                    reverse=True,
                )
                primary_label = ranked[0][0]

                for label, entries in ranked:
                    dates = [d for d, _ in entries]
                    cents_list = [c for _, c in entries]
                    cents_sorted = sorted(cents_list)
                    med_cents = int(statistics.median(cents_sorted)) if cents_sorted else 0

                    role = "primary_candidate" if label == primary_label else "component_candidate"
                    action = (
                        "Verify this is the bundle/master subscription; confirm other family charges are included; keep if correct."
                        if role == "primary_candidate"
                        else "Check if this is included in the bundle/master; cancel or downgrade if redundant."
                    )

                    w.writerow(
                        {
                            "family": fam,
                            "label": label,
                            "charge_count": len(entries),
                            "first_date": str(min(dates)),
                            "last_date": str(max(dates)),
                            "median_amount_usd": _usd(med_cents),
                            "min_amount_usd": _usd(min(cents_list)) if cents_list else "",
                            "max_amount_usd": _usd(max(cents_list)) if cents_list else "",
                            "median_interval_days": _median_interval_days(dates),
                            "typical_bill_day": _typical_bill_day(dates),
                            "suggested_role": role,
                            "suggested_action": action,
                        }
                    )

        console.print(f"[dim]Wrote: {out_path}[/dim]")

    finally:
        conn.close()


# ---------------------------
# Monthly Survival (budgeting)
# ---------------------------


def _parse_month(month: str):
    """Return (start_date_iso, end_date_iso) for YYYY-MM."""
    # Validate format
    try:
        dt = datetime.strptime(month, "%Y-%m")
    except ValueError as e:
        raise typer.BadParameter("month must be YYYY-MM") from e
    start = date(dt.year, dt.month, 1)
    # compute first day of next month
    if dt.month == 12:
        end = date(dt.year + 1, 1, 1)
    else:
        end = date(dt.year, dt.month + 1, 1)
    return start.isoformat(), end.isoformat()


def _payee_norm(label: str) -> str:
    return (label or "").strip().lower()


def _usd(cents: int) -> float:
    return round(float(cents) / 100.0, 2)


def _load_income_rules():
    """
    Load income rules from /app/data/income_sources.csv if present.

    CSV format:
      pattern,name
    where pattern is a case-insensitive substring to match against payee_norm.
    """
    p = Path("/app/data/income_sources.csv")
    rules = []
    if not p.exists():
        return rules
    try:
        with p.open("r", newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                pat = (row.get("pattern") or "").strip().lower()
                name = (row.get("name") or "").strip()
                if pat:
                    rules.append((pat, name or pat))
    except Exception as e:
        log.warning(f"Failed to load income rules: {type(e).__name__}: {e}")
        return rules
    return rules


def _classify_income(label_norm: str, rules):
    for pat, name in rules:
        if pat in label_norm:
            return name
    return ""


def _sha1_rows(rows):
    import hashlib

    h = hashlib.sha1()
    for r in rows:
        # stable ordering assumed
        s = f"{r['posted_at']}|{r['amount_cents']}|{(r['fingerprint'] if 'fingerprint' in r.keys() else '')}"
        h.update(s.encode("utf-8", errors="ignore"))
        h.update(b"\n")
    return h.hexdigest()


@app.command("month-close")
def month_close(
    month: str = typer.Option(..., help="Month to close in YYYY-MM (e.g. 2026-01)."),
):
    """
    Close a month: compute totals + data quality signals and write a canonical month_close CSV
    plus state file under /app/data. This is the "source of truth" snapshot.
    """
    import json

    cfg = load_config()
    setup_logging(cfg)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    start_iso, end_iso = _parse_month(month)

    try:
        rows = conn.execute(
            """
            SELECT posted_at, amount_cents, fingerprint,
                   COALESCE(NULLIF(merchant,''), NULLIF(description,'')) AS label
            FROM transactions
            WHERE posted_at >= ?
              AND posted_at < ?
            ORDER BY posted_at ASC
            """,
            (start_iso, end_iso),
        ).fetchall()

        income_cents = sum(int(r["amount_cents"]) for r in rows if int(r["amount_cents"]) > 0)
        spend_cents = sum(abs(int(r["amount_cents"])) for r in rows if int(r["amount_cents"]) < 0)
        net_cents = income_cents - spend_cents

        # Coverage signals
        days = sorted({r["posted_at"] for r in rows})
        coverage_days = len(days)

        # longest gap between transaction days (rough)
        def _d(s):
            return datetime.fromisoformat(s).date()

        gaps = []
        if days:
            ds = [_d(d) for d in days]
            for i in range(1, len(ds)):
                gaps.append((ds[i] - ds[i - 1]).days)
        max_gap_days = max(gaps) if gaps else 0

        # fingerprint duplicates (possible double imports)
        fp = [r["fingerprint"] for r in rows if ("fingerprint" in r.keys() and r["fingerprint"])]
        c = Counter(fp)
        dup_fp_kinds = sum(1 for k, v in c.items() if v > 1)
        dup_fp_extra_rows = sum((v - 1) for v in c.values() if v > 1)

        checksum = _sha1_rows(rows)

        # Write month_close CSV
        out_dir = Path("/app/exports")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"month_close_{month}.csv"

        fieldnames = ["month", "metric", "value", "notes", "source"]
        out_rows = [
            {"month": month, "metric": "income_total", "value": _usd(income_cents), "notes": "", "source": "transactions"},
            {"month": month, "metric": "spend_total", "value": _usd(spend_cents), "notes": "", "source": "transactions"},
            {"month": month, "metric": "net", "value": _usd(net_cents), "notes": "", "source": "computed"},
            {"month": month, "metric": "txn_count", "value": len(rows), "notes": "", "source": "transactions"},
            {"month": month, "metric": "coverage_days", "value": coverage_days, "notes": "", "source": "computed"},
            {"month": month, "metric": "max_gap_days", "value": max_gap_days, "notes": "largest gap between transaction days", "source": "computed"},
            {"month": month, "metric": "dup_fingerprint_kinds", "value": dup_fp_kinds, "notes": "fingerprints seen >1x", "source": "computed"},
            {"month": month, "metric": "dup_fingerprint_extra_rows", "value": dup_fp_extra_rows, "notes": "rows beyond first for dup fingerprints", "source": "computed"},
            {"month": month, "metric": "checksum_sha1", "value": checksum, "notes": "", "source": "computed"},
        ]

        with out_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(out_rows)

        # State file (for reproducibility)
        state_path = Path("/app/data/month_state.json")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        state.setdefault("closed_months", {})
        state["last_closed_month"] = month
        state["closed_months"][month] = {
            "closed_at": dates_mod.today().isoformat(),
            "start": start_iso,
            "end": end_iso,
            "txn_count": len(rows),
            "income_total": _usd(income_cents),
            "spend_total": _usd(spend_cents),
            "net": _usd(net_cents),
            "coverage_days": coverage_days,
            "max_gap_days": max_gap_days,
            "dup_fingerprint_kinds": dup_fp_kinds,
            "dup_fingerprint_extra_rows": dup_fp_extra_rows,
            "checksum_sha1": checksum,
        }
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

        console.print(f"[green]Closed month {month}[/green]")
        console.print(f"[dim]Wrote: {out_path}[/dim]")
        console.print(f"[dim]Wrote: {state_path}[/dim]")

    finally:
        conn.close()


@app.command("month-report")
def month_report(
    month: str = typer.Option(..., help="Month to report in YYYY-MM (e.g. 2026-01)."),
    top: int = typer.Option(15, help="Top vendors / sources to include."),
):
    """
    Monthly Survival report: income, spend, net, and a ranked cut plan from exports/report.csv.

    Output: /app/exports/month_report_YYYY-MM.csv
    Columns: month,section,rank,label,amount_usd,confidence,notes,source
    """
    cfg = load_config()
    setup_logging(cfg)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    start_iso, end_iso = _parse_month(month)
    rules = _load_income_rules()

    out_dir = Path("/app/exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"month_report_{month}.csv"

    fieldnames = ["month", "section", "rank", "label", "amount_usd", "confidence", "notes", "source"]
    rows_out = []

    def wr(section: str, rank: int, label: str, amount_usd, confidence="", notes="", source=""):
        rows_out.append(
            {
                "month": month,
                "section": section,
                "rank": rank,
                "label": label,
                "amount_usd": amount_usd,
                "confidence": confidence,
                "notes": notes,
                "source": source,
            }
        )

    try:
        tx = conn.execute(
            """
            SELECT posted_at, amount_cents,
                   COALESCE(NULLIF(merchant,''), NULLIF(description,'')) AS label
            FROM transactions
            WHERE posted_at >= ?
              AND posted_at < ?
              AND label IS NOT NULL
            """,
            (start_iso, end_iso),
        ).fetchall()

        # Income/spend
        income = []
        spend = []
        for r in tx:
            cents = int(r["amount_cents"])
            label = r["label"] or ""
            ln = _payee_norm(label)
            if cents > 0:
                src = _classify_income(ln, rules)
                income.append((label, cents, src))
            elif cents < 0:
                spend.append((label, abs(cents)))

        income_total = sum(c for _, c, _ in income)
        spend_total = sum(c for _, c in spend)
        net = income_total - spend_total

        matched_income = sum(c for _, c, src in income if src)
        income_conf = "low"
        if income_total > 0:
            ratio = matched_income / income_total
            if rules and ratio >= 0.80:
                income_conf = "high"
            elif matched_income > 0:
                income_conf = "medium"
            else:
                income_conf = "low"

        wr("summary", 0, "income_total", _usd(income_total), income_conf, "", "transactions")
        wr("summary", 1, "spend_total", _usd(spend_total), "", "", "transactions")
        wr("summary", 2, "net", _usd(net), "", "", "computed")

        required_cuts = max(0.0, round((-net) / 100.0, 2)) if net < 0 else 0.0
        wr("survival", 0, "required_cuts_to_break_even", required_cuts, "", "", "computed")

        # Cut plan from exports/report.csv (if exists)
        cut_plan_savings = 0.0
        report_path = Path("/app/exports/report.csv")
        cut_candidates = []
        if report_path.exists():
            try:
                with report_path.open("r", newline="", encoding="utf-8") as f:
                    rr = csv.DictReader(f)
                    for row in rr:
                        section = (row.get("section") or "").strip()
                        if section not in ("next_actions", "subscription_candidate", "bundle_component", "anomaly_price_increase"):
                            continue
                        # respect watch directives
                        ws = (row.get("watch_status") or "").strip().lower()
                        if ws in ("keep", "ignore", "done"):
                            continue
                        try:
                            impact = float(row.get("cancel_impact_usd") or row.get("monthly_est_usd") or 0.0)
                        except Exception:
                            impact = 0.0
                        if impact < 5:
                            continue
                        ds = (row.get("decision_suggestion") or "").strip().upper() or "REVIEW"
                        label = row.get("label") or ""
                        notes = ds
                        sa = (row.get("suggested_action") or "").strip()
                        if sa:
                            notes = f"{ds} | {sa}"
                        cut_candidates.append((impact, ds, label, notes))
            except Exception as e:
                cut_candidates = []
                wr("survival", 1, "cut_plan_savings", 0, "", f"failed to read report.csv: {type(e).__name__}", "report.csv")
        else:
            wr("survival", 1, "cut_plan_savings", 0, "", "missing exports/report.csv (run `fin report` first)", "report.csv")

        if cut_candidates:
            # rank and write top cuts
            cut_candidates.sort(key=lambda x: x[0], reverse=True)
            top_cuts = cut_candidates[:top]
            cut_plan_savings = round(sum(x[0] for x in top_cuts), 2)
            wr("survival", 1, "cut_plan_savings", cut_plan_savings, "", f"from top {len(top_cuts)} cut candidates", "report.csv")
            net_after = round(_usd(net) + cut_plan_savings, 2)
            wr("survival", 2, "net_after_cut_plan", net_after, "", "", "computed")

            for i, (impact, ds, label, notes) in enumerate(top_cuts):
                wr("cut_plan", i, label, round(impact, 2), "", notes, "report.csv")
        else:
            # no candidates
            if report_path.exists():
                wr("survival", 1, "cut_plan_savings", 0, "", "no eligible cut candidates (check watchlist directives or impact thresholds)", "report.csv")
            wr("survival", 2, "net_after_cut_plan", _usd(net), "", "", "computed")

        # Top spend vendors
        spend_by = defaultdict(int)
        spend_count = Counter()
        spend_rep = {}

        for label, cents in spend:
            n = _payee_norm(label)
            spend_by[n] += int(cents)
            spend_count[n] += 1
            if n not in spend_rep:
                spend_rep[n] = (label or n).strip()

        top_spend = sorted(spend_by.items(), key=lambda x: x[1], reverse=True)[:top]
        for i, (n, cents) in enumerate(top_spend):
            rep = spend_rep.get(n, n)
            wr("top_spend_vendors", i, rep, _usd(cents), "", f"occurrences={spend_count[n]}", "transactions")

        # Income sources
        inc_by = defaultdict(int)
        inc_count = Counter()
        inc_rep = {}
        inc_kind = {}  # key -> "rule" or "heuristic"

        for label, cents, src in income:
            if src:
                key = src
                inc_kind[key] = "rule"
                if key not in inc_rep:
                    inc_rep[key] = src
            else:
                key = _payee_norm(label)
                inc_kind.setdefault(key, "heuristic")
                if key not in inc_rep:
                    inc_rep[key] = (label or key).strip()
            inc_by[key] += int(cents)
            inc_count[key] += 1

        top_inc = sorted(inc_by.items(), key=lambda x: x[1], reverse=True)[:top]
        for i, (key, cents) in enumerate(top_inc):
            rep = inc_rep.get(key, key)
            kind = inc_kind.get(key, "")
            note = f"occurrences={inc_count[key]}"
            if kind:
                note = f"{note} | {kind}"
            wr("income_sources", i, rep, _usd(cents), "", note, "transactions")

        # Unknown income total (helpful for reliability)
        unknown_income = income_total - matched_income
        if income_total > 0:
            wr("income_quality", 0, "matched_income_total", _usd(matched_income), income_conf, "", "computed")
            wr(
                "income_quality",
                1,
                "unknown_income_total",
                _usd(unknown_income),
                income_conf,
                "add patterns to data/income_sources.csv to increase confidence",
                "computed",
            )

        with out_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows_out)

        console.print(f"[green]Wrote: {out_path}[/green]")

    finally:
        conn.close()


@app.command("export-sketchy")
def export_sketchy(
    out: str = typer.Option("/app/exports", help="Output directory for CSV exports."),
    days: int = typer.Option(60, help="Lookback window in days."),
):
    """
    Export sketchy/suspicious charges to CSV.

    Detects:
    - Duplicate charges (same merchant + amount within 3 days)
    - Unusual amounts (>2x median for that merchant)
    - Test charges ($0.01-$1.00)
    - Round amount spikes ($50/$100/$200 first time)
    - Rapid-fire charges (3+ in 24h)
    - Refund + recharge patterns
    """
    cfg = load_config()
    setup_logging(cfg)

    os.makedirs(out, exist_ok=True)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    try:
        alerts = detect_sketchy(conn, days=days)

        out_path = os.path.join(out, "sketchy_charges.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["posted_at", "merchant", "amount_usd", "pattern_type", "severity", "detail"])
            for alert in alerts:
                w.writerow([
                    alert.posted_at.isoformat(),
                    sanitize_csv_field(alert.merchant_norm),
                    f"{alert.amount_cents / 100:.2f}",
                    alert.pattern_type,
                    alert.severity,
                    sanitize_csv_field(alert.detail),
                ])

        console.print(f"[green]export complete[/green] wrote {len(alerts)} alerts -> {out_path}")
    finally:
        conn.close()


@app.command("export-duplicates")
def export_duplicates_cmd(
    out: str = typer.Option("/app/exports", help="Output directory for CSV exports."),
    days: int = typer.Option(400, help="Lookback window in days."),
):
    """
    Export duplicate subscription groups to CSV.

    Detects:
    - Fuzzy merchant matching (NETFLIX vs NETFLIX.COM)
    - Similar subscriptions (same amount +/- 10%, same cadence)
    - Known bundle families (Disney, Apple, Amazon, etc.)
    """
    cfg = load_config()
    setup_logging(cfg)

    os.makedirs(out, exist_ok=True)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    try:
        duplicates = detect_duplicates(conn, days=days)

        out_path = os.path.join(out, "duplicates.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["group_type", "merchants", "monthly_total_usd", "severity", "detail"])
            for dup in duplicates:
                w.writerow([
                    dup.group_type,
                    sanitize_csv_field("; ".join(dup.merchants)),
                    f"{dup.total_monthly_cents / 100:.2f}",
                    dup.severity,
                    sanitize_csv_field(dup.detail),
                ])

        console.print(f"[green]export complete[/green] wrote {len(duplicates)} groups -> {out_path}")
    finally:
        conn.close()


@app.command("export-summary")
def export_summary_cmd(
    out: str = typer.Option("/app/exports", help="Output directory for CSV exports."),
    period: str = typer.Option("month", help="Period type: month, quarter, year"),
    num_periods: int = typer.Option(12, help="Number of periods to export."),
):
    """
    Export income vs spend summary with rolling averages.
    """
    cfg = load_config()
    setup_logging(cfg)

    os.makedirs(out, exist_ok=True)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    period_map = {"month": TimePeriod.MONTH, "quarter": TimePeriod.QUARTER, "year": TimePeriod.YEAR}
    period_type = period_map.get(period.lower(), TimePeriod.MONTH)

    try:
        # Using canonical ReportService for all totals
        service = ReportService(conn)
        reports = service.report_periods(period_type, num_periods=num_periods)
        periods = compute_period_trends(reports, avg_window=3)

        out_path = os.path.join(out, f"{period}_summary.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "period", "start_date", "end_date",
                "income_usd", "recurring_usd", "discretionary_usd", "net_usd",
                "avg_income_usd", "avg_recurring_usd", "avg_discretionary_usd",
                "income_trend", "recurring_trend", "discretionary_trend",
                "transaction_count",
            ])
            for p in periods:
                w.writerow([
                    p.period_label,
                    p.start_date.isoformat(),
                    p.end_date.isoformat(),
                    f"{p.income_cents / 100:.2f}",
                    f"{p.recurring_cents / 100:.2f}",
                    f"{p.discretionary_cents / 100:.2f}",
                    f"{p.net_cents / 100:.2f}",
                    f"{p.avg_income_cents / 100:.2f}",
                    f"{p.avg_recurring_cents / 100:.2f}",
                    f"{p.avg_discretionary_cents / 100:.2f}",
                    p.income_trend,
                    p.recurring_trend,
                    p.discretionary_trend,
                    p.transaction_count,
                ])

        console.print(f"[green]export complete[/green] wrote {len(periods)} periods -> {out_path}")
    finally:
        conn.close()


@app.command("dashboard-cli")
def dashboard_cli(
    period: str = typer.Option("month", help="Period type: month, quarter, year"),
):
    """
    CLI dashboard: show financial health summary, alerts, and duplicates.
    """
    cfg = load_config()
    setup_logging(cfg)

    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    period_map = {"month": TimePeriod.MONTH, "quarter": TimePeriod.QUARTER, "year": TimePeriod.YEAR}
    period_type = period_map.get(period.lower(), TimePeriod.MONTH)

    try:
        # Get current period analysis using canonical ReportService
        service = ReportService(conn)
        reports = service.report_periods(period_type, num_periods=3)  # Get 3 for trends

        if not reports:
            console.print("[yellow]No transaction data available.[/yellow]")
            return

        periods = compute_period_trends(reports, avg_window=3)
        p = periods[0]

        # Format trend arrows
        def trend_arrow(t):
            if t == "up":
                return "[green]^[/green]"
            elif t == "down":
                return "[red]v[/red]"
            return "[dim]-[/dim]"

        console.print()
        console.print(f"[bold]FINANCIAL HEALTH - {p.period_label}[/bold]")
        console.print()

        console.print(f"  Income:        [green]${p.income_cents/100:>10,.2f}[/green]  {trend_arrow(p.income_trend)} vs prev  [dim]avg: ${p.avg_income_cents/100:,.0f}[/dim]")
        console.print(f"  Recurring:     [red]${p.recurring_cents/100:>10,.2f}[/red]  {trend_arrow(p.recurring_trend)} {p.recurring_trend:<6}  [dim]avg: ${p.avg_recurring_cents/100:,.0f}[/dim]")
        console.print(f"  Discretionary: [red]${p.discretionary_cents/100:>10,.2f}[/red]  {trend_arrow(p.discretionary_trend)} vs prev  [dim]avg: ${p.avg_discretionary_cents/100:,.0f}[/dim]")

        net_color = "green" if p.net_cents >= 0 else "red"
        net_status = "On track" if p.net_cents >= 0 else "Over budget"
        console.print(f"  Net:           [{net_color}]${p.net_cents/100:>10,.2f}[/{net_color}]  {net_status}")
        console.print()

        # Alerts
        alerts = detect_sketchy(conn, days=60)
        if alerts:
            console.print(f"[bold yellow]ALERTS ({len(alerts)})[/bold yellow]")
            for a in alerts[:5]:
                icon = "[red]*[/red]" if a.severity == "high" else "[yellow]*[/yellow]" if a.severity == "medium" else "[dim]*[/dim]"
                console.print(f"  {icon} {a.pattern_type.replace('_', ' ').title()}: {a.merchant_norm} ${a.amount_cents/100:.2f}")
                console.print(f"      [dim]{a.detail}[/dim]")
            if len(alerts) > 5:
                console.print(f"  [dim]+ {len(alerts) - 5} more...[/dim]")
            console.print()

        # Duplicates
        duplicates = detect_duplicates(conn, days=400)
        if duplicates:
            console.print(f"[bold yellow]POSSIBLE DUPLICATE SUBSCRIPTIONS ({len(duplicates)})[/bold yellow]")
            for d in duplicates[:3]:
                console.print(f"  * {d.detail} [dim](${d.total_monthly_cents/100:.2f}/mo)[/dim]")
                for item in d.items[:3]:
                    console.print(f"      - {item[0]} ${item[1]/100:.2f}/mo ({item[2]})")
            if len(duplicates) > 3:
                console.print(f"  [dim]+ {len(duplicates) - 3} more...[/dim]")
            console.print()

        console.print("[dim]Run 'fin web' to see full dashboard in browser[/dim]")

    finally:
        conn.close()


@app.command("import-csv")
def import_csv(
    file: str = typer.Argument(..., help="Path to CSV file to import"),
    account_id: str = typer.Option("manual-import", help="Account ID to assign to imported transactions"),
    date_col: str = typer.Option("date", help="Column name for transaction date"),
    amount_col: str = typer.Option("amount", help="Column name for amount (negative = expense)"),
    description_col: str = typer.Option("description", help="Column name for description/memo"),
    merchant_col: str = typer.Option(None, help="Column name for merchant (optional, uses description if not set)"),
    date_format: str = typer.Option("%Y-%m-%d", help="Date format (e.g., %%Y-%%m-%%d, %%m/%%d/%%Y)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without importing"),
):
    """
    Import transactions from a CSV file.

    The CSV should have columns for date, amount, and description/merchant.
    Amount should be negative for expenses, positive for income.

    Examples:
        fin import-csv transactions.csv
        fin import-csv bank_export.csv --date-col "Posted Date" --amount-col "Amount" --description-col "Description"
        fin import-csv export.csv --date-format "%%m/%%d/%%Y"
        fin import-csv data.csv --dry-run  # Preview only

    Column auto-detection: If your CSV has standard column names (date, amount, description),
    they'll be detected automatically.
    """
    import hashlib

    cfg = load_config()
    setup_logging(cfg)

    csv_path = Path(file)
    if not csv_path.exists():
        console.print(f"[red]Error:[/red] File not found: {csv_path}")
        raise typer.Exit(1)

    # Read and parse CSV
    try:
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            # Detect dialect
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample)
            except csv.Error:
                dialect = csv.excel

            reader = csv.DictReader(f, dialect=dialect)
            rows = list(reader)
    except Exception as e:
        console.print(f"[red]Error reading CSV:[/red] {e}")
        raise typer.Exit(1)

    if not rows:
        console.print("[yellow]CSV file is empty or has no data rows.[/yellow]")
        raise typer.Exit(1)

    # Verify columns exist
    available_cols = set(rows[0].keys())
    console.print(f"[dim]Found columns: {', '.join(sorted(available_cols))}[/dim]")
    console.print()

    # Check required columns
    missing = []
    if date_col not in available_cols:
        missing.append(f"date column '{date_col}'")
    if amount_col not in available_cols:
        missing.append(f"amount column '{amount_col}'")
    if description_col not in available_cols and merchant_col not in available_cols:
        missing.append(f"description column '{description_col}' or merchant column")

    if missing:
        console.print(f"[red]Error:[/red] Missing columns: {', '.join(missing)}")
        console.print()
        console.print("Available columns:")
        for col in sorted(available_cols):
            console.print(f"  - {col}")
        console.print()
        console.print("Use --date-col, --amount-col, --description-col to specify your column names.")
        raise typer.Exit(1)

    # Parse transactions
    transactions = []
    parse_errors = []

    for i, row in enumerate(rows, start=2):  # Start at 2 (row 1 is header)
        try:
            # Parse date
            date_str = row.get(date_col, "").strip()
            try:
                posted_date = datetime.strptime(date_str, date_format).date()
            except ValueError:
                # Try common formats
                for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%Y/%m/%d"]:
                    try:
                        posted_date = datetime.strptime(date_str, fmt).date()
                        break
                    except ValueError:
                        continue
                else:
                    raise ValueError(f"Cannot parse date '{date_str}'")

            # Parse amount
            amount_str = row.get(amount_col, "0").strip()
            # Remove currency symbols and commas
            amount_str = amount_str.replace("$", "").replace(",", "").replace(" ", "")
            # Handle parentheses for negative (accounting format)
            if amount_str.startswith("(") and amount_str.endswith(")"):
                amount_str = "-" + amount_str[1:-1]
            # Use Decimal for precise money conversion with ROUND_HALF_UP
            amount = Decimal(amount_str)
            amount_cents = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

            # Get description/merchant
            description = row.get(description_col, "").strip() if description_col in available_cols else ""
            merchant = row.get(merchant_col, "").strip() if merchant_col and merchant_col in available_cols else ""

            if not merchant and not description:
                raise ValueError("No description or merchant")

            # Generate fingerprint for deduplication
            fp_data = f"{posted_date.isoformat()}|{amount_cents}|{merchant or description}|{account_id}"
            fingerprint = hashlib.sha256(fp_data.encode()).hexdigest()[:32]

            transactions.append({
                "account_id": account_id,
                "posted_at": posted_date.isoformat(),
                "amount_cents": amount_cents,
                "currency": "USD",
                "description": description,
                "merchant": merchant if merchant else description,
                "fingerprint": f"csv_{fingerprint}",
            })

        except Exception as e:
            parse_errors.append(f"Row {i}: {e}")

    if parse_errors:
        console.print(f"[yellow]Parse errors ({len(parse_errors)}):[/yellow]")
        for err in parse_errors[:10]:
            console.print(f"  {err}")
        if len(parse_errors) > 10:
            console.print(f"  ... and {len(parse_errors) - 10} more")
        console.print()

    if not transactions:
        console.print("[red]No valid transactions to import.[/red]")
        raise typer.Exit(1)

    # Summary
    income = sum(t["amount_cents"] for t in transactions if t["amount_cents"] > 0)
    expense = sum(abs(t["amount_cents"]) for t in transactions if t["amount_cents"] < 0)
    dates = sorted(t["posted_at"] for t in transactions)

    console.print(f"[bold]Import Summary[/bold]")
    console.print(f"  Transactions: {len(transactions)}")
    console.print(f"  Date range: {dates[0]} to {dates[-1]}")
    console.print(f"  Income: [green]${income/100:,.2f}[/green]")
    console.print(f"  Expenses: [red]${expense/100:,.2f}[/red]")
    console.print(f"  Net: ${(income - expense)/100:,.2f}")
    console.print()

    if dry_run:
        console.print("[yellow]Dry run - no changes made.[/yellow]")
        console.print()
        console.print("Sample transactions:")
        for t in transactions[:5]:
            direction = "+" if t["amount_cents"] > 0 else ""
            console.print(f"  {t['posted_at']} | {direction}${t['amount_cents']/100:.2f} | {t['merchant'][:40]}")
        if len(transactions) > 5:
            console.print(f"  ... and {len(transactions) - 5} more")
        return

    # Import to database
    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    try:
        # Ensure account exists
        conn.execute(
            """
            INSERT OR IGNORE INTO accounts (account_id, institution, name, type, currency, last_seen_at)
            VALUES (?, 'Manual Import', ?, 'checking', 'USD', datetime('now'))
            """,
            (account_id, account_id),
        )

        inserted = 0
        skipped = 0

        for t in transactions:
            # Check if already exists (by fingerprint)
            existing = conn.execute(
                "SELECT 1 FROM transactions WHERE fingerprint = ?",
                (t["fingerprint"],)
            ).fetchone()

            if existing:
                skipped += 1
                continue

            conn.execute(
                """
                INSERT INTO transactions (
                    account_id, posted_at, amount_cents, currency,
                    description, merchant, fingerprint, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    t["account_id"],
                    t["posted_at"],
                    t["amount_cents"],
                    t["currency"],
                    t["description"],
                    t["merchant"],
                    t["fingerprint"],
                ),
            )
            inserted += 1

        conn.commit()

        console.print(f"[green]Import complete![/green]")
        console.print(f"  Inserted: {inserted}")
        console.print(f"  Skipped (duplicates): {skipped}")

    finally:
        conn.close()


@app.command("export-backup")
def export_backup(
    output: str = typer.Option(None, "--output", "-o", help="Output file path (default: fin_backup_YYYYMMDD.finbak)"),
    recipient: str = typer.Option(None, "--recipient", "-r", help="age public key recipient (age1...)"),
    passphrase: bool = typer.Option(False, "--passphrase", "-p", help="Encrypt with passphrase (interactive prompt or FIN_BACKUP_PASSWORD env var)"),
    no_encrypt: bool = typer.Option(False, "--no-encrypt", help="Create an UNENCRYPTED backup (not recommended)."),
):
    """
    Export an encrypted backup of the database.

    Encryption is required by default. Specify --passphrase (-p) or --recipient (-r).
    The FIN_BACKUP_PASSWORD environment variable is read automatically for passphrase mode.
    Use --no-encrypt only if you understand the risk.

    Requires the 'age' CLI tool to be installed:
    - Windows: winget install FiloSottile.age
    - macOS: brew install age
    - Linux: apt install age (or download from https://github.com/FiloSottile/age)

    Examples:
        fin export-backup -p                    # Passphrase encryption (prompted or via env)
        fin export-backup -r age1abc123...      # Recipient public key
        fin export-backup -p -o backup.finbak   # Custom output path
        fin export-backup --no-encrypt          # Unencrypted (shows warning)

    Decrypt with:
        age -d -o fin.db backup.finbak          # For passphrase mode
        age -d -i key.txt -o fin.db backup.finbak  # For recipient mode
    """
    import getpass
    import shutil
    import subprocess
    import sys

    cfg = load_config()
    setup_logging(cfg)

    today_str = dates_mod.today().strftime("%Y%m%d")

    # --- Unencrypted path ---
    if no_encrypt:
        sys.stderr.write(
            "\nWARNING: Creating an unencrypted backup.\n"
            "This file contains your complete financial history in plain text.\n"
            "Store it securely and delete it when no longer needed.\n\n"
        )
        db_path = Path(cfg.db_path)
        if not db_path.exists():
            console.print(f"[red]Error:[/red] Database not found at {db_path}")
            raise typer.Exit(1)
        if not output:
            output = f"fin_backup_{today_str}-UNENCRYPTED.sqlite"
        output_path = Path(output)
        import shutil as _shutil
        _shutil.copy2(str(db_path), str(output_path))
        console.print(f"[yellow]Unencrypted backup written to:[/yellow] {output_path}")
        return

    # --- Encrypted path ---
    age_path = shutil.which("age")
    if not age_path:
        console.print("[red]Error:[/red] 'age' encryption tool not found.")
        console.print()
        console.print("Install age:")
        console.print("  Windows: [cyan]winget install FiloSottile.age[/cyan]")
        console.print("  macOS:   [cyan]brew install age[/cyan]")
        console.print("  Linux:   [cyan]apt install age[/cyan] or download from https://github.com/FiloSottile/age")
        raise typer.Exit(1)

    # Resolve encryption mode: check env var before prompting
    env_password = os.getenv("FIN_BACKUP_PASSWORD", "")

    if not passphrase and not recipient:
        # Default: require encryption — prompt for passphrase
        if env_password:
            passphrase = True
        else:
            console.print("[bold]Backup encryption required.[/bold]")
            console.print("Specify --passphrase (-p) or --recipient (-r), or set FIN_BACKUP_PASSWORD.")
            console.print()
            console.print("Prompting for passphrase interactively...")
            passphrase = True

    if passphrase and recipient:
        console.print("[red]Error:[/red] Cannot use both --passphrase and --recipient")
        raise typer.Exit(1)

    # Verify database exists
    db_path = Path(cfg.db_path)
    if not db_path.exists():
        console.print(f"[red]Error:[/red] Database not found at {db_path}")
        raise typer.Exit(1)

    # Generate output filename
    if not output:
        output = f"fin_backup_{today_str}.finbak"
    output_path = Path(output)

    # Build age command
    cmd = [age_path, "-o", str(output_path)]

    if passphrase:
        if env_password:
            # Pass password non-interactively via stdin
            cmd.extend(["-p"])
            cmd.append(str(db_path))
            console.print(f"[bold]Encrypting database backup (password from FIN_BACKUP_PASSWORD)...[/bold]")
            console.print(f"  Source: {db_path}")
            console.print(f"  Output: {output_path}")
            console.print()
            try:
                result = subprocess.run(
                    cmd, check=True,
                    input=env_password + "\n" + env_password + "\n",
                    capture_output=True, text=True,
                )
            except subprocess.CalledProcessError as e:
                console.print(f"[red]Encryption failed:[/red] age returned exit code {e.returncode}")
                if e.stderr:
                    console.print(f"[dim]{e.stderr.strip()}[/dim]")
                raise typer.Exit(1)
        else:
            cmd.append("-p")
            cmd.append(str(db_path))
            console.print(f"[bold]Encrypting database backup...[/bold]")
            console.print(f"  Source: {db_path}")
            console.print(f"  Output: {output_path}")
            console.print()
            console.print("[yellow]You will be prompted to enter a passphrase.[/yellow]")
            console.print()
            try:
                result = subprocess.run(cmd, check=True)
            except subprocess.CalledProcessError as e:
                console.print(f"[red]Encryption failed:[/red] age returned exit code {e.returncode}")
                raise typer.Exit(1)
    else:
        cmd.extend(["-r", recipient])
        cmd.append(str(db_path))
        console.print(f"[bold]Encrypting database backup...[/bold]")
        console.print(f"  Source: {db_path}")
        console.print(f"  Output: {output_path}")
        console.print()
        try:
            result = subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Encryption failed:[/red] age returned exit code {e.returncode}")
            raise typer.Exit(1)
        except FileNotFoundError:
            console.print("[red]Error:[/red] age command not found")
            raise typer.Exit(1)

    console.print()
    console.print(f"[green]Backup complete![/green] Encrypted to: {output_path}")
    console.print()
    console.print("To decrypt:")
    if passphrase:
        console.print(f"  [cyan]age -d -o fin.db {output_path}[/cyan]")
    else:
        console.print(f"  [cyan]age -d -i your_key.txt -o fin.db {output_path}[/cyan]")


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def startup_security_check(host: str, auth_disabled: bool) -> None:
    """
    Hard-block dangerous startup combinations.

    Rules:
    - auth_disabled=True AND non-loopback host → sys.exit(1). No override.
    - auth_disabled=True AND loopback host → yellow WARNING to stderr (continue).

    Call this before any server startup logic.
    """
    import sys

    is_loopback = host in _LOOPBACK_HOSTS

    if auth_disabled and not is_loopback:
        import sys as _sys
        _sys.stderr.write(
            "\nERROR: Refusing to start.\n"
            "FIN_AUTH_DISABLED=1 is set AND the server is binding to a non-loopback "
            f"address ({host}).\n"
            "This combination would expose your financial data to the network with no "
            "authentication.\n\n"
            "To fix, choose one of:\n"
            "  1. Remove FIN_AUTH_DISABLED=1 (recommended)\n"
            "  2. Bind to loopback only: --host 127.0.0.1\n\n"
            "There is no override for this check.\n\n"
        )
        sys.exit(1)

    if auth_disabled and is_loopback:
        import sys as _sys
        _sys.stderr.write(
            "\nWARNING: FIN_AUTH_DISABLED=1 is set.\n"
            "Authentication is disabled. Anyone with access to this machine can read and "
            "modify your financial data via the web UI.\n"
            "This is only permitted because the server is binding to loopback only.\n\n"
        )


def _check_fde(db_path) -> tuple:
    """Check if the volume hosting db_path has full-disk encryption enabled."""
    import subprocess, sys

    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["fdesetup", "status"],
                capture_output=True, text=True, timeout=5
            )
            if "FileVault is On" in result.stdout:
                return True, "FileVault is enabled"
            elif "FileVault is Off" in result.stdout:
                return False, "FileVault is not enabled — your financial data is unencrypted at rest"
        except Exception:
            pass
        return None, "Could not verify FileVault status"

    elif sys.platform == "win32":
        drive = db_path.resolve().drive  # e.g. "C:"
        if not drive:
            return None, "Could not determine drive for encryption check"
        try:
            result = subprocess.run(
                ["manage-bde", "-status", drive],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                if "Protection On" in result.stdout:
                    return True, f"BitLocker is enabled on {drive}"
                elif "Protection Off" in result.stdout:
                    return False, f"BitLocker is not enabled on {drive} — your financial data is unencrypted at rest"
            # Non-zero return often means insufficient privileges
            return None, f"Could not verify BitLocker status on {drive} (run as administrator for full check)"
        except FileNotFoundError:
            return None, "manage-bde not found — verify BitLocker is enabled manually"
        except Exception:
            return None, "Could not verify BitLocker status"

    else:
        # Linux: too many distros/setups to check reliably — skip
        return None, ""


@app.command()
def web(
    port: int = typer.Option(8000, help="Port to serve on."),
    host: str = typer.Option("127.0.0.1", help="Host to bind to. Use 0.0.0.0 for LAN access (security risk)."),
    no_tls: bool = typer.Option(False, "--no-tls", help="Disable HTTPS and serve over plain HTTP."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open the browser automatically after startup."),
    i_understand_no_fde: bool = typer.Option(
        False,
        "--i-understand-no-fde",
        help="Bypass the full-disk encryption requirement. Use only if you understand the data-at-rest risk.",
    ),
):
    """Run the local web UI.

    By default, binds to localhost (127.0.0.1) with HTTPS (self-signed cert).
    Use --host 0.0.0.0 to allow LAN access.
    Use --no-tls to disable HTTPS (loopback only).

    API Authentication:
    - All /api/* endpoints require bearer token or session cookie auth
    - Set FIN_API_TOKEN env var for a fixed token, or use the auto-generated one
    - Set FIN_AUTH_DISABLED=1 to disable auth (loopback only — hard-blocked on LAN)
    """
    import sys
    import uvicorn
    from pathlib import Path
    from .security import get_auth_info
    from .tls import ensure_cert

    auth_disabled = os.getenv("FIN_AUTH_DISABLED", "").lower() in ("1", "true", "yes")
    is_loopback = host in _LOOPBACK_HOSTS

    # --- Item 3: Hard-block auth-disabled + non-loopback ---
    startup_security_check(host, auth_disabled)

    cfg = load_config()
    db_path = Path(cfg.db_path)

    from .config import ensure_data_dir
    ensure_data_dir(str(db_path))

    # Check if this looks like demo or real data
    if "demo" in str(db_path).lower():
        console.print(f"[yellow]Using demo database:[/yellow] {db_path}")
    else:
        console.print(f"[dim]Database:[/dim] {db_path}")

        # Hint about demo if no real data exists
        if not db_path.exists():
            console.print()
            console.print("[dim]No data yet. Try [cyan]fin demo[/cyan] to explore with sample data.[/dim]")

        # --- Item 5: FDE check — hard block unless bypassed ---
        skip_fde = i_understand_no_fde or os.getenv("FIN_SKIP_FDE_CHECK", "").lower() in ("1", "true", "yes")
        encrypted, fde_msg = _check_fde(db_path)
        if encrypted is False:
            if skip_fde:
                console.print(f"[yellow]Warning (bypassed):[/yellow] {fde_msg}.")
            else:
                sys.stderr.write(
                    f"\nERROR: Full-disk encryption is not enabled.\n"
                    f"{fde_msg}.\n\n"
                    "Your financial database is stored unencrypted on disk. If this machine\n"
                    "is lost or stolen, your complete financial history will be readable.\n\n"
                    "To fix:\n"
                    "  Windows: Enable BitLocker (Settings > Privacy & Security > Device Encryption)\n"
                    "  macOS:   Enable FileVault (System Preferences > Security & Privacy)\n\n"
                    "To bypass this check (not recommended):\n"
                    "  fin web --i-understand-no-fde\n"
                    "  or set FIN_SKIP_FDE_CHECK=1\n\n"
                )
                sys.exit(1)
        elif encrypted is None and fde_msg:
            console.print(f"[dim]Encryption check: {fde_msg}[/dim]")

    # Warn if binding to all interfaces
    if not is_loopback:
        console.print("[yellow]Warning: Binding to all interfaces. Your financial data will be accessible on the network.[/yellow]")
        console.print()

    # Show auth info
    auth_info = get_auth_info()
    if auth_info["auth_enabled"]:
        console.print(f"[dim]API Token:[/dim] {auth_info['full_token']} [dim]({auth_info['source']})[/dim]")
        console.print("[dim]Use this token to authenticate: set it as a cookie (fin_session) or Bearer header.[/dim]")
    else:
        console.print("[yellow]API Auth: Disabled[/yellow] (loopback only — set FIN_API_TOKEN to enable)")

    # --- Item 8: TLS setup — hard block on non-loopback if TLS unavailable ---
    ssl_kwargs = {}
    scheme = "http"
    if not no_tls:
        cert_dir = db_path.parent / "certs"
        result = ensure_cert(cert_dir)
        if result:
            cert_path, key_path = result
            ssl_kwargs["ssl_certfile"] = str(cert_path)
            ssl_kwargs["ssl_keyfile"] = str(key_path)
            scheme = "https"
            console.print(f"[dim]TLS cert:[/dim] {cert_path}")
            console.print("[dim]Your browser will show a security warning for the self-signed cert — this is expected.[/dim]")
        else:
            if not is_loopback:
                sys.stderr.write(
                    "\nERROR: TLS unavailable. Refusing to start on a non-loopback address without encryption.\n"
                    "openssl is required to generate a self-signed certificate for LAN access.\n\n"
                    "To fix:\n"
                    "  Windows: Install OpenSSL (e.g. via winget install openssl)\n"
                    "  macOS:   brew install openssl\n"
                    "  Linux:   apt install openssl\n\n"
                    "Alternatively, bind to loopback only: fin web --host 127.0.0.1\n\n"
                )
                sys.exit(1)
            else:
                sys.stderr.write(
                    "\nWARNING: TLS unavailable (openssl not found). Running over HTTP.\n"
                    "Your session token and financial data are transmitted unencrypted.\n"
                    "This is only permitted because the server is binding to loopback.\n\n"
                )
    else:
        # --no-tls explicitly requested
        if not is_loopback:
            sys.stderr.write(
                "\nERROR: --no-tls is not permitted when binding to a non-loopback address.\n"
                "Serving financial data over plain HTTP on a network interface is not allowed.\n"
                "Remove --no-tls or use --host 127.0.0.1.\n\n"
            )
            sys.exit(1)
        else:
            sys.stderr.write(
                "\nWARNING: Running over HTTP (--no-tls). "
                "Your session token and financial data are unencrypted.\n\n"
            )

    display_host = host if is_loopback else "127.0.0.1"
    console.print(f"[dim]Dashboard:[/dim] {scheme}://{display_host}:{port}/dashboard")
    console.print()

    if not no_browser:
        import threading
        import time
        import webbrowser
        import urllib.request
        import ssl

        def _open_browser_when_ready(scheme: str, port: int) -> None:
            health_url = f"{scheme}://127.0.0.1:{port}/health"
            dashboard_url = f"{scheme}://127.0.0.1:{port}/dashboard"

            # For self-signed certs, disable certificate verification in the poller only.
            if scheme == "https":
                ssl_ctx: ssl.SSLContext | None = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
            else:
                ssl_ctx = None

            for _ in range(20):
                time.sleep(0.5)
                try:
                    with urllib.request.urlopen(health_url, context=ssl_ctx, timeout=2) as resp:
                        if resp.status == 200:
                            webbrowser.open(dashboard_url)
                            return
                except Exception:
                    pass

            # All 20 attempts exhausted — server did not become ready in time.
            console.print("[dim]Browser auto-open: server did not respond to /health in time.[/dim]")

        browser_thread = threading.Thread(
            target=_open_browser_when_ready,
            args=(scheme, port),
            daemon=True,
        )
        browser_thread.start()

    uvicorn.run("fin.web:app", host=host, port=port, reload=False, **ssl_kwargs)


@app.command()
def demo(
    months: int = typer.Option(12, help="Months of demo data to generate."),
    start_web: bool = typer.Option(True, "--web/--no-web", help="Start web dashboard after loading."),
    port: int = typer.Option(8000, help="Port for web dashboard."),
    reset: bool = typer.Option(False, "--reset", help="Delete existing demo database first."),
    clear: bool = typer.Option(False, "--clear", help="Delete demo database and exit (cleanup)."),
    status: bool = typer.Option(False, "--status", help="Show demo vs real database status."),
):
    """
    Load demo data to explore fin without SimpleFIN.

    Creates a demo database with realistic transactions:
    - Income (bi-weekly paychecks)
    - Subscriptions (Netflix, Spotify, etc.)
    - Bills (utilities with variable amounts)
    - One-off spending (restaurants, shopping, gas)
    - Sample alerts (duplicate charge, unusual amount)

    The demo uses a SEPARATE database (data/demo.db) that won't
    affect your real data. When you're ready to use real data:

        fin demo --clear      # Remove demo database
        fin credentials set   # Configure SimpleFIN
        fin sync --full       # Pull real transactions

    Examples:
        fin demo              # Load 12 months of data and start web
        fin demo --no-web     # Just load data, don't start web
        fin demo --reset      # Clear and reload demo data
        fin demo --clear      # Delete demo database (cleanup)
        fin demo --status     # Check which databases exist
    """
    from pathlib import Path
    from .demo import load_demo_data, DEMO_ACCOUNTS

    # Use a separate demo database
    demo_db_path = Path("data/demo.db")
    real_db_path = Path(load_config().db_path)

    # Handle --status: show database status
    if status:
        console.print("[bold]Database Status[/bold]")
        console.print()

        # Demo database
        if demo_db_path.exists():
            conn = dbmod.connect(str(demo_db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM transactions")
            demo_count = cursor.fetchone()[0]
            conn.close()
            console.print(f"[green]Demo:[/green] {demo_db_path} ({demo_count} transactions)")
        else:
            console.print(f"[dim]Demo:[/dim] {demo_db_path} (not created)")

        # Real database
        if real_db_path.exists():
            conn = dbmod.connect(str(real_db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM transactions")
            real_count = cursor.fetchone()[0]
            conn.close()
            console.print(f"[green]Real:[/green] {real_db_path} ({real_count} transactions)")
        else:
            console.print(f"[dim]Real:[/dim] {real_db_path} (not created)")

        console.print()
        console.print("[dim]fin web[/dim] uses the real database")
        console.print("[dim]fin demo[/dim] uses the demo database")
        return

    # Handle --clear: delete demo database and exit
    if clear:
        if demo_db_path.exists():
            demo_db_path.unlink()
            console.print("[green]Demo database deleted.[/green]")
            console.print()
            console.print("You can now set up real data:")
            console.print("  [cyan]fin credentials set[/cyan]   # Configure SimpleFIN")
            console.print("  [cyan]fin sync --full[/cyan]       # Pull real transactions")
        else:
            console.print("[dim]No demo database found.[/dim]")
        return

    demo_db_path.parent.mkdir(parents=True, exist_ok=True)

    if reset and demo_db_path.exists():
        demo_db_path.unlink()
        console.print("[yellow]Deleted existing demo database[/yellow]")

    # Check if demo data already exists
    if demo_db_path.exists():
        conn = dbmod.connect(str(demo_db_path))
        cursor = conn.execute("SELECT COUNT(*) FROM transactions")
        count = cursor.fetchone()[0]
        conn.close()

        if count > 0:
            console.print(f"[green]Demo database already has {count} transactions.[/green]")
            console.print(f"[dim]Use --reset to regenerate demo data.[/dim]")
        else:
            # Empty database, load data
            conn = dbmod.connect(str(demo_db_path))
            dbmod.init_db(conn)
            accounts, txns = load_demo_data(conn, months)
            conn.close()
            console.print(f"[green]Loaded {txns} demo transactions across {accounts} accounts.[/green]")
    else:
        # Create and load
        conn = dbmod.connect(str(demo_db_path))
        dbmod.init_db(conn)
        accounts, txns = load_demo_data(conn, months)
        conn.close()
        console.print(f"[green]Created demo database with {txns} transactions.[/green]")

    console.print()
    console.print("[bold]Demo Accounts:[/bold]")
    for acct in DEMO_ACCOUNTS:
        console.print(f"  - {acct['name']} ({acct['type']})")

    if start_web:
        console.print()
        console.print(f"[bold]Starting web dashboard...[/bold]")
        console.print(f"Open [cyan]http://127.0.0.1:{port}/dashboard[/cyan]")
        console.print()

        # Set environment to use demo database
        os.environ["FIN_DB_PATH"] = str(demo_db_path)

        import uvicorn
        uvicorn.run("fin.web:app", host="127.0.0.1", port=port, reload=False)
    else:
        console.print()
        console.print(f"To view the demo, run:")
        console.print(f"  [cyan]set FIN_DB_PATH={demo_db_path}[/cyan]  (Windows)")
        console.print(f"  [cyan]export FIN_DB_PATH={demo_db_path}[/cyan]  (Mac/Linux)")
        console.print(f"  [cyan]fin web[/cyan]")