#!/usr/bin/env python3
"""Merge small collector deltas into the cached primary state."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "state" / "state.json"
NODES_DIR = ROOT / "state" / "nodes"


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


def main() -> int:
    state = load(STATE_FILE, {"roots": {}, "reviews": {}})
    changed = sum(merge_state(state, load(path, {}))
                  for path in sorted(NODES_DIR.glob("*.json")))
    if changed:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                              encoding="utf-8")
    print(f"merged {changed} local node records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
