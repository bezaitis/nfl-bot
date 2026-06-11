"""
storage.py — JSON persistence for seen-post IDs and runtime settings.

Extracted from bot.py so the bot and the tests share the exact same helpers.
"""

import json

SEEN_FILE = "seen_ids.json"
SETTINGS_FILE = "settings.json"
SEEN_MAX_SIZE = 500


def load_seen(path: str = SEEN_FILE) -> tuple[set[str], list[str]]:
    """Return (set for O(1) lookup, ordered list for insertion-order trimming)."""
    try:
        with open(path) as f:
            data = json.load(f)
        return set(data), list(data)
    except (FileNotFoundError, json.JSONDecodeError):
        return set(), []


def save_seen(seen_list: list[str], path: str = SEEN_FILE) -> None:
    """Persist seen IDs, keeping only the most recent SEEN_MAX_SIZE entries."""
    trimmed = seen_list[-SEEN_MAX_SIZE:]
    with open(path, "w") as f:
        json.dump(trimmed, f)


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"source": "both", "disabled_writers": []}


def save_settings(settings: dict) -> None:
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)
