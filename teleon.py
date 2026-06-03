import asyncio
import importlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import click
import notion_client
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

import ai as ai_module
import config
import notion
import search_intel
import telegram
import tracker
import enricher
import sync as sync_module

console = Console()
ENV_FILE = Path(".env")
ENV_EXAMPLE = Path(".env.example")
SEARCH_LOG = Path("logs/search_history.jsonl")

_BANNER = """\
████████╗███████╗██╗     ███████╗ ██████╗ ███╗   ██╗
   ██╔══╝██╔════╝██║     ██╔════╝██╔═══██╗████╗  ██║
   ██║   █████╗  ██║     █████╗  ██║   ██║██╔██╗ ██║
   ██║   ██╔══╝  ██║     ██╔══╝  ██║   ██║██║╚██╗██║
   ██║   ███████╗███████╗███████╗╚██████╔╝██║ ╚████║
   ╚═╝   ╚══════╝╚══════╝╚══════╝ ╚═════╝ ╚═╝  ╚═══╝"""


def _print_banner() -> None:
    if not sys.stdout.isatty():
        return
    console.print()
    for line in _BANNER.splitlines():
        console.print(f"  [bold cyan]{line}[/bold cyan]")
    console.print()
    console.print("  [dim]Telegram → Notion CRM[/dim]")
    console.print()


@click.group()
def cli():
    pass


# ─── sync ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--days", default=None, type=int, help="Days of history to sync")
def sync(days: int | None):
    """Run a full CRM sync from Telegram to Notion."""
    if days is not None:
        config.SYNC_DAYS_BACK = days
    asyncio.run(sync_module.main())


# ─── setup ─────────────────────────────────────────────────────────────────────

def _read_current_env() -> dict:
    """Read existing .env if it exists, return dict of current values."""
    if not ENV_FILE.exists():
        return {}
    current = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        current[key.strip()] = val.strip()
    return current


def _bool_to_yn(val: str) -> str:
    return "y" if val.lower() in ("true", "1", "yes") else "n"


def _yn_to_bool(val: str) -> bool:
    return val.strip().lower() in ("y", "yes", "true", "1")


def _prompt_str(label: str, key: str, current: dict, help_text: str = "",
                default_raw: str = "") -> str:
    """Prompt for a string value, showing current as default."""
    default = current.get(key) or default_raw
    prompt = f"{label}"
    if help_text:
        prompt += f"\n  [{help_text}]"
    prompt += "\n  "
    if default:
        return click.prompt(prompt, default=default, show_default=True, type=str)
    return click.prompt(prompt, default="", show_default=False, type=str)


def _write_env(values: dict):
    """Write formatted .env file."""
    parts = []

    parts.append("# ── Telegram (required) ───────────────────────────────────────────────────────")
    parts.append("# From: https://my.telegram.org/apps")
    parts.append(f"TELEGRAM_API_ID={values['TELEGRAM_API_ID']}")
    parts.append(f"TELEGRAM_API_HASH={values['TELEGRAM_API_HASH']}")
    parts.append(f"TELEGRAM_PHONE={values['TELEGRAM_PHONE']}")
    parts.append("")

    parts.append("# ── AI provider (optional) ────────────────────────────────────────────────────")
    parts.append("# Options: claude | gemini | openai | cli | none")
    parts.append(f"AI_PROVIDER={values['AI_PROVIDER']}")
    if values.get("AI_MODEL"):
        parts.append(f"AI_MODEL={values['AI_MODEL']}")
    else:
        parts.append("# AI_MODEL=")
    if values.get("AI_API_KEY"):
        parts.append(f"AI_API_KEY={values['AI_API_KEY']}")
    else:
        parts.append("# AI_API_KEY=")
    parts.append("")

    if values.get("NOTION_TOKEN"):
        parts.append("# ── Notion (optional) ─────────────────────────────────────────────────────────")
        parts.append("# Integration token: https://www.notion.so/my-integrations")
        parts.append(f"NOTION_TOKEN={values['NOTION_TOKEN']}")
        parts.append(f"NOTION_CONTACTS_DB_ID={values['NOTION_CONTACTS_DB_ID']}")
        parts.append(f"NOTION_INTERACTIONS_DB_ID={values['NOTION_INTERACTIONS_DB_ID']}")
        parts.append(f"NOTION_FOLLOWUPS_DB_ID={values['NOTION_FOLLOWUPS_DB_ID']}")
        parts.append("")

    parts.append("# ── Sync behaviour ────────────────────────────────────────────────────────────")
    parts.append(f"SYNC_DAYS_BACK={values['SYNC_DAYS_BACK']}")
    parts.append(f"SYNC_MAX_MESSAGES_PER_CHAT={values['SYNC_MAX_MESSAGES_PER_CHAT']}")
    parts.append(f"SYNC_DMS_ONLY={values['SYNC_DMS_ONLY']}")
    parts.append(f"AUTO_TRACK_NEW_CHATS={values['AUTO_TRACK_NEW_CHATS']}")
    parts.append(f"SYNC_DELAY_BETWEEN_CHATS={values['SYNC_DELAY_BETWEEN_CHATS']}")

    content = "\n".join(parts) + "\n"
    ENV_FILE.write_text(content, encoding="utf-8")


def _test_notion(token: str, db_ids: dict) -> list[str]:
    """Validate Notion connection and return list of ok/fail per DB."""
    results = []
    try:
        client = notion_client.Client(auth=token)
        me = client.users.me()
        results.append(f"Token owner: {me.get('name', '?')}")
    except Exception as e:
        return [f"Token invalid: {e}"]

    for label, db_id in db_ids.items():
        if not db_id:
            results.append(f"{label}: skipped (no ID)")
            continue
        try:
            db = client.databases.retrieve(database_id=db_id)
            results.append(f"{label}: OK ({len(db.get('properties', {}))} properties)")
        except Exception as e:
            results.append(f"{label}: error — {e}")

    return results


async def _test_telegram(api_id: int, api_hash: str, phone: str) -> list[str]:
    """Test Telegram connection. Returns status lines."""
    from telethon import TelegramClient
    from telethon.errors import FloodWaitError

    SESSION_FILE = "crm_session"
    results = []

    try:
        client = TelegramClient(SESSION_FILE, api_id, api_hash)
        await client.start(phone=phone)
        me = await client.get_me()
        if me:
            results.append(f"Connected as: @{me.username or '?'} ({me.first_name or '?'})")
        else:
            results.append("Connected but couldn't fetch user info")
        dialogs = []
        try:
            async for d in client.iter_dialogs(limit=5):
                dialogs.append(d.name or "?")
        except FloodWaitError as e:
            results.append(f"Flood wait on dialog list ({e.seconds}s) — skipped")
        await client.disconnect()
        if dialogs:
            results.append(f"Recent chats: {', '.join(dialogs[:3])}" + ("..." if len(dialogs) > 3 else ""))
        return results
    except Exception as e:
        results.append(f"Connection failed: {e}")
        return results


@cli.command()
def setup():
    """Interactive setup — prompts for all env vars, validates connections."""
    _print_banner()
    console.print(Panel.fit(
        "[bold cyan]Teleon Setup[/bold cyan]\n\n"
        "Configure your Telegram → Notion CRM.\n"
        "Press Enter to accept defaults shown in [brackets].",
        box=box.ROUNDED,
    ))

    current = _read_current_env()
    if not current:
        console.print("[dim]No existing .env found — starting fresh.[/dim]\n")

    # ── collect values ────────────────────────────────────────────────────
    api_id = _prompt_str("Telegram API ID", "TELEGRAM_API_ID", current,
                         "from https://my.telegram.org/apps")
    api_hash = _prompt_str("Telegram API Hash", "TELEGRAM_API_HASH", current,
                           "from https://my.telegram.org/apps")
    phone = _prompt_str("Phone (+countrycode)", "TELEGRAM_PHONE", current,
                        "e.g. +12025551234")
    provider = _prompt_str("AI Provider", "AI_PROVIDER", current,
                           "claude | gemini | openai | cli | none")
    api_key = _prompt_str("AI API Key (blank if using cli/none)", "AI_API_KEY", current,
                          "only needed for claude/gemini/openai")
    notion_token = _prompt_str("Notion Token (blank to skip Notion)", "NOTION_TOKEN", current,
                               "from https://www.notion.so/my-integrations")

    if notion_token:
        contacts_db = _prompt_str("Notion Contacts DB ID", "NOTION_CONTACTS_DB_ID", current)
        interactions_db = _prompt_str("Notion Interactions DB ID", "NOTION_INTERACTIONS_DB_ID", current)
        followups_db = _prompt_str("Notion Follow-ups DB ID", "NOTION_FOLLOWUPS_DB_ID", current)
    else:
        contacts_db = interactions_db = followups_db = ""

    days_back = _prompt_str("Sync days back", "SYNC_DAYS_BACK", current, default_raw="30")
    max_msgs = _prompt_str("Max messages per chat", "SYNC_MAX_MESSAGES_PER_CHAT", current, default_raw="50")
    dms_only_raw = _prompt_str("DMs only? (y/n)", "SYNC_DMS_ONLY", current, default_raw="n")
    auto_track_raw = _prompt_str("Auto-track new chats? (y/n)", "AUTO_TRACK_NEW_CHATS", current, default_raw="n")
    delay = _prompt_str("Delay between chats (seconds)", "SYNC_DELAY_BETWEEN_CHATS", current, default_raw="1.5")

    values = {
        "TELEGRAM_API_ID": api_id,
        "TELEGRAM_API_HASH": api_hash,
        "TELEGRAM_PHONE": phone,
        "AI_PROVIDER": provider or "none",
        "AI_MODEL": current.get("AI_MODEL", ""),
        "AI_API_KEY": api_key,
        "NOTION_TOKEN": notion_token,
        "NOTION_CONTACTS_DB_ID": contacts_db,
        "NOTION_INTERACTIONS_DB_ID": interactions_db,
        "NOTION_FOLLOWUPS_DB_ID": followups_db,
        "SYNC_DAYS_BACK": days_back,
        "SYNC_MAX_MESSAGES_PER_CHAT": max_msgs,
        "SYNC_DMS_ONLY": "true" if _yn_to_bool(dms_only_raw) else "false",
        "AUTO_TRACK_NEW_CHATS": "true" if _yn_to_bool(auto_track_raw) else "false",
        "SYNC_DELAY_BETWEEN_CHATS": delay,
    }

    # ── write .env ────────────────────────────────────────────────────────
    _write_env(values)
    console.print("\n[green]✓[/green] [bold].env written[/bold]")

    # ── test Notion ───────────────────────────────────────────────────────
    if notion_token:
        console.print("\n[cyan]Testing Notion connection...[/cyan]")
        db_ids = {
            "CRM Contacts": contacts_db,
            "CRM Interactions": interactions_db,
            "CRM Follow-ups": followups_db,
        }
        results = _test_notion(notion_token, db_ids)
        for r in results:
            icon = "[green]✓[/green]" if "OK" in r or "owner" in r else "[red]✗[/red]"
            console.print(f"  {icon} {r}")

        # run schema setup
        all_ok = all("OK" in r or "owner" in r or "skipped" in r for r in results)
        if all_ok:
            console.print("\n[cyan]Running Notion schema setup...[/cyan]")
            try:
                import setup_notion
                importlib.reload(setup_notion)
                setup_notion.main()
            except Exception as e:
                console.print(f"  [red]Schema setup failed: {e}[/red]")
    else:
        console.print("\n[dim]Notion: skipped (no token)[/dim]")

    # ── test Telegram ─────────────────────────────────────────────────────
    console.print(f"\n[cyan]Testing Telegram connection ({phone})...[/cyan]")
    console.print("[dim]If prompted, enter the login code sent to Telegram.[/dim]")
    try:
        tg_results = asyncio.run(_test_telegram(int(api_id), api_hash, phone))
        for r in tg_results:
            icon = "[green]✓[/green]" if "Connected" in r or "Recent" in r else "[yellow]![/yellow]"
            console.print(f"  {icon} {r}")
    except Exception as e:
        console.print(f"  [red]Telegram test failed: {e}[/red]")

    # ── summary ───────────────────────────────────────────────────────────
    console.print("\n" + "─" * 50)
    console.print("[bold green]Setup complete[/bold green]\n")

    summary_lines = [
        f"Telegram: {phone}",
        f"Notion:   {'configured' if notion_token else 'skipped'}",
        f"AI:       {provider or 'none'}",
    ]
    for line in summary_lines:
        console.print(f"  {line}")

    # ── optional: schedule automatic sync ────────────────────────────────
    console.print("")
    if click.confirm("Set up automatic daily sync?", default=False):
        run_time = click.prompt("  Sync time (HH:MM, 24h)", default="08:00")
        if sys.platform == "win32":
            ok, msg = _schedule_windows(run_time, None)
        else:
            ok, msg = _schedule_unix(run_time, None)
        if ok:
            console.print(f"[green]✓ Sync scheduled {msg}[/green]")
        else:
            console.print(f"[red]Schedule failed: {msg}[/red]")
            console.print(f"[dim]Try later: teleon schedule setup --time {run_time}[/dim]")
    else:
        console.print(f"[dim]Skip. Run 'teleon schedule setup' anytime to automate.[/dim]")

    console.print(f"\n[bold]Next steps:[/bold]")
    console.print(f"  [cyan]teleon sync[/cyan]              — run first sync")
    console.print(f"  [cyan]teleon scan[/cyan]              — preview active chats")
    console.print(f"  [cyan]teleon contacts[/cyan]          — view synced contacts")
    console.print(f"  [cyan]teleon schedule setup[/cyan]    — automate sync")
    console.print(f"  [cyan]teleon --help[/cyan]            — all commands")


# ─── contacts ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--limit", default=20, show_default=True, help="Max contacts to display")
def contacts(limit: int):
    """List Notion contacts sorted by most recently contacted."""
    rows = notion.list_contacts(limit=limit)
    table = Table(title="Contacts", show_header=True, header_style="bold cyan")
    table.add_column("Name")
    table.add_column("Username")
    table.add_column("Source")
    table.add_column("Last Contacted")
    for row in rows:
        table.add_row(row["name"], row["username"] or "", row["source"], row["last_contacted"])
    console.print(table)


@cli.command("contact")
@click.argument("name")
def contact_detail(name: str):
    """Show interaction history for a Notion contact (case-sensitive name)."""
    page = notion.find_contact(name)
    if not page:
        console.print(f"[red]Contact not found:[/red] {name}")
        sys.exit(1)
    interactions = notion.get_interactions_for_contact(page["id"])
    console.print(f"[bold]{name}[/bold] — {len(interactions)} interaction(s)")
    for i in interactions:
        console.print(f"  {i['date']}  {i['summary']}")


# ─── scan (raw Telegram view) ──────────────────────────────────────────────────

@cli.command()
@click.option("--days", default=None, type=int, help="Days back to check for activity")
@click.option("--dms-only", is_flag=True, default=False, help="Show only DMs, skip groups")
def scan(days: int | None, dms_only: bool):
    """Preview raw active Telegram dialogs (no registry filter)."""
    days_back = days if days is not None else config.SYNC_DAYS_BACK
    dms_only = dms_only or config.SYNC_DMS_ONLY

    async def _fetch():
        result = await telegram.get_recent_dialogs(days_back=days_back, dms_only=dms_only)
        await telegram.close_client()
        return result

    dialogs = asyncio.run(_fetch())

    table = Table(title=f"Active Chats (last {days_back} day(s))", show_header=True, header_style="bold cyan")
    table.add_column("Type")
    table.add_column("Name")
    table.add_column("Username")
    table.add_column("Last Active")
    table.add_column("Status")
    for d in dialogs:
        s = tracker.status(d["id"])
        status_color = {
            "tracked": "green", "pending": "yellow",
            "paused": "dim", "ignored": "red", "unknown": "cyan",
        }.get(s, "white")
        table.add_row(
            d["kind"].upper(),
            d["name"],
            d.get("username") or "",
            d["last_message_date"][:10],
            f"[{status_color}]{s}[/{status_color}]",
        )
    console.print(table)


# ─── chats group ───────────────────────────────────────────────────────────────

@cli.group()
def chats():
    """Manage the chat tracking registry (tracked_chats.json)."""
    pass


@chats.command("list")
def chats_list():
    """Show all tracked chats."""
    entries = tracker.get_tracked()
    if not entries:
        console.print("[dim]No tracked chats. Use 'chats approve' or 'chats add'.[/dim]")
        return
    table = Table(title="Tracked Chats", header_style="bold green")
    table.add_column("Kind")
    table.add_column("Name")
    table.add_column("Username")
    table.add_column("Added")
    for e in entries:
        table.add_row(e["kind"].upper(), e["name"], e.get("username") or "", e.get("added", ""))
    console.print(table)


@chats.command("pending")
def chats_pending():
    """Show new chats detected during sync, waiting for approval."""
    entries = tracker.get_pending()
    if not entries:
        console.print("[dim]No pending chats.[/dim]")
        return
    table = Table(title="Pending Chats", header_style="bold yellow")
    table.add_column("Kind")
    table.add_column("Name")
    table.add_column("Username")
    table.add_column("Detected")
    for e in entries:
        table.add_row(e["kind"].upper(), e["name"], e.get("username") or "", e.get("detected", ""))
    console.print(table)
    console.print(f"\n[dim]Run: teleon chats approve \"Name\" [bold]or[/bold] chats approve --all[/dim]")


@chats.command("approve")
@click.argument("name", required=False)
@click.option("--all", "approve_all", is_flag=True, default=False, help="Approve all pending chats")
def chats_approve(name: str | None, approve_all: bool):
    """Approve a pending chat (move to tracked)."""
    if approve_all:
        count = tracker.approve_all()
        console.print(f"[green]Approved {count} pending chat(s).[/green]")
    elif name:
        if tracker.approve(name):
            console.print(f"[green]Approved:[/green] {name}")
        else:
            console.print(f"[red]Not found in pending:[/red] {name}")
            sys.exit(1)
    else:
        console.print("[red]Provide a chat name or --all[/red]")
        sys.exit(1)


@chats.command("add")
@click.argument("name")
@click.option("--days", default=90, show_default=True, help="Days back to search for the chat")
def chats_add(name: str, days: int):
    """Find a chat on Telegram by name and add it directly to tracked."""
    async def _search():
        dialogs = await telegram.get_recent_dialogs(days_back=days)
        await telegram.close_client()
        return dialogs

    dialogs = asyncio.run(_search())

    # exact match first, then case-insensitive
    match = next((d for d in dialogs if d["name"] == name), None)
    if not match:
        match = next((d for d in dialogs if d["name"].lower() == name.lower()), None)

    if not match:
        console.print(f"[red]No chat named '{name}' found in the last {days} days.[/red]")
        console.print("[dim]Try --days 365 or check the name with: teleon scan[/dim]")
        sys.exit(1)

    s = tracker.status(match["id"])
    if s == "tracked":
        console.print(f"[yellow]Already tracked:[/yellow] {match['name']}")
        return
    if s == "ignored":
        console.print(f"[red]Chat is in ignored list.[/red] Use 'chats resume' first.")
        sys.exit(1)

    tracker.add_tracked(match)
    console.print(f"[green]Added to tracked:[/green] {match['name']} ({match['kind'].upper()})")


@chats.command("pause")
@click.argument("name")
def chats_pause(name: str):
    """Pause a tracked chat (skip during sync, keep in registry)."""
    if tracker.pause(name):
        console.print(f"[yellow]Paused:[/yellow] {name}")
    else:
        console.print(f"[red]Not found in tracked:[/red] {name}")
        sys.exit(1)


@chats.command("resume")
@click.argument("name")
def chats_resume(name: str):
    """Resume a paused chat."""
    if tracker.resume(name):
        console.print(f"[green]Resumed:[/green] {name}")
    else:
        console.print(f"[red]Not found in paused:[/red] {name}")
        sys.exit(1)


@chats.command("ignore")
@click.argument("name")
def chats_ignore(name: str):
    """Permanently ignore a chat (removes from all buckets, never re-adds)."""
    if tracker.ignore(name):
        console.print(f"[dim]Ignored:[/dim] {name}")
    else:
        console.print(f"[red]Not found:[/red] {name}")
        sys.exit(1)


# ─── search helpers ────────────────────────────────────────────────────────────

def _save_hits_to_notion(hits: list[dict], query: str) -> None:
    """Create one Notion Follow-up per unique (sender, chat) from search hits.

    Each hit dict must contain: sender, username, date, text, chat_name.
    """
    if not config.NOTION_ENABLED:
        console.print("[yellow]--save requires Notion configured (NOTION_TOKEN + DB IDs in .env).[/yellow]")
        return
    today = date.today().isoformat()
    seen: set[tuple[str, str]] = set()
    saved = 0
    for hit in hits:
        sender_name = hit.get("sender", "Unknown")
        chat_name = hit.get("chat_name", "")
        key = (sender_name, chat_name)
        if key in seen:
            continue
        seen.add(key)
        username_raw = (hit.get("username") or "").lstrip("@") or None
        try:
            contact_id = notion.upsert_contact(
                name=sender_name,
                username=username_raw,
                source=chat_name,
                last_message_date=hit.get("date", today),
            )
            notion.log_followup(
                contact_page_id=contact_id,
                task_name=f"Follow up: {query[:60]} — {sender_name}",
                due_date_iso=today,
                chat_name=chat_name,
                context=hit.get("text", "")[:2000],
            )
            saved += 1
        except Exception as e:
            console.print(f"  [red]Could not save {sender_name}: {e}[/red]")
    console.print(f"[green]Saved {saved} Follow-up(s) to Notion.[/green]")


# ─── search (context-aware + intelligent) ─────────────────────────────────────


@cli.command()
@click.argument("query")
@click.option("--chat", default=None, help="Restrict search to a specific chat name")
@click.option("--days", default=None, type=int, help="How far back to search (days)")
@click.option("--limit", default=5, show_default=True, help="Max hits per chat")
@click.option("--sender", default=None, help="Filter by sender: @username (API-level) or display name")
@click.option("--context", "ctx", default=5, show_default=True, help="Messages of context around each hit")
@click.option("--type", "chat_type", default=None, type=click.Choice(["dm", "group"]),
              help="Filter by chat type: dm or group")
@click.option("--ai", "use_ai", is_flag=True, default=False, help="Run AI analysis on results")
@click.option("--raw", is_flag=True, default=False, help="Skip context + intelligence layers")
@click.option("--save", is_flag=True, default=False, help="Save hits as Notion Follow-ups")
@click.option("--export", "export_file", default=None, metavar="FILE",
              help="Export results to a JSON file")
def search(query: str, chat: str | None, days: int | None, limit: int, sender: str | None,
           ctx: int, chat_type: str | None, use_ai: bool, raw: bool, save: bool,
           export_file: str | None):
    """Deep search across tracked Telegram chats.

    Finds matching messages and shows surrounding conversation context
    so you can read the full discussion around each result.

    \b
    Examples:
      teleon search "pricing"
      teleon search "polymarket" --context 10
      teleon search "BD" --chat "CryptoJobs" --days 30 --type group
      teleon search "funding" --ai --save
      teleon search "token" --sender "@alice" --export hits.json
    """
    entries = tracker.get_tracked()
    if not entries:
        console.print("[yellow]No tracked chats. Run 'teleon sync' first or add chats.[/yellow]")
        return

    if chat:
        matched = [e for e in entries if chat.lower() in e["name"].lower()]
        if not matched:
            console.print(f"[red]No tracked chat matching '{chat}'[/red]")
            console.print(f"[dim]Run 'teleon chats list' to see tracked chats.[/dim]")
            return
        entries = matched

    if chat_type:
        entries = [e for e in entries if e.get("kind") == chat_type]
        if not entries:
            console.print(f"[red]No tracked chats of type '{chat_type}'[/red]")
            return

    dialog_names = {e["id"]: e["name"] for e in entries}
    dialog_ids = list(dialog_names.keys())

    # ── intelligence store ─────────────────────────────────────────────
    intel_store: dict = {"chat_affinity": {}, "expansions": {}}
    intel_file = Path("data/search_intel.json")
    if intel_file.exists():
        try:
            intel_store = json.loads(intel_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            intel_store = {"chat_affinity": {}, "expansions": {}}

    expansions = intel_store.get("expansions", None)

    # ── query expansion ────────────────────────────────────────────────
    expanded = search_intel.expand_query(query, expansions) if not raw else [query]

    # ── sender: @username → API-level filter; display name → client-side
    api_from_user = sender.lstrip("@") if sender and sender.startswith("@") else None

    console.print(f"[bold cyan]Search[/bold cyan] \"{query}\" across {len(dialog_ids)} chat(s)" +
                  (f" [{chat_type}]" if chat_type else ""))
    if days:
        console.print(f"  Last {days} days, context ±{ctx}")
    if not raw and len(expanded) > 1:
        console.print(f"  [dim]Also searching: {', '.join(expanded[1:])}[/dim]")
    console.print("")

    # ── affinity-based chat ordering ───────────────────────────────────
    if not raw:
        ordered = search_intel.rank_chats(intel_store, [e["name"] for e in entries], query)
        entries.sort(key=lambda e: ordered.index(e["name"]) if e["name"] in ordered else 999)

    # ── raw mode ───────────────────────────────────────────────────────
    if raw:
        with console.status("[cyan]Searching...[/cyan]", spinner="dots"):
            async def _run_raw():
                all_r: list[dict] = []
                for term in expanded:
                    r = await telegram.search_messages(
                        query=term, dialog_ids=dialog_ids,
                        dialog_names=dialog_names, limit_per_chat=limit,
                        days_back=days, from_user=api_from_user,
                    )
                    all_r.extend(r)
                await telegram.close_client()
                return all_r
            all_raw = asyncio.run(_run_raw())

        if not all_raw:
            console.print("[yellow]No matches found.[/yellow]")
            return
        processed = search_intel.process_results(all_raw, query)
        results = processed["results"]
        entities = processed["entities"]
        stats = processed["stats"]

        # client-side sender filter for display-name matching
        if sender and not api_from_user:
            results = [r for r in results if sender.lower() in r.get("sender", "").lower()]
            if not results:
                console.print(f"[yellow]No matches from sender '{sender}'.[/yellow]")
                return

        total = len(results)
        by_chat: dict[str, list[dict]] = {}
        for r in results:
            by_chat.setdefault(r.get("chat_name", "?"), []).append(r)

        console.print(f"[bold]{total} match(es)[/bold] in {len(by_chat)} chat(s) "
                      f"({stats['total_raw']} raw → {stats['total_deduped']} deduped)\n")
        for chat_name, msgs in sorted(by_chat.items()):
            console.print(f"[bold cyan]-- {chat_name}[/bold cyan] ({len(msgs)})")
            for m in msgs:
                s = m["date"][:10] if m.get("date") else "?"
                t = m["text"][:200].replace("\n", " ")
                console.print(f"  [dim]{s}[/dim] [green]{m['sender']} {m.get('username', '')}[/green]: {t}")
            console.print("")
        if entities["usernames"]:
            console.print("[bold]Users:[/bold] " + ", ".join(entities["usernames"]))

        if export_file:
            Path(export_file).write_text(
                json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            console.print(f"[green]Exported {len(results)} result(s) → {export_file}[/green]")

        if save and click.confirm(f"Save {len(results)} hit(s) to Notion Follow-ups?", default=False):
            _save_hits_to_notion(results, query)
        return

    # ── context-aware search (default) ─────────────────────────────────
    with console.status("[cyan]Searching with context...[/cyan]", spinner="dots"):
        async def _run_ctx():
            all_b: list[dict] = []
            for term in expanded:
                blocks = await telegram.search_with_context(
                    query=term, dialog_ids=dialog_ids,
                    dialog_names=dialog_names, limit_per_chat=limit,
                    days_back=days, context_window=ctx, from_user=api_from_user,
                )
                all_b.extend(blocks)
            await telegram.close_client()
            return all_b
        all_blocks = asyncio.run(_run_ctx())

    # deduplicate blocks (same hit message_id + chat_id)
    seen_blocks: set[tuple[int, int]] = set()
    unique_blocks: list[dict] = []
    for blk in all_blocks:
        key = (blk["chat_id"], blk["hit"]["message_id"])
        if key not in seen_blocks:
            seen_blocks.add(key)
            unique_blocks.append(blk)

    if not unique_blocks:
        console.print("[yellow]No matches found.[/yellow]")
        return

    # client-side sender filter for display-name matching
    if sender and not api_from_user:
        filtered = [b for b in unique_blocks if sender.lower() in b["hit"].get("sender", "").lower()]
        if not filtered:
            console.print(f"[yellow]No matches from sender '{sender}'.[/yellow]")
            return
        unique_blocks = filtered

    entities = search_intel.extract_entities_from_blocks(unique_blocks)

    by_chat_name: dict[str, list[dict]] = {}
    for blk in unique_blocks:
        by_chat_name.setdefault(blk["chat_name"], []).append(blk)

    total_hits = len(unique_blocks)
    console.print(f"[bold]{total_hits} hit(s)[/bold] in {len(by_chat_name)} chat(s) (context ±{ctx})")
    if entities["usernames"]:
        console.print(f"[green]{len(entities['usernames'])} @users[/green] | {len(entities['links'])} links | {len(entities['amounts'])} amounts\n")
    else:
        console.print(f"{len(entities['links'])} links | {len(entities['amounts'])} amounts\n")

    for chat_name, blocks in sorted(by_chat_name.items()):
        blocks.sort(key=lambda b: b["hit"].get("date", ""))
        console.print(f"[bold cyan]-- {chat_name}[/bold cyan] ({len(blocks)} hits)")

        for blk in blocks:
            hit = blk["hit"]
            before = blk.get("before", [])
            after = blk.get("after", [])

            for m in before:
                s = m["date"][:10] if m.get("date") else "?"
                t = m["text"][:150].replace("\n", " ")
                console.print(f"  [dim]{s}[/dim] [cyan]{m['sender']} {m.get('username', '')}[/cyan]: [dim]{t}[/dim]")

            hs = hit["date"][:10] if hit.get("date") else "?"
            ht = hit["text"][:300].replace("\n", " ")
            console.print(f"  [dim]{hs}[/dim] [bold green]> {hit['sender']} {hit.get('username', '')}[/bold green]: [bold]{ht}[/bold]")

            for m in after:
                s = m["date"][:10] if m.get("date") else "?"
                t = m["text"][:150].replace("\n", " ")
                console.print(f"  [dim]{s}[/dim] [cyan]{m['sender']} {m.get('username', '')}[/cyan]: {t}")

            console.print("")

    if entities["usernames"]:
        console.print(f"[bold]People found:[/bold] {', '.join(entities['usernames'])}")
        top = entities["usernames"][:3]
        console.print(
            f"[dim]  Profile intel: "
            + " · ".join(f"teleon profile {u}" for u in top)
            + "[/dim]"
        )
    if entities["links"]:
        console.print("[bold]Links:[/bold]")
        for link in entities["links"][:5]:
            console.print(f"  {link[:120]}{'...' if len(link) > 120 else ''}")

    # learn affinity
    for chat_name in by_chat_name:
        search_intel.learn(intel_store, chat_name, query, len(by_chat_name[chat_name]))

    intel_file.parent.mkdir(parents=True, exist_ok=True)
    intel_file.write_text(json.dumps(intel_store, indent=2, ensure_ascii=False), encoding="utf-8")

    # log search
    SEARCH_LOG.parent.mkdir(exist_ok=True)
    with SEARCH_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "chat_filter": chat,
            "type_filter": chat_type,
            "days_back": days,
            "context_window": ctx,
            "sender_filter": sender,
            "ai_used": use_ai,
            "saved": save,
            "total_hits": total_hits,
            "chats_with_matches": len(by_chat_name),
            "usernames_found": len(entities.get("usernames", [])),
            "links_found": len(entities.get("links", [])),
            "chats_searched": len(dialog_ids),
        }) + "\n")

    if export_file:
        export_data = [
            {"chat_id": blk["chat_id"], "chat_name": blk["chat_name"],
             "hit": blk["hit"], "before": blk.get("before", []), "after": blk.get("after", [])}
            for blk in unique_blocks
        ]
        Path(export_file).write_text(
            json.dumps(export_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        console.print(f"[green]Exported {len(unique_blocks)} block(s) → {export_file}[/green]")

    if use_ai:
        console.print("[cyan]Running AI analysis...[/cyan]")
        hits_flat = [blk["hit"] for blk in unique_blocks]
        analysis = ai_module.analyze_search_results(query, hits_flat)
        console.print(Panel(analysis, title="[bold]AI Analysis[/bold]", border_style="cyan"))

    if save and click.confirm(f"Save {total_hits} hit(s) to Notion Follow-ups?", default=False):
        hits_with_chat = [{"chat_name": blk["chat_name"], **blk["hit"]} for blk in unique_blocks]
        _save_hits_to_notion(hits_with_chat, query)


# ─── profile ──────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("person")
@click.option("--days", default=90, show_default=True, help="How far back to scan messages")
@click.option("--limit", default=30, show_default=True, help="Max messages per chat")
@click.option("--export", "export_file", default=None, metavar="FILE",
              help="Export full profile to JSON")
def profile(person: str, days: int, limit: int, export_file: str | None):
    """Build an intelligence profile on a person from their Telegram messages.

    Scans all tracked chats for messages sent by this person, extracts topics,
    interests, expertise, communication style, and generates specific cold DM
    conversation openers grounded in what they actually said.

    \b
    Examples:
      teleon profile "@alice"
      teleon profile "Alice Chen" --days 30
      teleon profile "@bob" --export bob.json
    """
    entries = tracker.get_tracked()
    if not entries:
        console.print("[yellow]No tracked chats. Run 'teleon sync' first.[/yellow]")
        return

    dialog_names = {e["id"]: e["name"] for e in entries}
    dialog_kinds = {e["id"]: e.get("kind", "group") for e in entries}
    dialog_ids = list(dialog_names.keys())

    handle = person.lstrip("@")
    console.print(f"[bold cyan]Profile[/bold cyan] {person} — scanning {len(dialog_ids)} chat(s), last {days} days\n")

    with console.status("[cyan]Collecting messages...[/cyan]", spinner="dots"):
        async def _run():
            msgs = await telegram.get_person_messages(
                from_user=handle,
                dialog_ids=dialog_ids,
                dialog_names=dialog_names,
                dialog_kinds=dialog_kinds,
                limit_per_chat=limit,
                days_back=days,
            )
            await telegram.close_client()
            return msgs
        messages = asyncio.run(_run())

    if not messages:
        console.print(f"[yellow]No messages found for '{person}' in tracked chats.[/yellow]")
        console.print("[dim]Try --days 365, or check the username matches exactly.[/dim]")
        return

    # ── basic stats ────────────────────────────────────────────────────
    chats_seen = {m["chat_name"] for m in messages}
    console.print(f"[bold]{len(messages)} message(s)[/bold] across {len(chats_seen)} chat(s)\n")

    # ── NLP quick pass (always runs, no AI needed) ─────────────────────
    import nlp as nlp_module
    texts = [m["text"] for m in messages if m.get("text")]
    nlp_data = nlp_module.enrich([{"text": t} for t in texts])

    sentiment = nlp_data.get("sentiment", {})
    entities_nlp = nlp_data.get("entities", {})
    links_found = nlp_data.get("contact_info", {}).get("urls", [])

    console.print(f"[bold]Sentiment:[/bold] {sentiment.get('label', '?')} "
                  f"(score {sentiment.get('compound', 0):.2f})")
    if entities_nlp.get("orgs"):
        console.print(f"[bold]Orgs mentioned:[/bold] {', '.join(entities_nlp['orgs'][:8])}")
    if entities_nlp.get("persons"):
        others = [p for p in entities_nlp["persons"] if handle.lower() not in p.lower()]
        if others:
            console.print(f"[bold]People mentioned:[/bold] {', '.join(others[:6])}")
    if links_found:
        console.print(f"[bold]Links shared:[/bold] {len(links_found)}")
        for link in links_found[:3]:
            console.print(f"  {link[:100]}")

    # ── AI profile (if provider configured) ───────────────────────────
    console.print("")
    if config.AI_PROVIDER.lower() == "none":
        console.print("[dim]Set AI_PROVIDER in .env for full profile + DM openers.[/dim]")
    else:
        with console.status("[cyan]Building AI profile...[/cyan]", spinner="dots"):
            prof = ai_module.build_person_profile(person, messages)

        if prof.get("summary"):
            console.print(Panel(prof["summary"], title="[bold]Summary[/bold]", border_style="cyan"))

        if prof.get("recent_focus"):
            console.print(f"[bold]Recent focus:[/bold] {prof['recent_focus']}")

        for label, key in [("Topics", "topics"), ("Expertise", "expertise"),
                           ("Projects", "projects"), ("Interests", "interests")]:
            vals = prof.get(key, [])
            if vals:
                console.print(f"[bold]{label}:[/bold] {', '.join(vals)}")

        if prof.get("style"):
            console.print(f"[bold]Style:[/bold] {prof['style']}")

        openers = prof.get("openers", [])
        if openers:
            console.print(f"\n[bold cyan]Cold DM openers[/bold cyan]")
            for i, opener in enumerate(openers, 1):
                console.print(Panel(opener, title=f"[dim]Option {i}[/dim]",
                                    border_style="dim", padding=(0, 2)))

    # ── export ─────────────────────────────────────────────────────────
    if export_file:
        export_data = {
            "person": person,
            "messages_collected": len(messages),
            "chats": list(chats_seen),
            "nlp": nlp_data,
        }
        if config.AI_PROVIDER.lower() != "none":
            export_data["profile"] = prof  # type: ignore[possibly-undefined]
        Path(export_file).write_text(
            json.dumps(export_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        console.print(f"\n[green]Exported → {export_file}[/green]")


# ─── enrich ───────────────────────────────────────────────────────────────────

_ALL_SOURCES = ["web", "linkedin", "github", "twitter", "crunchbase", "custom"]

@cli.command()
@click.argument("name", required=False)
@click.option(
    "--source", "sources",
    multiple=True,
    type=click.Choice(_ALL_SOURCES),
    default=["web", "linkedin"],
    show_default=True,
    help="Sources to search. Can be repeated.",
)
@click.option("--query", default=None, help="Custom search query (used with --source custom)")
@click.option("--all", "enrich_all", is_flag=True, default=False, help="Enrich all tracked contacts")
def enrich(name: str | None, sources: tuple, query: str | None, enrich_all: bool):
    """Enrich a contact by searching online sources, saved as extra Notion columns.

    \b
    Examples:
      teleon enrich "Alice"
      teleon enrich "Alice" --source linkedin --source github
      teleon enrich "Alice" --source custom --query "Alice Chen CEO Acme Corp"
      teleon enrich --all --source linkedin
    """
    sources_list = list(sources) if sources else ["web", "linkedin"]

    if enrich_all:
        contacts = notion.list_contacts(limit=100)
        if not contacts:
            console.print("[dim]No contacts in Notion yet.[/dim]")
            return
        console.print(f"Enriching {len(contacts)} contact(s) via: {', '.join(sources_list)}\n")
        for c in contacts:
            page = notion.find_contact(c["name"], fuzzy=False)
            if not page:
                continue
            fields = enricher.enrich_contact(
                contact_name=c["name"],
                contact_page_id=page["id"],
                sources=sources_list,
                custom_query=query,
            )
            if fields:
                console.print(f"  [green]✓[/green] {c['name']}: {', '.join(fields.keys())}")
            else:
                console.print(f"  [dim]–[/dim] {c['name']}: nothing extracted")
        return

    if not name:
        console.print("[red]Provide a contact name or --all[/red]")
        sys.exit(1)

    page = notion.find_contact(name)
    if not page:
        console.print(f"[red]Contact not found:[/red] {name}")
        console.print("[dim]Run 'teleon contacts' to see available contacts.[/dim]")
        sys.exit(1)

    fields = enricher.enrich_contact(
        contact_name=name,
        contact_page_id=page["id"],
        sources=sources_list,
        custom_query=query,
    )

    if fields:
        table = Table(title=f"Enrichment — {name}", header_style="bold green")
        table.add_column("Field")
        table.add_column("Value")
        for k, v in fields.items():
            table.add_row(k, v[:80] + ("…" if len(v) > 80 else ""))
        console.print(table)
    else:
        console.print(f"[yellow]No enrichment data found for '{name}'[/yellow]")


# ─── schedule ──────────────────────────────────────────────────────────────────

_TASK_NAME = "Teleon-Sync"


def _cwd() -> str:
    return str(Path(__file__).parent.resolve())


def _uv_exe() -> str:
    import shutil
    return shutil.which("uv") or "uv"


def _schedule_windows(run_time: str, every_hours: int | None) -> tuple[bool, str]:
    cwd = _cwd()
    uv = _uv_exe()
    if every_hours:
        trigger = (
            f"New-ScheduledTaskTrigger -Once -At (Get-Date).Date "
            f"-RepetitionInterval (New-TimeSpan -Hours {every_hours})"
        )
        desc = f"every {every_hours}h"
    else:
        trigger = f'New-ScheduledTaskTrigger -Daily -At "{run_time}"'
        desc = f"daily at {run_time}"
    ps = (
        f'$a = New-ScheduledTaskAction -Execute "{uv}" -Argument "run teleon sync"'
        f' -WorkingDirectory "{cwd}";'
        f"$t = {trigger};"
        f"$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2)"
        f" -StartWhenAvailable $true;"
        f'Register-ScheduledTask -TaskName "{_TASK_NAME}" -Action $a -Trigger $t'
        f" -Settings $s -Force | Out-Null"
    )
    r = subprocess.run(
        ["powershell", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True, encoding="utf-8",
    )
    return (True, desc) if r.returncode == 0 else (False, (r.stderr or r.stdout).strip())


def _remove_schedule_windows() -> tuple[bool, str]:
    ps = f'Unregister-ScheduledTask -TaskName "{_TASK_NAME}" -Confirm:$false'
    r = subprocess.run(
        ["powershell", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True, encoding="utf-8",
    )
    return (True, "removed") if r.returncode == 0 else (False, r.stderr.strip())


def _show_schedule_windows() -> str:
    ps = (
        f'$t = Get-ScheduledTask -TaskName "{_TASK_NAME}" -ErrorAction SilentlyContinue;'
        f"if ($t) {{ $t | Get-ScheduledTaskInfo | Select-Object LastRunTime, NextRunTime | Format-List }}"
        f' else {{ Write-Output "Not scheduled." }}'
    )
    r = subprocess.run(
        ["powershell", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True, encoding="utf-8",
    )
    return r.stdout.strip()


def _schedule_unix(run_time: str, every_hours: int | None) -> tuple[bool, str]:
    cwd = _cwd()
    log = f"{cwd}/logs/cron.log"
    if every_hours:
        expr = f"0 */{every_hours} * * *"
        desc = f"every {every_hours}h"
    else:
        h, m = run_time.split(":", 1)
        expr = f"{m.strip()} {h.strip()} * * *"
        desc = f"daily at {run_time}"
    entry = f'{expr} cd "{cwd}" && teleon sync >> "{log}" 2>&1  # Teleon'
    existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = [
        l for l in (existing.stdout if existing.returncode == 0 else "").splitlines()
        if "Teleon" not in l and l.strip()
    ]
    lines.append(entry)
    r = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                       capture_output=True, text=True)
    return (True, desc) if r.returncode == 0 else (False, r.stderr.strip())


def _remove_schedule_unix() -> tuple[bool, str]:
    existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if existing.returncode != 0:
        return False, "No crontab found"
    lines = [l for l in existing.stdout.splitlines() if "Teleon" not in l]
    r = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                       capture_output=True, text=True)
    return (True, "removed") if r.returncode == 0 else (False, r.stderr.strip())


def _show_schedule_unix() -> str:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if r.returncode != 0:
        return "No crontab configured."
    lines = [l for l in r.stdout.splitlines() if "Teleon" in l]
    return "\n".join(lines) if lines else "No Teleon schedule found."


@cli.group()
def schedule():
    """Manage automatic sync schedule (Windows Task Scheduler on Windows, cron on Unix)."""
    pass


@schedule.command("setup")
@click.option("--time", "run_time", default="08:00", show_default=True,
              help="Daily sync time (HH:MM, 24h)")
@click.option("--every", "every_hours", default=None, type=int,
              help="Run every N hours instead of once daily")
def schedule_setup(run_time: str, every_hours: int | None):
    """Set up automatic Telegram → Notion sync.

    \b
    Examples:
      teleon schedule setup                  # daily at 08:00
      teleon schedule setup --time 09:30     # daily at 09:30
      teleon schedule setup --every 6        # every 6 hours
    """
    console.print("[cyan]Setting up schedule...[/cyan]")
    if sys.platform == "win32":
        ok, msg = _schedule_windows(run_time, every_hours)
    else:
        ok, msg = _schedule_unix(run_time, every_hours)
    if ok:
        console.print(f"[green]✓ Sync scheduled:[/green] {msg}")
        console.print(f"[dim]  Task name: {_TASK_NAME}[/dim]")
        if sys.platform != "win32":
            console.print(f"[dim]  Logs: {_cwd()}/logs/cron.log[/dim]")
    else:
        console.print(f"[red]Failed to create schedule: {msg}[/red]")


@schedule.command("show")
def schedule_show():
    """Show the current sync schedule and last/next run times."""
    if sys.platform == "win32":
        console.print(_show_schedule_windows())
    else:
        info = _show_schedule_unix()
        console.print(info or "[dim]No Teleon schedule found.[/dim]")


@schedule.command("remove")
def schedule_remove():
    """Remove the automatic sync schedule."""
    if not click.confirm(f"Remove '{_TASK_NAME}' schedule?", default=False):
        return
    if sys.platform == "win32":
        ok, msg = _remove_schedule_windows()
    else:
        ok, msg = _remove_schedule_unix()
    console.print("[green]Schedule removed.[/green]" if ok else f"[red]Failed: {msg}[/red]")


if __name__ == "__main__":
    cli()
