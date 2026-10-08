#!/usr/bin/env python3
"""Add Reddit-AI-monitor candidate roots to the trend-radar root pool.

Usage:
  python3 reddit_ai_roots.py --candidates /tmp/cands.json [--push]

/tmp/cands.json: [{"root": "some ai tool", "source_post": "https://..."}]

Filtering (user rules 2026-10-07):
  - big-vendor AI model terms excluded (is_vendor_model_term)
  - root_blocklist respected (never re-add)
  - dedup vs existing roots
  - noise: >6 words, empty, URLs
With --push: merge-safe push to GitHub (remote wins, keep local unpushed,
blocklist union) via helpers from github_radar_watch.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from github_radar_watch import (  # noqa: E402
    fetch_github_roots,
    is_vendor_model_term,
    load_roots,
    merge_stores,
    norm,
    push_roots,
)

STATE = Path(__file__).resolve().parent.parent / "state" / "roots.json"


# Lee 明确不要的技术向词（2026-10-06）：lora/unsloth/qlora 相关、微调代工方向
TECHNICAL_EXCLUDE_SUBSTRINGS = ["lora", "unsloth", "qlora", "fine-tune", "finetune"]


def _is_technical_excluded(q: str) -> bool:
    return any(s in q for s in TECHNICAL_EXCLUDE_SUBSTRINGS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()

    cands = json.loads(Path(args.candidates).read_text())
    local = load_roots(STATE)
    existing = {norm(r) for r in local["roots"]}
    blocked = {norm(r) for r in local["root_blocklist"]}

    added, skipped = [], []
    now = datetime.now(timezone.utc).isoformat()
    survivors: list[tuple[str, dict]] = []
    for c in cands:
        q = norm(c.get("root", ""))
        if not q or len(q.split()) > 6 or "http" in q:
            skipped.append({"root": q, "reason": "noise"})
            continue
        if is_vendor_model_term(q):
            skipped.append({"root": q, "reason": "vendor-model term"})
            continue
        if _is_technical_excluded(q):
            skipped.append({"root": q, "reason": "technical-excluded (lora/fine-tune)"})
            continue
        if q in blocked:
            skipped.append({"root": q, "reason": "blocklisted"})
            continue
        if q in existing:
            skipped.append({"root": q, "reason": "duplicate"})
            continue
        survivors.append((q, c))
    # Google Trends interest gate (2026-10-08): skip terms with zero search
    # interest so they don't consume rotation budget. Fail-open on rate limit.
    if survivors:
        from trends_gate import validate_roots_interest  # noqa: E402
        verdicts = validate_roots_interest([q for q, _ in survivors])
    else:
        verdicts = {}
    for q, c in survivors:
        verdict = verdicts.get(q)
        if verdict is False:
            skipped.append({"root": q, "reason": "no-trends-interest"})
            continue
        local["roots"].append(q)
        local.setdefault("root_sources", {})[q] = "reddit-ai"
        local.setdefault("root_added_at", {})[q] = now
        existing.add(q)
        entry = {"root": q, "source_post": c.get("source_post", "")}
        if verdict is None:
            entry["trends_unchecked"] = True  # rate-limited: added without gate
        added.append(entry)

    local["count"] = len(local["roots"])
    if args.push and added:
        remote = fetch_github_roots()
        if remote:
            # remote is authoritative; merge keeps our not-yet-pushed additions
            local = merge_stores(remote, local)
            local["count"] = len(local["roots"])
            # drop from `added` anything the remote side had blacklisted
            final = {norm(r) for r in local["roots"]}
            added = [a for a in added if norm(a["root"]) in final]
        STATE.write_text(json.dumps(local, ensure_ascii=False, indent=2))
        ok = push_roots(STATE, f"reddit-ai-monitor: add {len(added)} roots")
        print(json.dumps({"added": added, "skipped": skipped, "pushed": ok,
                          "total": local["count"]}, ensure_ascii=False))
    else:
        STATE.write_text(json.dumps(local, ensure_ascii=False, indent=2))
        print(json.dumps({"added": added, "skipped": skipped, "pushed": False,
                          "total": local["count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
