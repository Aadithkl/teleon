# Telegram → Notion CRM Setup Guide

## Prerequisites

- Python 3.11+
- `uv` package manager ([install](https://docs.astral.sh/uv/))
- A Telegram account
- A Notion workspace with an integration token
- An Anthropic API key

---

## Step 1: Install Dependencies

```bash
cd crm
uv sync
```

---

## Step 2: Configure Credentials

```bash
cp .env.example .env
```

Edit `.env` with your real values:

### Telegram API credentials
1. Go to https://my.telegram.org/apps
2. Create a new application
3. Copy `App api_id` → `TELEGRAM_API_ID`
4. Copy `App api_hash` → `TELEGRAM_API_HASH`
5. Set `TELEGRAM_PHONE` to your phone number in international format (e.g. `+12025551234`)

### Notion integration
1. Go to https://www.notion.so/my-integrations
2. Create a new integration, copy the token → `NOTION_TOKEN`
3. Create two databases in Notion: **Contacts** and **Interactions**
   - See schemas in sections 5A and 5B of the implementation plan
4. Share each database with your integration (click "..." → "Connections" → add integration)
5. Copy each database ID from the URL → `NOTION_CONTACTS_DB_ID` and `NOTION_INTERACTIONS_DB_ID`
   - URL format: `notion.so/<workspace>/<DATABASE_ID>?v=...`

### Anthropic API key
1. Get your key from https://console.anthropic.com
2. Set `ANTHROPIC_API_KEY`

---

## Step 3: Notion Database Schemas

Create the following properties in each database exactly as specified (names are case-sensitive):

### Contacts Database

| Property | Type |
|----------|------|
| Name | Title (default) |
| Username | Rich Text |
| Source | Select |
| First Seen | Date |
| Last Contacted | Date |
| Notes | Rich Text |

For **Source**, add options: `Telegram DM`, `Telegram Group`

### Interactions Database

| Property | Type |
|----------|------|
| Name | Title (default) |
| Contact | Relation → Contacts database |
| Date | Date |
| Chat | Rich Text |
| Summary | Rich Text |
| Kind | Select |

For **Kind**, add options: `DM`, `Group`

---

## Step 4: Validate Config

```bash
uv run config.py
```

Should print all config values without errors.

---

## Step 5: First Run (Telegram Auth)

```bash
uv run sync.py
```

On first run, Telethon will prompt:
1. `Please enter your phone (or bot token):` → enter your phone number
2. `Please enter the code you received:` → enter the SMS/app code
3. If 2FA is enabled: `Please enter your password:`

A `crm_session.session` file is created. All future runs are silent.

**Keep this file secure — it grants full access to your Telegram account.**

---

## Step 6: CLI Commands

```bash
# Run a sync
uv run cli.py sync

# Sync last 3 days
uv run cli.py sync --days 3

# List contacts
uv run cli.py contacts

# View a contact's history
uv run cli.py contact "John Smith"

# Preview active chats
uv run cli.py chats
```

---

## Step 7: Cron Setup (Daily Sync at 8 AM)

```bash
crontab -e
```

Add this line (replace `/absolute/path/to/crm` with your actual path):

```cron
0 8 * * * cd /absolute/path/to/crm && uv run sync.py >> logs/sync.log 2>&1
```

Verify:
```bash
crontab -l
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `ValueError: Missing required environment variable: X` | Add `X` to `.env` |
| `Check NOTION_CONTACTS_DB_ID in .env` | Wrong DB ID, or integration not shared with the database |
| Telegram auth loop | Delete `crm_session.session` and re-run |
| `FloodWaitError` | Telegram rate-limited; the sync will skip that chat and continue |
| Claude returns "Could not parse" | Check `ANTHROPIC_API_KEY`; raw response logged to stderr |
