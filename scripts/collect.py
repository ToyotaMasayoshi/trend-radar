#!/usr/bin/env python3
"""Collect Google Trends related rising queries, review history, classify.

Pipeline (methodology inferred from the reference daily reports):
  1. For each root: related rising queries in `now 7-d` and `now 1-d` (global).
  2. Candidate pool = ALL rising queries after normalized dedup
     (Breakout or growth >= 1000% is only a display subset, not the gate).
  3. Review candidates with history windows:
       today 12-m  -> first appearance, recent peak vs prior baseline
       today 1-m   -> last-30d daily line, has it faded
       now 7-d     -> compare against `gpts` in the same chart (relative multiple)
       today 5-y   -> any visible heat more than a year ago
  4. Classify with hard rules only:
       new      : pre-rise peak < 1% of recent-6-week peak, no 5y history
       revived  : 5y history OR recent peak >= 5x prior median
       spike    : revived but only 1-2 active days in 30d and already faded
       watch    : anything else / incomplete evidence

On the first HTTP 429 the round stops immediately: no fake zeros are written,
the last successful output is kept, and stats are printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trends import RateLimited, TrendsClient

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "state" / "state.json"

WINDOWS = ("now 7-d", "now 1-d")
ROOTS_PER_RUN = max(1, int(os.getenv("ROOTS_PER_RUN", "24")))
REVIEWS_PER_RUN = max(1, int(os.getenv("REVIEWS_PER_RUN", "8")))
HIGH_RISE_CUT = 1000  # percent; display subset only
ENTERTAINMENT_MARKERS = (
    "game", "games", "gaming", "roblox", "wordle", "quiz", "puzzle",
    "song", "music", "movie", "film", "anime", "manga", "celebrity",
    "actor", "actress", "singer", "football", "soccer", "basketball",
    "baseball", "cricket", "sports", "tiktok", "youtube", "instagram",
    "游戏", "歌曲", "音乐", "电影", "影视", "动漫", "明星", "演员",
    "歌手", "综艺", "足球", "篮球", "体育", "谜题", "测验",
)
PLURAL_EXCEPTIONS = {"news", "series", "species", "analysis", "status", "gpts"}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    words = re.sub(r"[-_/]+", " ", text).split()
    words = [w[:-1] if (len(w) > 4 and w.endswith("s")
                        and not w.endswith(("ss", "us", "is"))
                        and w not in PLURAL_EXCEPTIONS) else w for w in words]
    return " ".join(words)


def is_entertainment(term: str, roots: list[str]) -> bool:
    """Deterministic policy label from the term and its source roots."""
    text = " ".join(normalize(v) for v in [term, *roots])
    return any(re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", text)
               for marker in ENTERTAINMENT_MARKERS)


def load_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    tmp.replace(path)


def fmt_growth(growth, breakout: bool) -> str:
    if breakout:
        return "飙升"
    if growth is None:
        return "—"
    return f"+{growth}%"


# -- classification ------------------------------------------------------

def classify(yearly: list, monthly: list, weekly: list, weekly_gpts: list,
             five_year: list) -> dict:
    """Apply the hard rules. All series are lists of {"time","value"}.

    Positional approximation: yearly ~= 52 weekly points, recent = last 6;
    five_year ~= 260 weekly points, "a year ago" ~= drop last 52.
    """
    y = [p["value"] for p in yearly]
    recent = y[-6:] if len(y) >= 6 else y
    prior = y[:-6] if len(y) > 6 else []
    recent_peak = max(recent, default=0)
    prior_peak = max(prior, default=0)
    baseline = statistics.median(prior) if prior else 0
    multiple = (min(100.0, round(recent_peak / baseline, 1))
                if baseline else (100.0 if recent_peak else 0.0))

    new_term = recent_peak > 0 and prior_peak < recent_peak * 0.01

    old = five_year[:-52] if len(five_year) > 52 else []
    older_activity = any(p["value"] > 0 for p in old)
    revived = older_activity or (not new_term and recent_peak > 0 and multiple >= 5)

    m = [p["value"] for p in monthly]
    active_days = sum(1 for v in m if v > 0)
    day_peak = max(m, default=0)
    short_spike = bool(revived and 0 < active_days <= 2 and m and m[-1] < day_peak)

    # Both series come from the same Trends chart. The ratio of aligned sums
    # preserves small values and is comparable across candidates using gpts as
    # the fixed reference; it is still a relative proxy, not absolute volume.
    pairs = list(zip((p["value"] for p in weekly),
                     (p["value"] for p in weekly_gpts)))
    term_total = sum(term for term, _ in pairs)
    gpts_total = sum(gpts for _, gpts in pairs)
    vs_gpts = round(term_total / gpts_total, 4) if gpts_total else None

    if short_spike:
        verdict, note = "spike", "30天内仅一两个活跃日且已回落，短时尖峰"
    elif new_term and not older_activity:
        verdict, note = "new", "起量前峰值不到最近6周峰值的1%，且5年内无历史热度"
    elif revived:
        verdict, note = "revived", "一年前有历史热度，或近期峰值≥前期中位数5倍"
    else:
        verdict, note = "watch", "未满足公开硬规则，待观察"

    return {
        "verdict": verdict, "note": note,
        "recent_peak": recent_peak, "prior_peak": prior_peak,
        "baseline": baseline, "recent_to_baseline": multiple,
        "vs_gpts": vs_gpts, "vs_gpts_points": len(pairs),
        "term_7d_total": term_total, "gpts_7d_total": gpts_total,
        "active_days_30d": active_days,
        "yearly": y, "monthly_30d": m,
    }


# -- collection ----------------------------------------------------------

def collect_roots(client: TrendsClient, roots: list, state: dict,
                  limit: int, force: bool) -> tuple[int, int, bool]:
    """Refresh up to `limit` roots, oldest-updated first. Returns
    (refreshed, rising_rows, rate_limited)."""
    cached = state.setdefault("roots", {})
    order = sorted(roots, key=lambda r: cached.get(r, {}).get("updated", ""))
    refreshed, rising = 0, 0
    for root in order:
        if refreshed >= limit and not force:
            break
        if refreshed >= limit:
            break
        record = {"updated": iso_now(), "windows": {}}
        try:
            for window in WINDOWS:
                rows = client.related_rising(root, window)
                record["windows"][window] = [
                    {"q": r["query"], "growth": r["growth"],
                     "breakout": r["breakout"],
                     "formatted": fmt_growth(r["growth"], r["breakout"])}
                    for r in rows
                ]
                rising += len(rows)
        except RateLimited:
            return refreshed, rising, True
        cached[root] = record
        refreshed += 1
    return refreshed, rising, False


def build_candidates(state: dict) -> list:
    """All rising queries, normalized dedup, keep source roots + windows."""
    seen: dict[str, dict] = {}
    for root, record in state.get("roots", {}).items():
        for window, rows in record.get("windows", {}).items():
            for row in rows:
                key = normalize(row["q"])
                item = seen.setdefault(key, {
                    "q": row["q"], "roots": [], "windows": [],
                    "growth": row["growth"], "breakout": row["breakout"],
                    "formatted": row["formatted"],
                })
                if root not in item["roots"]:
                    item["roots"].append(root)
                if window not in item["windows"]:
                    item["windows"].append(window)
                # Keep Breakout if any source saw it, plus the largest numeric rise.
                if row["breakout"]:
                    item["breakout"] = True
                    item["formatted"] = "飙升"
                if (row["growth"] or 0) > (item["growth"] or 0):
                    item["growth"] = row["growth"]
                    if not item["breakout"]:
                        item["formatted"] = row["formatted"]
    return list(seen.values())


def review_candidates(client: TrendsClient, candidates: list, state: dict,
                      limit: int) -> tuple[int, bool]:
    """Review up to `limit` unreviewed candidates. Returns (reviewed, rate_limited)."""
    reviews = state.setdefault("reviews", {})
    pending = sorted(
        (c for c in candidates if normalize(c["q"]) not in reviews),
        key=lambda c: (not c["breakout"], -(c["growth"] or 0), normalize(c["q"])),
    )
    reviewed = 0
    try:
        for cand in pending[:limit]:
            term = cand["q"]
            yearly = client.timeline([term], "today 12-m")[term]
            monthly = client.timeline([term], "today 1-m")[term]
            both = client.timeline([term, "gpts"], "now 7-d")
            five_year = client.timeline([term], "today 5-y")[term]
            result = classify(yearly, monthly, both.get(term, []),
                              both.get("gpts", []), five_year)
            result.update({
                "q": term, "reviewed_at": iso_now(),
                "growth": cand["growth"], "breakout": cand["breakout"],
                "formatted": cand["formatted"],
                "roots": cand["roots"], "windows": cand["windows"],
            })
            reviews[normalize(term)] = result
            reviewed += 1
    except RateLimited:
        return reviewed, True
    return reviewed, False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--root-limit", type=int, default=ROOTS_PER_RUN)
    parser.add_argument("--review-limit", type=int, default=REVIEWS_PER_RUN)
    args = parser.parse_args()

    roots = json.loads((ROOT / "state" / "roots.json").read_text(encoding="utf-8"))["roots"]
    state = load_json(STATE_FILE, {"roots": {}, "reviews": {}})
    client = TrendsClient()
    rate_limited = False

    candidates = build_candidates(state)
    reviewed, hit = review_candidates(client, candidates, state, args.review_limit)
    rate_limited |= hit
    refreshed, rising = 0, 0
    if not rate_limited:
        refreshed, rising, hit = collect_roots(client, roots, state, args.root_limit, args.force)
        rate_limited |= hit
        candidates = build_candidates(state)

    if refreshed or reviewed:
        state["meta"] = {"updated": iso_now(), "requests": client.requests,
                         "rate_limited": rate_limited}
        save_json(STATE_FILE, state)
    else:
        print("No fresh root evidence; keeping the last successful output")

    if rate_limited:
        print("partial failures: Google Trends returned HTTP 429")
    print(f"roots {refreshed}/{len(roots)}; candidates {len(candidates)}; "
          f"reviewed {reviewed}; requests {client.requests}")
    if not state.get("roots"):
        print("No valid collection state; refusing to build an empty report", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
