# Teleon — Telegram → Notion CRM

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/managed%20by-uv-purple.svg)](https://docs.astral.sh/uv/)

Sync your Telegram conversations into a structured Notion CRM. Tracks contacts, logs interactions, surfaces follow-ups, and lets you search across your entire message history — all from the terminal.

```
████████╗███████╗██╗     ███████╗ ██████╗ ███╗   ██╗
   ██╔══╝██╔════╝██║     ██╔════╝██╔═══██╗████╗  ██║
   ██║   █████╗  ██║     █████╗  ██║   ██║██╔██╗ ██║
   ██║   ██╔══╝  ██║     ██╔══╝  ██║   ██║██║╚██╗██║
   ██║   ███████╗███████╗███████╗╚██████╔╝██║ ╚████║
   ╚═╝   ╚══════╝╚══════╝╚══════╝ ╚═════╝ ╚═╝  ╚═══╝
```

---

## What it does

- Reads your Telegram DMs, groups, and channels using your own account (not a bot)
- Enriches each contact with NLP: sentiment, language, entities, dates, topics
- Optionally summarises conversations with AI (Claude, Gemini, OpenAI, or Claude Code CLI — free)
- Writes structured records to three Notion databases: Contacts, Interactions, Follow-ups
- Full-text search across all tracked chats with context window and entity extraction
- Automated sync via Windows Task Scheduler or cron

---

## Requirements

- Python 3.11–3.13 (3.14 not supported — spaCy incompatibility)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) package manager
- A Telegram account
- A Notion account (free tier works)

---

## Installation

### 1. Install uv

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone the repo

```bash
git clone https://github.com/your-username/teleon.git
cd teleon
```

### 3. Install dependencies

```bash
uv sync
```

This creates a `.venv` and installs everything including spaCy, Telethon, Rich, and Notion client.

### 4. Download the spaCy language model

```bash
uv run python -m spacy download en_core_web_sm
```

---

## Configuration

### Get your Telegram API credentials

Telegram requires API credentials to use MTProto (the protocol that lets you read your own messages).

1. Go to **https://my.telegram.org/apps** and log in with your phone number
2. Click **"Create new application"**
3. Fill in any app name and short name (e.g. `teleon` / `teleon`) — platform doesn't matter
4. You'll see:
   - **App api_id** — a number like `12345678`
   - **App api_hash** — a 32-character hex string

> These credentials are tied to your account and stay private in your `.env` file. Never commit them.

### Set up a Notion integration

1. Go to **https://www.notion.so/my-integrations** and click **"New integration"**
2. Give it a name (e.g. `Teleon`), associate it with your workspace, and save
3. Copy the **Internal Integration Token** (starts with `ntn_` or `secret_`)

The rest — creating databases, setting up schema, and connecting everything — is handled automatically by `teleon setup`.

### Run the setup wizard

```bash
uv run teleon setup
```

The wizard will:
- Prompt for all credentials
- Validate your Telegram connection (may ask for a login code sent to your Telegram app)
- Validate each Notion database
- Auto-discover and fill in the Data Source IDs
- Ask if you want to schedule automatic daily sync
- Write a clean `.env` file

If you prefer to configure manually, copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

---

## Running Teleon

### First sync

```bash
teleon sync
```

Or using uv directly (if `teleon` isn't on PATH yet):
```bash
uv run teleon sync
```

On first run it will discover your active chats and add them to the pending list. Review and approve:

```bash
teleon chats pending       # see what was found
teleon chats approve --all # approve everything, or approve individually
teleon sync                # sync again now that chats are tracked
```

---

## Command Reference

### Sync

```bash
teleon sync                  # sync all tracked chats (default: last 1 day)
teleon sync --days 30        # sync last 30 days
```

### Search

```bash
teleon search "pricing"
teleon search "funding" --days 30
teleon search "deal" --type dm            # DMs only
teleon search "token" --chat "CryptoJobs" # specific chat
teleon search "BD" --sender "@alice"      # filter by sender
teleon search "demo" --context 10         # show 10 messages around each hit
teleon search "contract" --ai             # run AI analysis on results
teleon search "intro" --save              # save hits as Notion Follow-ups
teleon search "leads" --export leads.json # export results to file
teleon search "funding" --raw             # flat results, no context
```

### Contacts

```bash
teleon contacts              # list all contacts (sorted by last contacted)
teleon contacts --limit 50
teleon contact "Alice"       # show Alice's interaction history
```

### Chat management

```bash
teleon scan                  # preview all active Telegram chats
teleon scan --days 7 --dms-only
teleon chats list            # show tracked chats
teleon chats pending         # show chats waiting for approval
teleon chats approve "Name"  # approve one chat
teleon chats approve --all   # approve all pending
teleon chats add "Name"      # find and track a chat by name
teleon chats pause "Name"    # skip during sync (keep in registry)
teleon chats resume "Name"   # re-enable a paused chat
teleon chats ignore "Name"   # permanently exclude a chat
```

### Contact enrichment

```bash
teleon enrich "Alice"                          # web + LinkedIn search
teleon enrich "Alice" --source github          # specific source
teleon enrich "Alice" --source custom --query "Alice Chen Acme Corp"
teleon enrich --all                            # enrich all contacts
```

### Automated sync

```bash
teleon schedule setup                    # daily at 08:00
teleon schedule setup --time 09:30       # custom time
teleon schedule setup --every 6          # every 6 hours
teleon schedule show                     # last/next run info
teleon schedule remove                   # remove schedule
```

On **Windows** this creates a Windows Task Scheduler task. On **macOS/Linux** it adds a cron entry.

---

## AI Providers (optional)

Teleon can use AI to generate conversation summaries and relationship signals. Set `AI_PROVIDER` in `.env`:

| Provider | Value | Key needed |
|----------|-------|-----------|
| Claude (Anthropic API) | `claude` | `ANTHROPIC_API_KEY` |
| Gemini | `gemini` | `GEMINI_API_KEY` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| Claude Code CLI | `cli` | None — uses your local Claude Code install |
| None (NLP only) | `none` | — |

**Free option:** If you have [Claude Code](https://claude.ai/code) installed, set `AI_PROVIDER=cli` and no API key is needed.

---

## Sync behaviour settings

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNC_DAYS_BACK` | `1` | How many days of history to fetch per sync |
| `SYNC_MAX_MESSAGES_PER_CHAT` | `50` | Message cap per chat per sync |
| `SYNC_DMS_ONLY` | `false` | If true, skips groups and channels |
| `AUTO_TRACK_NEW_CHATS` | `false` | Auto-approve newly discovered chats |
| `SYNC_DELAY_BETWEEN_CHATS` | `1.5` | Seconds between chats (flood wait protection) |

---

## Project structure

```
teleon/
├── teleon.py          — CLI entry point (all commands)
├── telegram.py        — Telegram client (Telethon, MTProto)
├── notion.py          — Notion write/read layer (v3 API)
├── sync.py            — Main sync pipeline
├── enricher.py        — Web/LinkedIn contact enrichment
├── ai.py              — AI provider abstraction
├── nlp.py             — Local NLP (spaCy, VADER, dateparser, rapidfuzz)
├── search_intel.py    — Search intelligence (query expansion, scoring, affinity)
├── tracker.py         — Chat registry (tracked_chats.json)
├── config.py          — Env var loader
├── setup_notion.py    — Notion schema setup
├── pyproject.toml     — Dependencies (managed by uv)
├── .env.example       — Config template
├── .env               — Your credentials (gitignored)
└── logs/              — Sync logs, failed writes, search history (gitignored)
```

---

## Security notes

The following are gitignored and should never be committed:

- `.env` — contains your Telegram API keys and Notion token
- `crm_session.session` — your Telegram session file (grants full account access)
- `tracked_chats.json` — contains personal contact names and IDs
- `logs/` — may contain message excerpts
- `data/` — contains learned search affinity data

---

## Troubleshooting

**`FloodWaitError` during sync**
Telegram rate-limits heavy requests. Teleon waits out the full flood period with a live countdown and retries automatically. Increase `SYNC_DELAY_BETWEEN_CHATS` in `.env` to reduce frequency.

**`spaCy` install fails**
Python 3.14 is not supported. Run `uv python pin 3.13` then `uv sync` to force 3.13.

**Notion writes succeed but reads return nothing**
notion-client v3 uses `data_sources.query` instead of `databases.query`. The Data Source IDs are different from Database IDs. Run `teleon setup` to auto-discover and fill them in.

**`teleon` command not found**
Run `uv sync` first to register the script entry point. Then use `uv run teleon <command>` or activate the venv with `.venv\Scripts\activate` (Windows) / `source .venv/bin/activate` (Unix).

**Telegram login code not arriving**
Make sure the phone number in `.env` is in international format (e.g. `+911234567890`). The code is sent to your Telegram app, not by SMS.
