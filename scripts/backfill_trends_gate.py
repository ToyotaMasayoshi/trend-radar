#!/usr/bin/env python3
"""One-off backfill: validate existing reddit-ai / github-radar roots.

Checks Google Trends interest for roots that entered the pool before the
trends gate (2026-10-08) existed. Roots with confirmed zero interest are
removed and blacklisted (consistent with the user's delete-and-blacklist
practice); roots that can't be checked (rate limited / network error) are
left untouched.

Stops at the first network/rate-limit error -- never delete on unknown
status. Progress is saved incrementally in state/backfill_progress.json so
interrupted runs can resume:  python3 scripts/backfill_trends_gate.py
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trends import RateLimited  # noqa: E402
from trends_gate import validate_roots_interest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state" / "roots.json"
PROGRESS = ROOT / "state" / "backfill_progress.json"
GATED_SOURCES = ("reddit-ai", "github-radar")

STOP_ERRORS = (
    RateLimited,
    urllib.error.URLError,
    http.client.HTTPException,  # includes RemoteDisconnected
    TimeoutError,
    ConnectionError,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="max roots to check this run (0 = all remaining)")
    args = ap.parse_args()

    store = json.loads(STATE.read_text(encoding="utf-8"))
    sources = store.get("root_sources", {})
    progress = json.loads(PROGRESS.read_text(encoding="utf-8")) \
        if PROGRESS.exists() else {}

    targets = [r for r in store["roots"]
               if sources.get(r) in GATED_SOURCES and r not in progress]
    if args.limit:
        targets = targets[:args.limit]
    print(f"checking {len(targets)} remaining roots "
          f"({len(progress)} already checked)", flush=True)

    stopped_early = []
    for i, term in enumerate(targets):
        try:
            v = validate_roots_interest([term]).get(term)
        except STOP_ERRORS as e:
            stopped_early.append(term)
            print(f"[{i+1}/{len(targets)}] {type(e).__name__}: {term} "
                  f"-- STOPPING", flush=True)
            break
        progress[term] = v
        PROGRESS.write_text(json.dumps(progress, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(f"[{i+1}/{len(targets)}] "
              f"{'DEAD' if v is False else ('UNCHECKED' if v is None else 'ok')}: "
              f"{term}", flush=True)

    # clean orphaned source/added_at entries for roots no longer present
    present = set(store["roots"])
    for field in ("root_sources", "root_added_at"):
        d = store.get(field) or {}
        for k in [k for k in d if k not in present]:
            del d[k]

    dead = [t for t, v in progress.items()
            if v is False and t in store["roots"]]
    removed = []
    if dead:
        deadset = set(dead)
        store["roots"] = [r for r in store["roots"] if r not in deadset]
        store["count"] = len(store["roots"])
        blocklist = store.setdefault("root_blocklist", [])
        have = set(blocklist)
        for r in dead:
            if r not in have:
                blocklist.append(r)
        for field in ("root_sources", "root_added_at"):
            d = store.get(field) or {}
            for r in dead:
                d.pop(r, None)
        removed = dead

    STATE.write_text(json.dumps(store, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    remaining = [r for r in store["roots"]
                 if sources.get(r) in GATED_SOURCES and r not in progress]
    print(json.dumps({
        "checked_this_run": len([t for t in targets if t in progress]),
        "dead_removed": removed,
        "stopped_early": stopped_early,
        "remaining": len(remaining),
        "total": store["count"],
        "blocklist": len(store["root_blocklist"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
