"""Simple JSON file cache with TTL, keyed by (source, date)."""

import json
import os
import time
from typing import Any, Optional

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "events.json")

# Bumped whenever a change to the Event shape makes older cached entries wrong
# rather than merely stale. Entries are namespaced by it, so a schema change
# takes effect on the next run instead of waiting out a 12–24 hour TTL — an
# all-day listing cached before `all_day` existed would otherwise keep being
# rendered as a midnight start for another day.
SCHEMA_VERSION = 2


def _versioned(key: str) -> str:
    return f"{key}@v{SCHEMA_VERSION}"


def _load() -> dict:
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get(key: str, ttl_seconds: int = 3600 * 12) -> Optional[Any]:
    """Return cached value if it exists and is fresher than ttl_seconds."""
    data = _load()
    entry = data.get(_versioned(key))
    if not entry:
        return None
    if time.time() - entry["ts"] > ttl_seconds:
        return None
    return entry["value"]


def set(key: str, value: Any) -> None:
    """Store value in cache."""
    data = _load()
    # Drop entries written under an older schema; nothing will read them again.
    suffix = f"@v{SCHEMA_VERSION}"
    data = {k: v for k, v in data.items() if k.endswith(suffix)}
    data[_versioned(key)] = {"ts": time.time(), "value": value}
    _save(data)
