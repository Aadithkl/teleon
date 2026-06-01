import asyncio
from datetime import datetime, timezone, timedelta

from telethon import TelegramClient
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


async def get_recent_dialogs(days_back: int) -> list[dict]:
    client = await get_client()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    dialogs = []

    async for dialog in client.iter_dialogs():
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
            kind = "group"
            name = entity.title or str(entity.id)
            username = f"@{entity.username}" if hasattr(entity, "username") and entity.username else None
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


async def get_messages(dialog_id: int, limit: int, days_back: int) -> list[dict]:
    client = await get_client()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
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
