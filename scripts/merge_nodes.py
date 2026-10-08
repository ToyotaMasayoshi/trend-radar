#!/usr/bin/env python3
"""Merge small collector deltas into the cached primary state."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "state" / "state.json"
NODES_DIR = ROOT / "state" / "nodes"
SUGGESTIONS_FILE = ROOT / "state" / "seo-suggestions.json"
SUGGESTIONS_PUBLIC = ROOT / "site" / "data" / "seo-suggestions.json"


def load(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def merge_state(state: dict, node: dict) -> int:
    changed = 0
    for section, timestamp in (("roots", "updated"), ("reviews", "reviewed_at")):
        target = state.setdefault(section, {})
        for key, value in node.get(section, {}).items():
            current = target.get(key, {})
            if value.get(timestamp, "") >= current.get(timestamp, "") and value != current:
                target[key] = value
                changed += 1
    return changed


def merge_suggestions(target: dict, node: dict) -> int:
    changed = 0
    items = target.setdefault("items", {})
    for key, value in node.get("suggestions", {}).items():
        if value.get("fetched_at", "") >= items.get(key, {}).get("fetched_at", "") \
                and value != items.get(key):
            items[key] = value
            changed += 1
    if node.get("updated", "") > target.get("updated", ""):
        target["updated"] = node["updated"]
    return changed


def main() -> int:
    state = load(STATE_FILE, {"roots": {}, "reviews": {}})
    suggestions = load(SUGGESTIONS_FILE, {"updated": "", "items": {}})
    nodes = [load(path, {}) for path in sorted(NODES_DIR.glob("*.json"))]
    changed = sum(merge_state(state, node) for node in nodes)
    suggestion_changes = sum(merge_suggestions(suggestions, node) for node in nodes)
    if changed:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                              encoding="utf-8")
    if suggestion_changes:
        SUGGESTIONS_FILE.write_text(
            json.dumps(suggestions, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8")
    SUGGESTIONS_PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    SUGGESTIONS_PUBLIC.write_text(
        json.dumps(suggestions, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    print(f"merged {changed} local node records and {suggestion_changes} suggestion sets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
