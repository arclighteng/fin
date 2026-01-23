# fin

A local-first personal finance tool. Syncs with your bank accounts via SimpleFIN or import CSV directly—analyzes spending patterns, detects subscriptions, and keeps your data local with encrypted backups.

## Features

**Security & Privacy**
- **Local-Only Data**: All data stays on your machine in SQLite. No cloud sync, no tracking.
- **System Keyring**: Credentials stored in OS secure storage (Keychain, Credential Manager, Secret Service)
- **Encrypted Backups**: Age encryption for portable, audited backup files

**Accuracy & Trust**
- **Known Service Detection**: 150+ subscription services recognized instantly (Netflix, Spotify, etc.)
- **Pattern Validation**: `fin audit-subs` verifies detection accuracy—no false positives
- **Manual Override**: Correct any miscategorization; your choice persists

**Analysis**
- **Dashboard**: Visual overview of income, spending, alerts, and subscriptions
- **Smart Categorization**: Automatic transaction categorization with manual override
- **Subscription Detection**: Finds recurring charges and separates subscriptions from utility bills
- **Alerts**: Detects duplicate charges, unusual amounts, price increases
- **Bundle Detection**: Flags potential duplicate subscriptions (Disney+/Hulu/ESPN+ overlap)

**Interface**
- **Web Dashboard**: Full-featured UI at localhost
- **CLI Tools**: Complete command-line interface for automation
- **Mobile Responsive**: Works on phone, tablet, and desktop
- **Dark Mode**: Easy on the eyes

## Quick Start

### Option 1: Local Python (Recommended for Development)

```bash
# 1. Create virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Mac/Linux

# 2. Install
pip install -e .

# 3. Configure credentials (choose one)
fin credentials set                    # Recommended: uses system keyring
# OR: cp .env.example .env && edit .env  # Alternative: environment file

# 4. Set database path (Windows)
set FIN_DB_PATH=data/fin.db

# 5. Sync your transactions
fin sync --full  # First time: 120 days of history

# 6. Start the web dashboard
fin web
# Open http://127.0.0.1:8000/dashboard
```

### Option 2: Docker

```bash
# 1. Configure
cp .env.example .env
# Edit .env with your SimpleFIN access URL

# 2. Build & run
docker compose build
docker compose run --rm fin sync
docker compose run --rm fin web
```

### Try the Demo First

Explore fin with sample data before connecting your bank:

```bash
pip install -e .
fin demo
# Opens dashboard with 12 months of realistic demo data
```

When ready for real data:
```bash
fin demo --clear      # Remove demo database
fin credentials set   # Configure SimpleFIN
fin sync --full       # Pull real transactions
```

## Security

Your financial data is sensitive. fin is designed with security as a priority.

### Credential Storage

**Recommended: System Keyring**

```bash
fin credentials set
# Prompted for SimpleFIN Access URL, stored securely
```

Credentials are stored in:
- **Windows**: Credential Manager
- **macOS**: Keychain
- **Linux**: Secret Service (GNOME Keyring, KWallet)

Check status: `fin credentials status`

**Alternative: Environment File**

Create a `.env` file (gitignored):

```bash
SIMPLEFIN_ACCESS_URL=https://your-access-url-from-simplefin
FIN_DB_PATH=data/fin.db
```

Keyring takes priority over .env if both are configured.

### Data Protection

**Full-Disk Encryption (Recommended)**

Enable your OS's built-in encryption:

| OS | Solution |
|----|----------|
| Windows | BitLocker (Pro/Enterprise) or VeraCrypt |
| macOS | FileVault |
| Linux | LUKS |

This protects your database at rest with zero configuration in fin.

**Encrypted Backups**

For backups or sharing across devices:

```bash
# Install age encryption tool
# Windows: winget install FiloSottile.age
# macOS: brew install age
# Linux: apt install age

# Create encrypted backup with passphrase
fin export-backup -p

# Or with age public key (for automated backups)
fin export-backup -r age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p

# Decrypt later
age -d -o fin.db fin_backup_20260123.db.age
```

### Why This Architecture?

We chose OS-level encryption + encrypted exports over SQLCipher because:
1. Zero configuration for users who already have FileVault/BitLocker enabled
2. No additional dependencies or build complexity
3. `age` is a modern, audited tool with better key management
4. You control when encryption happens (backups) vs always-on overhead

## Subscription Detection

fin uses a two-tier approach to find subscriptions:

### Known Services (Instant)

150+ well-known services are recognized immediately, even with just one charge:
- Streaming: Netflix, Hulu, Disney+, HBO Max, YouTube TV, Spotify
- Software: Adobe, Microsoft 365, GitHub, ChatGPT, 1Password
- Fitness: Peloton, Planet Fitness, Strava
- And many more...

### Pattern Detection (3+ charges)

Unknown merchants are detected via:
- Consistent amounts (low variance)
- Regular intervals (weekly, monthly, annual)
- Recurring payment indicators

### Verifying Accuracy

```bash
# Audit what's being detected
fin audit-subs

# Show all detected (including pattern-based)
fin audit-subs --all
```

### Bundle Detection

Find overlapping subscriptions:

```bash
fin bundle-check
```

Flags vendor families where you might be paying twice (Disney+/Hulu/ESPN+, Apple services, etc.)

## Web Dashboard

The dashboard at `http://127.0.0.1:8000/dashboard` shows:

### Financial Health Banner
- Net position for the selected period
- Savings rate percentage
- Quick health indicators

### Spending by Category
- Click any category to drill down to merchants
- Click the pencil icon to change a merchant's category
- Categories auto-expand to show individual transactions

### Alerts
- Duplicate charges
- Unusual amounts (>2x your typical spend)
- Test charges ($0.01-$1.00)

### Date Selection
- **This Month / Last Month**: Quick period toggles
- **Custom dates**: Date pickers for any range
- **Account filter**: Focus on specific accounts

### Subscriptions & Bills
- **Subscriptions**: Netflix, Spotify, software services
- **Bills**: Electric, gas, internet (utility services)
- Click the type badge to change between Subscription/Bill
- Click "View All" to see the dedicated Recurring page

## CLI Commands

### Everyday Use

| Command | Description |
|---------|-------------|
| `fin sync` | Pull latest transactions from bank |
| `fin web` | Start the web dashboard |
| `fin status` | Financial status at a glance (CLI) |
| `fin trend` | Monthly trend over time |

### Analysis & Audit

| Command | Description |
|---------|-------------|
| `fin drill recurring` | All recurring expenses |
| `fin drill one-offs` | Discretionary spending |
| `fin drill alerts` | All alerts with details |
| `fin drill income` | Income sources |
| `fin audit-subs` | Verify subscription detection accuracy |
| `fin bundle-check` | Find duplicate/overlapping subscriptions |
| `fin dashboard-cli` | Full CLI dashboard with alerts |

### Export & Backup

| Command | Description |
|---------|-------------|
| `fin export-csv` | Export all data to CSV files |
| `fin export-backup -p` | Encrypted backup with passphrase |
| `fin export-backup -r age1...` | Encrypted backup to recipient key |
| `fin export-summary` | Income vs spend summary with rolling averages |
| `fin export-duplicates` | Export duplicate subscription groups |
| `fin import-csv FILE` | Import transactions from CSV |

### Credentials & Setup

| Command | Description |
|---------|-------------|
| `fin setup TOKEN` | Exchange SimpleFIN setup token for access URL |
| `fin credentials set` | Store credentials in system keyring |
| `fin credentials status` | Show credential source (keyring/env/none) |
| `fin credentials clear` | Remove credentials from keyring |

## Sync Options

```bash
# Daily sync (14 days) - catches new transactions
fin sync --quick

# Weekly sync (30 days) - default, covers statement cycle
fin sync

# After vacation/absence (120 days)
fin sync --full

# January annual (400 days) - finds yearly subscriptions
fin sync --annual-bootstrap
```

## Getting SimpleFIN

1. Go to [SimpleFIN Bridge](https://beta-bridge.simplefin.org/)
2. Subscribe (~$1.50/month) and link your bank accounts
3. Copy your Setup Token (base64-encoded string)
4. Run `fin setup YOUR_SETUP_TOKEN` to claim your Access URL
5. Credentials auto-save to keyring, or add to `.env` manually

See [docs/SIMPLEFIN_SETUP.md](docs/SIMPLEFIN_SETUP.md) for detailed instructions.

## Troubleshooting

### "No transactions found"
Run `fin sync --full` to pull more history.

### Categories are wrong
Click the category, then the pencil icon to override.

### Subscription showing as Bill (or vice versa)
Click the type badge to toggle.

### Suspicious subscription detection
Run `fin audit-subs` to verify what's being detected.

### Alerts not showing expected transactions
Use the date pickers to select a custom range, or click "This Month" / "Last Month" to reset.

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Type checking
mypy src/fin
```

## License

MIT
