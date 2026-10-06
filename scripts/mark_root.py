#!/usr/bin/env python3
"""Mark roots by potential: which roots are worth watching, which are not.

Usage:
  python3 scripts/mark_root.py --term "roomgpt" --status potential --note "AI photo niche, breakout twice"
  python3 scripts/mark_root.py --term "best pizza near me" --status nop --note "local noise"
  python3 scripts/mark_root.py --term "roomgpt" --clear
  python3 scripts/mark_root.py --list [--status potential]

Status values:
  potential  有发展潜力 — worth deeper tracking / site potential
  nop        无潜力 — noise or dead end, excluded from future opportunity lists

Marks live in state/roots.json under "marks" and are surfaced in the
daily report stats. Never touches the "roots" list itself.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTS_FILE = ROOT / "state" / "roots.json"
VALID = {"potential", "nop"}


def load() -> dict:
    return json.loads(ROOTS_FILE.read_text(encoding="utf-8"))


def save(store: dict) -> None:
    ROOTS_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--term", help="root term to mark")
    ap.add_argument("--status", choices=sorted(VALID), help="potential | nop")
    ap.add_argument("--note", default="", help="why")
    ap.add_argument("--clear", action="store_true", help="remove the mark")
    ap.add_argument("--list", action="store_true", help="list marks")
    args = ap.parse_args()

    store = load()
    marks = store.setdefault("marks", {})

    if args.list:
        wanted = {args.status} if args.status else VALID
        rows = [(t, m) for t, m in marks.items() if m.get("status") in wanted]
        for t, m in sorted(rows):
            src = store.get("root_sources", {}).get(t, "?")
            print(f"[{m['status']}] {t} (src={src}) {m.get('note','')}")
        print(f"total: {len(rows)}")
        return 0

    if not args.term:
        print("need --term or --list", file=sys.stderr)
        return 2
    term = " ".join(args.term.lower().strip().split())
    if term not in store["roots"]:
        print(f"not in root pool: {term}", file=sys.stderr)
        return 2
    if args.clear:
        marks.pop(term, None)
        print(f"cleared mark: {term}")
    else:
        if args.status not in VALID:
            print("need --status potential|nop", file=sys.stderr)
            return 2
        marks[term] = {
            "status": args.status,
            "note": args.note,
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        print(f"marked [{args.status}]: {term}")
    save(store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
