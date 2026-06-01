---
name: telegram-notion-crm
description: Sync Telegram conversations (DMs + groups) into a Notion CRM database. Uses Claude to extract summaries, key points, and action items from each chat.
homepage: https://github.com/your-username/telegram-notion-crm
---

# telegram-notion-crm

Reads your Telegram account (not a bot — real MTProto user session), summarizes conversations with Claude, and writes structured contact + interaction records into Notion.

## How it works

```
uv run sync.py  (or cron daily at 8am)
  │
  ├─ Telegram MTProto  →  dialogs active in last N days
  │                         └─ up to 50 text messages per chat
  │
  ├─ Claude (claude-sonnet-4-6)
  │    transcript  →  { summary, key_points, action_items }
  │
  └─ Notion
       ├─ Contacts DB   →  upsert contact (deduped by name)
       └─ Interactions DB →  append interaction page (linked to contact)
```

## Setup

### 1. Clone and install

```bash
git clone https://github.com/your-username/telegram-notion-crm
cd telegram-notion-crm
uv sync
```

### 2. Configure credentials

```bash
cp .env.example .env
# edit .env — see below for where to get each value
```

| Variable | Where to get it |
|----------|----------------|
| `TELEGRAM_API_ID` | https://my.telegram.org/apps → create app |
| `TELEGRAM_API_HASH` | same page |
| `TELEGRAM_PHONE` | your phone in `+12025551234` format |
| `NOTION_TOKEN` | https://www.notion.so/my-integrations → create integration |
| `NOTION_CONTACTS_DB_ID` | Notion DB URL: `notion.so/workspace/<THIS_PART>?v=...` |
| `NOTION_INTERACTIONS_DB_ID` | same, for the Interactions DB |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |

### 3. Create Notion databases

**Contacts** — property names are case-sensitive:

| Property | Type | Notes |
|----------|------|-------|
| Name | Title | |
| Username | Rich Text | |
| Source | Select | options: `Telegram DM`, `Telegram Group` |
| First Seen | Date | |
| Last Contacted | Date | |
| Notes | Rich Text | |

**Interactions** — must have a Relation back to Contacts:

| Property | Type | Notes |
|----------|------|-------|
| Name | Title | |
| Contact | Relation → Contacts | |
| Date | Date | |
| Chat | Rich Text | |
| Summary | Rich Text | |
| Kind | Select | options: `DM`, `Group` |

Then: share each database with your integration (click `...` → Connections → add integration).

### 4. First run (Telegram auth)

```bash
uv run sync.py
```

Telethon will prompt once for phone + SMS code (+ 2FA password if enabled). It saves `crm_session.session` — never commit this file.

### 5. Daily cron

```cron
0 8 * * * cd /path/to/telegram-notion-crm && uv run sync.py >> logs/sync.log 2>&1
```

## CLI reference

```bash
uv run cli.py sync [--days N]        # run sync (override days window)
uv run cli.py contacts [--limit N]   # list contacts table
uv run cli.py contact "Full Name"    # show one contact's interaction history
uv run cli.py chats [--days N]       # preview what would be synced
```

## Project structure

```
telegram-notion-crm/
├── config.py       ← loads .env, fails loudly on missing vars
├── telegram.py     ← Telethon: auth singleton, dialogs, messages
├── notion.py       ← Notion: upsert contacts, log interactions, queries
├── claude.py       ← Anthropic: transcript → JSON extraction
├── sync.py         ← orchestrator (cron target)
├── cli.py          ← Click CLI wrapping all commands
├── pyproject.toml
├── .env.example
└── SETUP.md        ← detailed setup guide
```

## Using with Claude Code

Open the project directory in Claude Code. The `.claude/commands/crm.md` slash command is included — type `/crm` to get contextual help managing, debugging, or extending the CRM.

## Security notes

- `crm_session.session` grants full Telegram account access — keep it out of git (already in `.gitignore`)
- `.env` is gitignored — never commit it
- The sync reads messages only — it never sends messages or modifies Telegram data
