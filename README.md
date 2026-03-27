# fin

Local-first personal finance. Syncs with your bank via SimpleFIN or CSV import — analyzes spending, detects subscriptions, catches unusual charges. All data stays on your machine.

**You are not the product.** No cloud. No tracking. No accounts.

[![PyPI](https://img.shields.io/pypi/v/getfin)](https://pypi.org/project/getfin/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## Features

### Security & Privacy
- **Local-only data** — SQLite on your machine. No cloud sync, no telemetry, no phone home.
- **System keyring** — Credentials stored in OS secure storage (Keychain, Credential Manager, Secret Service).
- **Encrypted backups** — Age encryption (ChaCha20-Poly1305) for portable, audited backup files.

### Analysis
- **Smart categorization** — Automatic transaction categorization with manual override.
- **Subscription detection** — 150+ known services recognized instantly, plus pattern detection for the rest.
- **Alerts** — Duplicate charges, unusual amounts, price increases, bundle overlap.
- **Spending breakdown** — Top categories with 3-month rolling averages and outlier badges.
- **Cash flow tracking** — Income vs expenses, savings rate, mid-month pacing.

### Interface
- **Web dashboard** — Full-featured UI at localhost with drilldown into every number.
- **CLI tools** — Complete command-line interface for automation and scripting.
- **Mobile responsive** — Works on phone, tablet, and desktop.
- **Dark mode** — Easy on the eyes.

## Getting Started

### Install from PyPI

```bash
pip install getfin
fin web
# Browser opens to https://127.0.0.1:8000/dashboard
```

> **First run:** The dashboard uses a self-signed certificate for local HTTPS — your browser will show a security warning, which is normal. On first launch with no data, the dashboard shows an empty state with a banner to import a CSV or connect SimpleFIN. To explore with sample data first, run `fin demo` — this loads demo transactions with a dismissible banner so you can see every feature before connecting your bank.

### Install from source

```bash
git clone https://github.com/arclighteng/fin.git
cd fin && python -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e .
fin web
```

### Docker

```bash
cp .env.example .env        # Edit with your SimpleFIN access URL
docker compose build
docker compose run --rm fin sync
docker compose run --rm fin web
```

The database is stored at:
- **Windows**: `%APPDATA%\fin\fin.db`
- **macOS/Linux**: `~/.local/share/fin/fin.db`

Override with `FIN_DB_PATH` if needed.

## Connecting Your Bank

From the dashboard, click **"Connect your bank"** to open the setup page (`/connect`). Two options:

**CSV import** (easiest — no account required)
1. Download a transaction export from your bank (CSV)
2. Drag and drop it onto the import page
3. Automatic format detection for Chase, BofA, Amex, Wells Fargo, and Capital One

**SimpleFIN** (automatic daily sync, ~$1.50/month)
1. Go to [SimpleFIN Bridge](https://beta-bridge.simplefin.org/), subscribe, and link your bank
2. Copy your Setup Token
3. Paste it into the SimpleFIN section on the connect page

See [docs/SIMPLEFIN_SETUP.md](docs/SIMPLEFIN_SETUP.md) for detailed SimpleFIN instructions.

## Web Dashboard

Launch with `fin web`. The dashboard at `/dashboard` shows a 5-card layout:

| Card | What it shows |
|------|---------------|
| **Cash Flow** | Income vs expenses, savings rate, 3-month comparison, mid-month pacing |
| **Commitments** | Detected subscriptions and bills, total as % of income, price change alerts |
| **Spending Breakdown** | Top 7 categories with bars, 3-month averages, outlier badges |
| **Heads Up** | Unusual charges with dismiss actions, spending trends, bill deviation alerts |
| **Your Trend** | 6-month bar chart of net cash flow, clickable months |

Click any number, bar, category, or merchant to drill down to the full transaction list.

### Other Pages

| Route | Purpose |
|-------|---------|
| `/connect` | Import CSV files or connect via SimpleFIN |
| `/commitments` | Subscriptions and bills — filter, export, toggle types |
| `/insights` | 12-month savings and income trends |
| `/review` | Transaction triage and categorization |
| `/budget` | Spending targets by category vs actual |
| `/watchlist` | Transaction watchlist |
| `/anomalies` | Unusual charges (60-day window) |
| `/sync-log` | Sync history |

### Navigation
- **Month navigation**: Previous/next with current month indicator
- **Account filter**: Multi-select to focus on specific accounts
- **Transaction search**: Live results — type a merchant name or amount
- **Keyboard accessible**: Tab navigation, Enter to select, Escape to close

## Subscription Detection

### Known Services (instant)

150+ services recognized from a single charge:
- **Streaming**: Netflix, Hulu, Disney+, Max, YouTube TV, Spotify, Apple Music
- **Software**: Adobe, Microsoft 365, GitHub, ChatGPT, 1Password
- **Fitness**: Peloton, Planet Fitness, Strava
- **And more** — VPN, news, gaming, home security, cloud storage

### Pattern Detection (3+ charges)

Unknown merchants are detected via consistent amounts, regular intervals, and recurring payment indicators.

### Bundle Detection

```bash
fin bundle-check
```

Flags vendor families where you might be paying twice (Disney+/Hulu/ESPN+, Apple services, etc.)

### Verifying Accuracy

```bash
fin audit-subs          # Audit detected subscriptions
fin audit-subs --all    # Include pattern-based detections
```

## Security

Your financial data is sensitive. fin eliminates entire threat categories by design.

### What we don't do
- Cloud storage or remote API calls
- User accounts, login tokens, or session cookies
- Telemetry, analytics, or phone home — ever

### What we delegate
- **Credentials** → OS keyring (Keychain, Credential Manager, Secret Service)
- **Backup encryption** → [age](https://github.com/FiloSottile/age) (ChaCha20-Poly1305)
- **Disk encryption** → BitLocker / FileVault / LUKS
- **TLS** → Python cryptography library

We didn't build our own crypto — that's the point.

### Credential Storage

```bash
fin credentials set       # Store SimpleFIN URL in system keyring
fin credentials status    # Check credential source (keyring/env/none)
fin credentials clear     # Remove from keyring
```

Credentials are stored in the OS keyring by default. Alternative: create a `.env` file (gitignored) with `SIMPLEFIN_ACCESS_URL`. Keyring takes priority if both are configured.

### Encrypted Backups

```bash
# Passphrase-protected
fin export-backup -p

# Age public key (for automated backups)
fin export-backup -r age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p

# Decrypt
age -d -o fin.db fin_backup_20260123.finbak
```

Requires `age`: `winget install FiloSottile.age` (Windows) / `brew install age` (macOS) / `apt install age` (Linux).

### Why this architecture?

OS-level encryption + encrypted exports instead of SQLCipher because:
1. Zero config for users who already have FileVault/BitLocker enabled
2. No additional dependencies or build complexity
3. `age` is a modern, audited tool with better key management
4. You control when encryption happens (backups) vs always-on overhead

## CLI Reference

### Sync

| Command | Description |
|---------|-------------|
| `fin sync` | Pull latest transactions (30 days, default) |
| `fin sync --quick` | Quick sync — 14 days (daily use) |
| `fin sync --full` | Full sync — 120 days (catch up after absence) |
| `fin sync --annual-bootstrap` | Annual sync — 400 days (find yearly subscriptions) |

### Dashboard & Reports

| Command | Description |
|---------|-------------|
| `fin web` | Start the web dashboard (browser opens automatically) |
| `fin web --no-browser` | Start without auto-opening browser |
| `fin dashboard-cli` | Full dashboard as CLI table with alerts |
| `fin report` | Spending report (CLI table output) |

### Analysis & Audit

| Command | Description |
|---------|-------------|
| `fin audit-subs` | Verify subscription detection accuracy |
| `fin audit-subs --all` | Include pattern-based detections |
| `fin bundle-check` | Find duplicate/overlapping subscriptions |
| `fin subs-pick` | Select subscriptions to audit |
| `fin watchlist-show` | Show transaction watchlist |
| `fin watchlist-done` | Mark watchlist items as reviewed |

### Export & Backup

| Command | Description |
|---------|-------------|
| `fin export-csv` | Export all data to CSV |
| `fin export-backup -p` | Encrypted backup with passphrase |
| `fin export-backup -r age1...` | Encrypted backup to recipient key |
| `fin export-summary` | Income vs spend summary with rolling averages |
| `fin export-duplicates` | Export duplicate subscription groups |
| `fin export-sketchy` | Export suspicious transactions |
| `fin import-csv FILE` | Import transactions from CSV |

### Setup & Diagnostics

| Command | Description |
|---------|-------------|
| `fin setup TOKEN` | Exchange SimpleFIN setup token for access URL |
| `fin credentials set` | Store credentials in system keyring |
| `fin credentials status` | Show credential source (keyring/env/none) |
| `fin credentials clear` | Remove credentials from keyring |
| `fin db-info` | Show account/transaction count and date range |
| `fin health` | Check SimpleFIN connection status |
| `fin demo` | Load demo data for testing |

### Accounting

| Command | Description |
|---------|-------------|
| `fin month-close` | Close accounting period |
| `fin month-report` | Generate monthly reconciliation report |

## Troubleshooting

### "No transactions found"
Run `fin sync --full` to pull more history, or import a CSV from `/connect`.

### Categories are wrong
Click the category in the dashboard, then click the edit icon to override.

### Subscription showing as bill (or vice versa)
Click the type badge on the Commitments page to toggle.

### Suspicious subscription detection
Run `fin audit-subs` to verify what's being detected.

## Contributing

Bug reports and feature requests are welcome via [GitHub Issues](https://github.com/arclighteng/fin/issues). Pull requests are considered — open an issue first to discuss the change. All contributions are licensed under [MIT](LICENSE).

## Development

```bash
pip install -e ".[dev]"
FIN_DB_PATH=/tmp/test.db pytest
mypy src/fin
```

## License

[MIT](LICENSE)
