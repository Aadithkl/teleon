# /crm — Telegram → Notion CRM assistant

You are helping manage, run, debug, or extend the CRM at `C:\Users\Aadith\crm`.

## Pipeline (in order)

```
telegram.get_recent_dialogs(days_back, dms_only)
    ↓  all active dialogs from user's Telegram account
tracker.status(dialog_id)
    ↓  filter: tracked → sync | pending/paused/ignored → skip | unknown → add to pending
telegram.get_messages(dialog_id, limit, days_back)
    ↓  list of {date, sender, text}
claude.extract_crm_data(chat_name, messages)          [Anthropic API]
    ↓  {summary, key_points, action_items}
nlp.enrich(messages)                                   [100% local]
    ↓  {sentiment, entities, dates, contact_info}
notion.upsert_contact() + notion.log_interaction()
    ↓  Notion Contacts + Interactions databases
```

## Module map

| File | Role | Key functions |
|------|------|---------------|
| `telegram.py` | Read Telegram | `get_recent_dialogs()`, `get_messages()` |
| `tracker.py` | Registry R/W | `status()`, `approve()`, `pause()`, `ignore()`, `add_pending()` |
| `nlp.py` | Local enrichment | `enrich()`, `sentiment_score()`, `extract_entities()`, `extract_dates()`, `extract_contact_info()`, `fuzzy_find_contact()` |
| `claude.py` | AI extraction | `extract_crm_data()` |
| `notion.py` | Notion write | `upsert_contact()`, `log_interaction()`, `find_contact()` |
| `sync.py` | Orchestrator | `main()` — cron target |
| `teleon.py` | CLI | sync, scan, contacts, contact, chats * |

## CLI commands

```bash
teleon sync [--days N]
teleon scan [--days N] [--dms-only]      # raw Telegram + registry status
teleon chats list
teleon chats pending
teleon chats approve "Name" / --all
teleon chats add "Name"
teleon chats pause "Name"
teleon chats resume "Name"
teleon chats ignore "Name"
teleon contacts [--limit N]
teleon contact "Name"
```

## NLP functions (nlp.py) — no API cost

```python
nlp.enrich(messages)                         # → {sentiment, entities, dates, contact_info}
nlp.sentiment_score(texts)                   # → {label, compound, breakdown}
nlp.extract_entities(texts)                  # → {persons, orgs, locations}  — spaCy
nlp.extract_dates(texts)                     # → ["2026-06-05", ...]         — dateparser
nlp.extract_contact_info(texts)              # → {emails, phones, urls, domains} — regex
nlp.fuzzy_find_contact(name, candidates)     # → (match, score)              — rapidfuzz
```

## Tracker statuses

```
tracked  → synced every run
pending  → detected, awaiting: teleon chats approve "Name"
paused   → skipped temporarily
ignored  → permanent skip
unknown  → first time seen, queued as pending
```

## Key constraints

- `telegram.py` is async; `notion.py`, `nlp.py`, `claude.py` are synchronous
- `sync.py` owns the event loop — use `asyncio.run(main())` from CLI
- `upsert_contact()` uses rapidfuzz for deduplication (85% threshold)
- `log_interaction()` is append-only — never deduplicates interactions
- Claude model: `claude-sonnet-4-6` (no date suffix)
- Always `$env:PYTHONUTF8 = "1"` in PowerShell (Unicode chat names)
- `tracked_chats.json`, `.env`, `*.session` are gitignored — never commit
