"""Simple JSON file cache with TTL, keyed by (source, date)."""

import json
import os
import time
from typing import Any, Optional

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "events.json")


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
    entry = data.get(key)
    if not entry:
        return None
    if time.time() - entry["ts"] > ttl_seconds:
        return None
    return entry["value"]


def set(key: str, value: Any) -> None:
    """Store value in cache."""
    data = _load()
    data[key] = {"ts": time.time(), "value": value}
    _save(data)
