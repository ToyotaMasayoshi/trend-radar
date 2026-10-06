#!/usr/bin/env python3
"""Verify propagation evidence for candidates via Hacker News (Algolia API).

Methodology step 3 ("verify propagation") requires checking, in time order:
social discussion -> Google Trends rise -> related search terms -> site traffic.
This script covers the "social discussion" end with a free, keyless source:
Hacker News stories mentioning the term.

For each term it returns:
  {hn_hits, top_points, top_title, top_url, earliest, verdict}
where verdict is "传播证据" (evidence found) or "未观测到" (none observed).

Usage:
  from verify_spread import verify_terms
  results = verify_terms(["mesh avatar studio", ...], limit=30)

Results are cached in state/state.json under "spread" for 24h so the hourly
workflow does not re-query the same terms every run. Never raises: on any
failure it returns {} and the caller continues without spread data.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "state" / "state.json"
HN_API = "https://hn.algolia.com/api/v1/search"
CACHE_TTL_HOURS = 24


def _query_hn(term: str) -> dict:
    params = urllib.parse.urlencode(
        {"query": term, "tags": "story", "hitsPerPage": 5}
    )
    req = urllib.request.Request(
        f"{HN_API}?{params}", headers={"User-Agent": "trend-radar-verify/1.0"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    hits = data.get("hits", [])
    if not hits:
        return {"hn_hits": 0, "top_points": 0, "top_title": None,
                "top_url": None, "earliest": None, "verdict": "未观测到"}
    top = max(hits, key=lambda h: h.get("points") or 0)
    earliest = min(
        (h.get("created_at") for h in hits if h.get("created_at")),
        default=None,
    )
    return {
        "hn_hits": data.get("nbHits", len(hits)),
        "top_points": top.get("points") or 0,
        "top_title": top.get("title"),
        "top_url": top.get("url"),
        "earliest": earliest,
        "verdict": "传播证据",
    }


def _load_cache() -> dict:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return state.get("spread", {})
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8")) \
            if STATE_FILE.exists() else {}
        state["spread"] = cache
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _fresh(entry: dict) -> bool:
    try:
        ts = datetime.fromisoformat(entry["checked_at"].replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        return age_h < CACHE_TTL_HOURS
    except Exception:
        return False


def verify_terms(terms: list[str], limit: int = 30) -> dict[str, dict]:
    """Return {term: spread_result}. Cached 24h. Never raises."""
    results: dict[str, dict] = {}
    try:
        cache = _load_cache()
        todo = []
        for t in terms[:limit]:
            entry = cache.get(t)
            if entry and _fresh(entry):
                results[t] = entry["result"]
            else:
                todo.append(t)
        for t in todo:
            try:
                res = _query_hn(t)
            except Exception:
                res = {"hn_hits": 0, "top_points": 0, "top_title": None,
                       "top_url": None, "earliest": None, "verdict": "查询失败"}
                results[t] = res
                continue
            cache[t] = {"checked_at": datetime.now(timezone.utc).isoformat()
                        .replace("+00:00", "Z"), "result": res}
            results[t] = res
            time.sleep(0.3)  # be polite to the free API
        _save_cache(cache)
    except Exception:
        pass
    return results


if __name__ == "__main__":
    import sys
    terms = sys.argv[1:] or ["mesh avatar studio"]
    print(json.dumps(verify_terms(terms), ensure_ascii=False, indent=1))
