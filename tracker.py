"""Chat tracking registry — source of truth for what gets synced.

Registry file: tracked_chats.json
Shape:
{
  "tracked": [{id, name, kind, username, added}],
  "pending": [{id, name, kind, username, detected}],
  "paused":  [{id, name, kind, username}],
  "ignored": [id, id, ...]          <- int dialog IDs
}

Statuses:
  tracked  — active, will be synced
  pending  — detected but not yet approved
  paused   — was tracked, temporarily skipped
  ignored  — never sync, never re-add to pending
  unknown  — not in registry at all
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_FILE = Path("tracked_chats.json")


# ─── low-level I/O ─────────────────────────────────────────────────────────────

def load() -> dict:
    if not REGISTRY_FILE.exists():
        return {"tracked": [], "pending": [], "paused": [], "ignored": []}
    with open(REGISTRY_FILE, encoding="utf-8-sig") as f:
        data = json.load(f)
    # ensure all keys exist
    data.setdefault("tracked", [])
    data.setdefault("pending", [])
    data.setdefault("paused", [])
    data.setdefault("ignored", [])
    return data


def save(registry: dict) -> None:
    with open(REGISTRY_FILE, "w", encoding="utf-8", newline="") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)


# ─── status queries ────────────────────────────────────────────────────────────

def status(dialog_id: int) -> str:
    """Return 'tracked' | 'pending' | 'paused' | 'ignored' | 'unknown'."""
    reg = load()
    if dialog_id in reg["ignored"]:
        return "ignored"
    if any(e["id"] == dialog_id for e in reg["tracked"]):
        return "tracked"
    if any(e["id"] == dialog_id for e in reg["paused"]):
        return "paused"
    if any(e["id"] == dialog_id for e in reg["pending"]):
        return "pending"
    return "unknown"


def get_tracked() -> list[dict]:
    return load()["tracked"]


def get_pending() -> list[dict]:
    return load()["pending"]


def get_paused() -> list[dict]:
    return load()["paused"]


def get_ignored_ids() -> list[int]:
    return load()["ignored"]


# ─── mutations ─────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _find(entries: list[dict], name_or_id: str | int) -> dict | None:
    for e in entries:
        if isinstance(name_or_id, int):
            if e["id"] == name_or_id:
                return e
        else:
            if e["name"].lower() == str(name_or_id).lower():
                return e
    return None


def add_pending(dialog: dict) -> None:
    """Add a dialog to pending if not already in any bucket."""
    if status(dialog["id"]) != "unknown":
        return
    reg = load()
    reg["pending"].append({
        "id": dialog["id"],
        "name": dialog["name"],
        "kind": dialog.get("kind", "dm"),
        "username": dialog.get("username"),
        "detected": _now(),
    })
    save(reg)


def add_tracked(dialog: dict) -> None:
    """Add directly to tracked, skipping pending. Removes from other buckets first."""
    reg = load()
    _remove_from_all(reg, dialog["id"])
    reg["tracked"].append({
        "id": dialog["id"],
        "name": dialog["name"],
        "kind": dialog.get("kind", "dm"),
        "username": dialog.get("username"),
        "added": _now(),
    })
    save(reg)


def approve(name_or_id: str | int) -> bool:
    """Move from pending → tracked. Returns True if found and moved."""
    reg = load()
    entry = _find(reg["pending"], name_or_id)
    if not entry:
        return False
    reg["pending"].remove(entry)
    reg["tracked"].append({
        "id": entry["id"],
        "name": entry["name"],
        "kind": entry["kind"],
        "username": entry.get("username"),
        "added": _now(),
    })
    save(reg)
    return True


def approve_all() -> int:
    """Move all pending → tracked. Returns count moved."""
    reg = load()
    count = len(reg["pending"])
    for entry in reg["pending"]:
        reg["tracked"].append({
            "id": entry["id"],
            "name": entry["name"],
            "kind": entry["kind"],
            "username": entry.get("username"),
            "added": _now(),
        })
    reg["pending"] = []
    save(reg)
    return count


def pause(name_or_id: str | int) -> bool:
    """Move from tracked → paused."""
    reg = load()
    entry = _find(reg["tracked"], name_or_id)
    if not entry:
        return False
    reg["tracked"].remove(entry)
    reg["paused"].append({
        "id": entry["id"],
        "name": entry["name"],
        "kind": entry["kind"],
        "username": entry.get("username"),
    })
    save(reg)
    return True


def resume(name_or_id: str | int) -> bool:
    """Move from paused → tracked."""
    reg = load()
    entry = _find(reg["paused"], name_or_id)
    if not entry:
        return False
    reg["paused"].remove(entry)
    reg["tracked"].append({
        "id": entry["id"],
        "name": entry["name"],
        "kind": entry["kind"],
        "username": entry.get("username"),
        "added": _now(),
    })
    save(reg)
    return True


def ignore(name_or_id: str | int) -> bool:
    """Remove from all buckets and add ID to ignored list."""
    reg = load()
    # find the id first
    dialog_id = None
    if isinstance(name_or_id, int):
        dialog_id = name_or_id
    else:
        for bucket in ("tracked", "pending", "paused"):
            entry = _find(reg[bucket], name_or_id)
            if entry:
                dialog_id = entry["id"]
                break
    if dialog_id is None:
        return False
    _remove_from_all(reg, dialog_id)
    if dialog_id not in reg["ignored"]:
        reg["ignored"].append(dialog_id)
    save(reg)
    return True


def _remove_from_all(reg: dict, dialog_id: int) -> None:
    for bucket in ("tracked", "pending", "paused"):
        reg[bucket] = [e for e in reg[bucket] if e["id"] != dialog_id]
    if dialog_id in reg["ignored"]:
        reg["ignored"].remove(dialog_id)
