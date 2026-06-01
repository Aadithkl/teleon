# /crm — Telegram → Notion CRM assistant

You are helping the user manage, run, debug, or extend their `telegram-notion-crm` project.

## Project overview

This is a Python CLI tool (uv, Python 3.11+) that:
1. Reads Telegram DMs + group chats via Telethon (MTProto user session — not a bot)
2. Sends each conversation transcript to Claude for structured extraction
3. Writes Contact + Interaction records to two Notion databases

## Key files

| File | Role |
|------|------|
| `config.py` | Loads `.env`, exposes typed constants, raises `ValueError` on missing vars |
| `telegram.py` | `get_client()` singleton, `get_recent_dialogs()`, `get_messages()` |
| `notion.py` | `upsert_contact()`, `log_interaction()`, `list_contacts()`, `get_interactions_for_contact()` |
| `claude.py` | `extract_crm_data()` — builds transcript, calls Claude, parses JSON |
| `sync.py` | Orchestrator — runs the full pipeline, entry point for cron |
| `cli.py` | Click commands: `sync`, `contacts`, `contact`, `chats` |

## Running commands

```bash
uv run sync.py                       # full sync
uv run cli.py sync --days 3          # sync last 3 days
uv run cli.py contacts               # list all contacts
uv run cli.py contact "Name"         # show one contact's history
uv run cli.py chats                  # preview active dialogs
uv run config.py                     # validate .env is loaded correctly
```

## Common tasks you can help with

- **Debug a sync error**: read `logs/sync.log` or the stderr output, trace back to which module failed
- **Extend the schema**: if the user wants to add Notion properties (e.g. "Company", "Tags"), update `notion.py` upsert functions and re-check property name casing
- **Change the Claude prompt**: it's in `claude.py` — the `extract_crm_data()` function
- **Add a CLI command**: use Click in `cli.py`, follow the existing pattern
- **Test a single module**: run inline with `uv run python -c "import asyncio; from telegram import get_recent_dialogs; ..."`
- **Set up cron**: the line goes in `crontab -e` — path must be absolute, output to `logs/sync.log`

## Credentials (never in code)

All secrets live in `.env` only. The variables are:
`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE`,
`NOTION_TOKEN`, `NOTION_CONTACTS_DB_ID`, `NOTION_INTERACTIONS_DB_ID`,
`ANTHROPIC_API_KEY`, `SYNC_DAYS_BACK`, `SYNC_MAX_MESSAGES_PER_CHAT`

## Important constraints

- `telegram.py` is async (Telethon). `notion.py` and `claude.py` are synchronous.
- `sync.py` owns the event loop — use `asyncio.run(main())` from CLI, never nest loops.
- `upsert_contact()` deduplicates by exact name match — two syncs of the same person = one Contact record.
- `log_interaction()` is append-only — always creates a new Interaction page.
- Claude model string: `claude-sonnet-4-6` (no date suffix).
- Session file `crm_session.session` must never be committed.

When the user asks what to do, suggest the exact `uv run` command or the file + line number to edit.
