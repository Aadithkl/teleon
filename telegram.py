import asyncio
import sys
from datetime import datetime, timezone, timedelta

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import User, Chat, Channel

import config

SESSION_FILE = "crm_session"

_client: TelegramClient | None = None


async def get_client() -> TelegramClient:
    global _client
    if _client is None or not _client.is_connected():
        _client = TelegramClient(SESSION_FILE, config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH)
        await _client.start(phone=config.TELEGRAM_PHONE)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.disconnect()
        _client = None


async def _wait_flood(seconds: int, context: str = "") -> None:
    """Block until Telegram's flood wait is fully over, with a live countdown."""
    label = f" [{context}]" if context else ""
    total = seconds + 5          # always add a 5-second buffer on top
    print(f"\n  ⏳ Flood wait{label} — need to pause {total}s", flush=True)
    for remaining in range(total, 0, -1):
        print(f"\r  ⏳ {remaining:3d}s remaining{label}...   ", end="", flush=True)
        await asyncio.sleep(1)
    print(f"\r  ✓ Flood wait over{label}. Retrying now.           ")


async def get_recent_dialogs(days_back: int, dms_only: bool = False) -> list[dict]:
    client = await get_client()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    while True:
        try:
            dialogs = []
            async for dialog in client.iter_dialogs(limit=500):
                if dialog.date is None:
                    continue
                last_date = dialog.date
                if last_date.tzinfo is None:
                    last_date = last_date.replace(tzinfo=timezone.utc)
                if last_date < cutoff:
                    continue

                entity = dialog.entity
                if isinstance(entity, User):
                    if entity.bot:
                        continue
                    kind = "dm"
                    name_parts = [p for p in [entity.first_name, entity.last_name] if p]
                    name = " ".join(name_parts) if name_parts else str(entity.id)
                    username = f"@{entity.username}" if entity.username else None
                elif isinstance(entity, (Chat, Channel)):
                    if dms_only:
                        continue
                    kind = "group"
                    name = entity.title or str(entity.id)
                    username = (
                        f"@{entity.username}"
                        if hasattr(entity, "username") and entity.username
                        else None
                    )
                else:
                    continue

                dialogs.append({
                    "id": dialog.id,
                    "name": name,
                    "kind": kind,
                    "username": username,
                    "last_message_date": last_date.isoformat(),
                })
            return dialogs

        except FloodWaitError as e:
            # wait the full duration, then loop back and retry from scratch
            await _wait_flood(e.seconds, context="fetching dialogs")


async def search_messages(
    query: str,
    dialog_ids: list[int],
    dialog_names: dict[int, str],
    limit_per_chat: int = 20,
    days_back: int | None = None,
    from_user: str | None = None,
) -> list[dict]:
    """Search messages across multiple chats using Telegram server-side search.

    Args:
        query: Search term (Telegram's built-in full-text search).
        dialog_ids: List of chat dialog IDs to search.
        dialog_names: Mapping of dialog_id → display name.
        limit_per_chat: Max results to return per chat.
        days_back: Only include messages newer than this many days.
        from_user: Telegram username (without @) to filter at API level.

    Returns: list of {chat_id, chat_name, message_id, date, sender, text}
    """
    client = await get_client()
    cutoff = None
    if days_back is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    results: list[dict] = []
    for dialog_id in dialog_ids:
        chat_name = dialog_names.get(dialog_id, str(dialog_id))
        iter_kwargs: dict = {"search": query, "limit": limit_per_chat}
        if from_user:
            iter_kwargs["from_user"] = from_user
        try:
            async for msg in client.iter_messages(dialog_id, **iter_kwargs):
                if not msg.text:
                    continue
                msg_date = msg.date
                if msg_date and msg_date.tzinfo is None:
                    msg_date = msg_date.replace(tzinfo=timezone.utc)
                if cutoff and msg_date and msg_date < cutoff:
                    continue

                sender_name, sender_username = _format_sender(msg)

                results.append({
                    "chat_id": dialog_id,
                    "chat_name": chat_name,
                    "message_id": msg.id,
                    "date": msg_date.isoformat() if msg_date else "",
                    "sender": sender_name,
                    "username": sender_username,
                    "text": msg.text,
                })

                if len([r for r in results if r["chat_id"] == dialog_id]) >= limit_per_chat:
                    break

        except FloodWaitError as e:
            await _wait_flood(e.seconds, context=f"search '{query[:30]}' in {chat_name}")

    return results


async def search_with_context(
    query: str,
    dialog_ids: list[int],
    dialog_names: dict[int, str],
    limit_per_chat: int = 10,
    days_back: int | None = None,
    context_window: int = 5,
    from_user: str | None = None,
) -> list[dict]:
    """Search messages and return context blocks around each hit.

    For each matching message, also fetches the surrounding messages
    so you can read the full conversation flow.

    Args:
        query: Search term.
        dialog_ids: Chats to search.
        dialog_names: Mapping of dialog_id → display name.
        limit_per_chat: Max hits to find per chat.
        days_back: Time range filter.
        context_window: How many messages before/after each hit to return.
        from_user: Telegram username (without @) to filter at API level.

    Returns: list of context blocks:
        {chat_id, chat_name, hit: {...}, before: [{...}], after: [{...}]}
    """
    client = await get_client()
    cutoff = None
    if days_back is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    # Step 1: find hits per chat
    hits_by_chat: dict[int, list] = {}
    for dialog_id in dialog_ids:
        chat_name = dialog_names.get(dialog_id, str(dialog_id))
        chat_hits: list = []
        iter_kwargs: dict = {"search": query, "limit": limit_per_chat}
        if from_user:
            iter_kwargs["from_user"] = from_user
        try:
            async for msg in client.iter_messages(dialog_id, **iter_kwargs):
                if not msg.text:
                    continue
                msg_date = msg.date
                if msg_date and msg_date.tzinfo is None:
                    msg_date = msg_date.replace(tzinfo=timezone.utc)
                if cutoff and msg_date and msg_date < cutoff:
                    continue
                sender_name, sender_username = _format_sender(msg)
                chat_hits.append({
                    "message_id": msg.id,
                    "date": msg_date.isoformat() if msg_date else "",
                    "sender": sender_name,
                    "username": sender_username,
                    "text": msg.text,
                    "_msg": msg,  # keep Telethon object for context fetch
                })
        except FloodWaitError as e:
            await _wait_flood(e.seconds, context=f"search '{query[:30]}' in {chat_name}")

        if chat_hits:
            hits_by_chat[dialog_id] = {"name": chat_name, "hits": chat_hits}

    if not hits_by_chat:
        return []

    # Step 2: for each chat, fetch context around hits
    blocks: list[dict] = []
    for dialog_id, info in hits_by_chat.items():
        chat_name = info["name"]
        chat_hits = info["hits"]

        for hit in chat_hits:
            hit_msg = hit.pop("_msg")  # remove Telethon object

            # ── fetch messages before the hit (lead-up) ───────────────
            before_raw: list[dict] = []
            try:
                async for msg in client.iter_messages(
                    dialog_id, limit=context_window, offset_id=hit_msg.id
                ):
                    if msg.text:
                        s, u = _format_sender(msg)
                        before_raw.append({
                            "message_id": msg.id,
                            "date": msg.date.isoformat() if msg.date else "",
                            "sender": s,
                            "username": u,
                            "text": msg.text,
                        })
            except FloodWaitError:
                pass

            # ── fetch messages after the hit (replies/responses) ──────
            after_raw: list[dict] = []
            try:
                async for msg in client.iter_messages(
                    dialog_id, limit=context_window, offset_id=hit_msg.id, reverse=True
                ):
                    if msg.text:
                        s, u = _format_sender(msg)
                        after_raw.append({
                            "message_id": msg.id,
                            "date": msg.date.isoformat() if msg.date else "",
                            "sender": s,
                            "username": u,
                            "text": msg.text,
                        })
            except FloodWaitError:
                pass

            blocks.append({
                "chat_id": dialog_id,
                "chat_name": chat_name,
                "hit": hit,
                "before": list(reversed(before_raw)),  # chronological order
                "after": after_raw,
            })

    return blocks


def _format_sender(msg) -> tuple[str, str | None]:
    """Extract display name and @username from a message."""
    sender_name = "Unknown"
    sender_username: str | None = None
    if msg.sender:
        sender = msg.sender
        if isinstance(sender, User):
            parts = [p for p in [sender.first_name, sender.last_name] if p]
            sender_name = " ".join(parts) if parts else str(sender.id)
            if sender.username:
                sender_username = f"@{sender.username}"
        elif hasattr(sender, "title"):
            sender_name = sender.title
    return sender_name, sender_username


async def get_messages(dialog_id: int, limit: int, days_back: int) -> list[dict]:
    client = await get_client()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    while True:
        try:
            messages = []
            async for msg in client.iter_messages(dialog_id, limit=limit):
                if not msg.text:
                    continue
                msg_date = msg.date
                if msg_date.tzinfo is None:
                    msg_date = msg_date.replace(tzinfo=timezone.utc)
                if msg_date < cutoff:
                    break

                sender_name = "Unknown"
                if msg.sender:
                    sender = msg.sender
                    if isinstance(sender, User):
                        parts = [p for p in [sender.first_name, sender.last_name] if p]
                        sender_name = " ".join(parts) if parts else str(sender.id)
                    elif hasattr(sender, "title"):
                        sender_name = sender.title

                messages.append({
                    "id": msg.id,
                    "date": msg_date.isoformat(),
                    "sender": sender_name,
                    "text": msg.text,
                })

            messages.reverse()
            return messages

        except FloodWaitError as e:
            # wait the full duration + buffer, then loop back and retry
            await _wait_flood(e.seconds, context="reading messages")
