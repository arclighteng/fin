# fin

A local-first personal finance tool. Syncs with your bank accounts via SimpleFIN, analyzes spending patterns, detects subscriptions, and helps you understand: **Are you okay?**

## Features

- **Dashboard**: Visual overview of income, spending, alerts, and subscriptions
- **Smart Categorization**: Automatic transaction categorization with manual override
- **Subscription Detection**: Finds recurring charges and separates subscriptions from utility bills
- **Alerts**: Detects duplicate charges, unusual amounts, price increases
- **Date Filtering**: View any time period (month, quarter, year, or custom range)
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

# 3. Configure
cp _env.example .env
# Edit .env with your SimpleFIN access URL

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
cp _env.example .env
# Edit .env with your SimpleFIN access URL

# 2. Build & run
docker compose build
docker compose run --rm fin sync
docker compose run --rm fin web
```

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
- Respects date range selection

### Subscriptions & Bills
- **Subscriptions**: Netflix, Spotify, software services
- **Bills**: Electric, gas, internet (utility services)
- Click the type badge to change between Subscription/Bill
- Click "View All" to see the dedicated Recurring page

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

## CLI Commands

### Everyday Use

| Command | Description |
|---------|-------------|
| `fin sync` | Pull latest transactions from bank |
| `fin web` | Start the web dashboard |
| `fin status` | Financial status at a glance (CLI) |
| `fin trend` | Monthly trend over time |

### Drill Down

| Command | Description |
|---------|-------------|
| `fin drill recurring` | All recurring expenses |
| `fin drill one-offs` | Discretionary spending |
| `fin drill alerts` | All alerts with details |
| `fin drill income` | Income sources |

### Export

| Command | Description |
|---------|-------------|
| `fin export-csv` | Export all data to CSV files |

## Manual Overrides

### Changing Categories

When the auto-categorization is wrong:

1. Go to Dashboard > Spending by Category
2. Click the category to expand it
3. Find the miscategorized merchant
4. Click the pencil icon
5. Select the correct category

Your choice is saved and applied to all future transactions from that merchant.

### Changing Subscription/Bill Type

1. Go to Dashboard or Recurring page
2. Find the item in the table
3. Click the type badge (blue "Sub" or yellow "Bill")
4. It toggles to the other type

Example: If "AT&T" shows as a Subscription but it's your phone bill, click to change it to Bill.

## Date Range Selection

The dashboard respects date selection across all sections:

- **Period buttons**: Month, Quarter, Year
- **Custom dates**: Use the date picker for any range

All sections update: Income, Spending, Alerts, Categories.

## Key Concepts

**Baseline** = Income - Recurring Expenses

- Positive baseline: Your lifestyle costs less than you earn
- Negative baseline: Spending exceeds income (unsustainable)

**Net** = Baseline - One-off Spending

- This is your actual savings (or loss) for the period

**Subscriptions** = Recurring charges for optional services (streaming, software)

**Bills** = Recurring charges for essential utilities (electric, water, internet)

## Getting SimpleFIN

1. Go to [SimpleFIN Bridge](https://beta-bridge.simplefin.org/)
2. Subscribe (~$1.50/month) and link your bank accounts
3. Copy your Setup Token (base64-encoded string)
4. Run `fin setup YOUR_SETUP_TOKEN` to claim your Access URL
5. Add the Access URL to your `.env` file as `SIMPLEFIN_ACCESS_URL`

See [docs/SIMPLEFIN_SETUP.md](docs/SIMPLEFIN_SETUP.md) for detailed instructions.

## Configuration

Create a `.env` file:

```bash
# Required
SIMPLEFIN_ACCESS_URL=https://your-access-url-from-simplefin

# Optional
FIN_DB_PATH=data/fin.db  # Default database location
```

## Data Storage

- All data stored locally in `./data/fin.db` (SQLite)
- No cloud services beyond SimpleFIN
- No tracking, no ads
- You own your data

## Troubleshooting

### "No transactions found"
Run `fin sync --full` to pull more history.

### Categories are wrong
Click the category, then the pencil icon to override.

### Subscription showing as Bill (or vice versa)
Click the type badge to toggle.

### Alerts not updating with date range
Make sure you're selecting a date range, not just changing period type.

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
