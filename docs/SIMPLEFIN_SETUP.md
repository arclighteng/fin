# SimpleFIN Setup Guide

SimpleFIN is a service that securely connects to your bank accounts and provides read-only access to your transaction data. This guide explains how to set up SimpleFIN with fin.

## Step 1: Create a SimpleFIN Account

1. Go to [SimpleFIN Bridge](https://beta-bridge.simplefin.org/)
2. Click "Get Started" and subscribe (~$1.50/month)
3. Follow the prompts to connect your bank accounts
4. After setup, you'll receive a **Setup Token** - a long base64-encoded string

## Step 2: Claim Your Access URL

SimpleFIN uses a two-step authentication process for security. The Setup Token you received is a one-time claim code that must be exchanged for your permanent Access URL. (See [SimpleFIN Developer Guide](https://beta-bridge.simplefin.org/info/developers) for technical details.)

**Option A: Use the CLI (Recommended)**

```bash
fin setup YOUR_SETUP_TOKEN_HERE
```

This will print your Access URL. Copy it for the next step.

**Option B: Manual claim**

If you already have an Access URL from SimpleFIN (looks like `https://user:pass@beta-bridge.simplefin.org/simplefin`), skip to Step 3.

## Step 3: Configure fin

1. Create a `.env` file in your fin project directory (if it doesn't exist)

2. Add your SimpleFIN Access URL:
   ```
   SIMPLEFIN_ACCESS_URL=https://YOUR_USERNAME:YOUR_PASSWORD@beta-bridge.simplefin.org/simplefin
   ```

3. Verify the `.env` file is in your `.gitignore` (it should be by default)

> **Important**: This URL contains your credentials. Never share it or commit it to version control.

## Step 3: Run Your First Sync

Test that everything is working:

```bash
fin status
```

This will show if SimpleFIN can connect successfully.

Then run your first sync:

```bash
fin sync --full
```

The `--full` flag fetches 120 days of history. This is recommended for the first sync.

## Sync Options

| Command | Lookback | Use Case |
|---------|----------|----------|
| `fin sync --quick` | 14 days | Daily syncs |
| `fin sync` | 30 days | Weekly syncs (default) |
| `fin sync --full` | 120 days | After being away, or first sync |
| `fin sync --annual-bootstrap` | 400 days | January sync to catch annual subscriptions |

## Using the Web UI

Once configured, you can also sync from the web interface:

1. Start the web server: `fin web`
2. Open http://127.0.0.1:8000
3. Click the **Sync** button in the navigation bar

The sync status (last sync time and data range) is displayed next to the button.

## Troubleshooting

### "Missing SIMPLEFIN_ACCESS_URL"

The `.env` file is missing or doesn't contain the access URL. Make sure:
- The `.env` file exists in your project directory
- It contains `SIMPLEFIN_ACCESS_URL=https://...`
- There are no extra spaces around the `=` sign

### Sync returns 0 transactions

- Check that your bank accounts are still connected in SimpleFIN
- Try running `fin sync --full` to fetch more history
- Verify the Access URL hasn't expired (you may need to regenerate it)

### Connection errors

- Check your internet connection
- SimpleFIN may be temporarily unavailable; try again later
- The Access URL may have expired; regenerate it at beta-bridge.simplefin.org

## Security Notes

- Your Access URL contains credentials - treat it like a password
- The `.env` file should never be committed to git
- fin only stores transaction data locally in SQLite (`data/fin.db`)
- No data is sent to external servers (except SimpleFIN for fetching)
