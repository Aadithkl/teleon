import asyncio
import sys

import click
from rich.console import Console
from rich.table import Table

import config
import notion
import telegram
import sync as sync_module

console = Console()


@click.group()
def cli():
    pass


@cli.command()
@click.option("--days", default=None, type=int, help="Days of history to sync")
def sync(days: int | None):
    """Run a full CRM sync from Telegram to Notion."""
    if days is not None:
        config.SYNC_DAYS_BACK = days
    asyncio.run(sync_module.main())


@cli.command()
@click.option("--limit", default=20, show_default=True, help="Max contacts to display")
def contacts(limit: int):
    """List contacts sorted by most recently contacted."""
    rows = notion.list_contacts(limit=limit)
    table = Table(title="Contacts", show_header=True, header_style="bold cyan")
    table.add_column("Name")
    table.add_column("Username")
    table.add_column("Source")
    table.add_column("Last Contacted")
    for row in rows:
        table.add_row(
            row["name"],
            row["username"] or "",
            row["source"],
            row["last_contacted"],
        )
    console.print(table)


@cli.command("contact")
@click.argument("name")
def contact_detail(name: str):
    """Show interaction history for a contact by name (case-sensitive)."""
    page = notion.find_contact(name)
    if not page:
        console.print(f"[red]Contact not found:[/red] {name}")
        sys.exit(1)

    page_id = page["id"]
    interactions = notion.get_interactions_for_contact(page_id)
    console.print(f"[bold]{name}[/bold] — {len(interactions)} interaction(s)")
    for interaction in interactions:
        console.print(f"  {interaction['date']}  {interaction['summary']}")


@cli.command()
@click.option("--days", default=None, type=int, help="Days back to check for activity")
def chats(days: int | None):
    """Preview active Telegram chats that would be synced."""
    days_back = days if days is not None else config.SYNC_DAYS_BACK

    async def _fetch():
        return await telegram.get_recent_dialogs(days_back=days_back)

    dialogs = asyncio.run(_fetch())

    asyncio.run(telegram.close_client())

    table = Table(title=f"Active Chats (last {days_back} day(s))", show_header=True, header_style="bold cyan")
    table.add_column("Type")
    table.add_column("Name")
    table.add_column("Username")
    table.add_column("Last Active")
    for d in dialogs:
        table.add_row(
            d["kind"].upper(),
            d["name"],
            d.get("username") or "",
            d["last_message_date"][:10],
        )
    console.print(table)


if __name__ == "__main__":
    cli()
