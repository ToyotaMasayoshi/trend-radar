#!/usr/bin/env python3
"""Collect Google Trends related rising queries, review history, classify.

Pipeline (methodology inferred from the reference daily reports):
  1. For each root: related rising queries in `now 7-d` and `now 1-d` (global).
  2. Candidate pool = ALL rising queries after normalized dedup
     (Breakout or growth >= 1000% is only a display subset, not the gate).
  3. Pre-filter the review queue by intent/root relevance, then review with
     resumable history windows under a fixed request budget:
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
ROOTS_PER_RUN = max(1, int(os.getenv("ROOTS_PER_RUN", "12")))
REVIEW_REQUEST_BUDGET = max(4, int(os.getenv("REVIEW_REQUEST_BUDGET", "40")))
BACKLOG_THRESHOLD = 50
BACKLOG_ROOT_LIMIT = 12
BACKLOG_REVIEW_BUDGET = 64
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
COMMON = {
    "the", "and", "for", "with", "from", "what", "how", "new", "best",
    "to", "of", "in", "on", "at", "by",
    "ai", "app", "game", "song", "video", "news", "tool", "free", "online",
    "generator", "text", "speech", "download", "login", "official", "website",
}
NAVIGATION = {"download", "login", "official", "website", "官网", "下载", "登录"}
RELATION_STOP = COMMON | {"page", "site", "near", "me"}
LOCAL_INTENT = ("near me", "open now", "closest", "附近", "就近")
DOMAIN_NOISE = re.compile(r"(?:https?://|www\.|\.(?:com|gov|org|net|io)(?:\b|/))", re.I)
ERROR_NOISE = ("i wasn't able", "i'm sorry", "as an ai", "i cannot",
               "unable to generate", "error on my side", "something went wrong")
QUESTION_NOISE = ("how ", "what ", "why ", "when ", "where ", "which ")
EDUCATION_INTENT = {"coloring", "worksheet", "lesson", "quiz", "printable", "教材", "练习题", "涂色"}
TOOL_INTENT = {"generator", "converter", "editor", "maker", "api", "app", "tool", "platform", "生成器", "转换器", "编辑器", "工具"}
INFO_INTENT = {"how", "what", "why", "guide", "tutorial", "教程", "怎么", "什么", "为什么"}
COMMERCIAL_INTENT = {"buy", "price", "pricing", "shop", "deal", "review", "best", "购买", "价格", "评测"}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    words = re.sub(r"[-_/]+", " ", text).split()
    words = [w[:-1] if (len(w) > 4 and w.endswith("s")
                        and not w.endswith(("ss", "us", "is"))
                        and w not in PLURAL_EXCEPTIONS) else w for w in words]
    return " ".join(words)


def token_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", normalize(text)))


def is_noise_term(term: str) -> bool:
    """Reject terms that should consume neither review nor feedback slots."""
    raw = unicodedata.normalize("NFKC", term or "").casefold()
    normalized = normalize(raw)
    tokens = token_set(raw)
    return (not tokens or any(marker in normalized for marker in LOCAL_INTENT + ERROR_NOISE)
            or normalized.startswith(QUESTION_NOISE) or bool(tokens & NAVIGATION)
            or bool(DOMAIN_NOISE.search(raw)) or len(tokens) > 6)


def is_entertainment(term: str, roots: list[str]) -> bool:
    """Deterministic policy label from the term and its source roots."""
    text = " ".join(normalize(v) for v in [term, *roots])
    return any(re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", text)
               for marker in ENTERTAINMENT_MARKERS)


def tag_candidate(item: dict, reviewed: bool | None = None) -> dict:
    """Deterministic per-run labels; raw Trends roots are audit data only."""
    query = item.get("q", "")
    roots = sorted(set(item.get("roots", [])))
    query_tokens = token_set(query)
    meaningful = query_tokens - RELATION_STOP
    relevant_roots = [root for root in roots
                      if meaningful & (token_set(root) - RELATION_STOP)]
    evidence = bool(item.get("source_evidence"))
    relation = "direct" if relevant_roots else "evidence" if evidence else "behavioral_unverified"
    reviewed = (bool(item.get("reviewed_at")) or item.get("verdict") not in (None, "pending")) \
        if reviewed is None else reviewed
    normalized = normalize(query)
    if any(marker in normalized for marker in LOCAL_INTENT):
        intent = "local_commercial"
    elif query_tokens & NAVIGATION:
        intent = "navigation"
    elif query_tokens & EDUCATION_INTENT:
        intent = "educational_content"
    elif query_tokens & TOOL_INTENT:
        intent = "tool_product"
    elif query_tokens & INFO_INTENT:
        intent = "informational"
    elif query_tokens & COMMERCIAL_INTENT:
        intent = "commercial_research"
    else:
        intent = "unclassified"
    return {
        "raw_source_root_count": len(roots),
        "relevant_roots": relevant_roots,
        "relevant_root_count": len(relevant_roots),
        "relation_status": relation,
        "search_intent": intent,
        "review_status": "reviewed" if reviewed else "pending",
        "market_status": "unchecked",
    }


def review_eligibility(item: dict) -> tuple[bool, str]:
    """Keep generic noise out while preserving short high-rise coined terms."""
    if is_noise_term(item.get("q", "")):
        return False, "noise"
    tags = tag_candidate(item, False)
    if is_entertainment(item.get("q", ""), []):
        return False, "entertainment"
    if tags["search_intent"] in {"local_commercial", "navigation"}:
        return False, "generic_intent"
    high_rise = bool(item.get("breakout")) or (item.get("growth") or 0) >= 500
    if tags["relation_status"] in {"direct", "evidence"} and \
            (high_rise or bool(item.get("source_evidence"))):
        return True, "related"
    if tags["relation_status"] in {"direct", "evidence"}:
        return False, "below_review_threshold"
    distinctive = token_set(item.get("q", "")) - RELATION_STOP
    coined_rise = bool(item.get("breakout")) or (item.get("growth") or 0) >= HIGH_RISE_CUT
    if coined_rise and tags["search_intent"] == "unclassified" \
            and 0 < len(distinctive) <= 3 and any(len(token) >= 4 for token in distinctive):
        return True, "coined_term_fallback"
    return False, "unverified_relation"


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
                      request_budget: int) -> tuple[int, bool]:
    """Review eligible candidates within a request budget, resuming saved windows."""
    reviews = state.setdefault("reviews", {})
    progress = state.setdefault("review_progress", {})
    pending = sorted(
        (c for c in candidates if normalize(c["q"]) not in reviews
         and review_eligibility(c)[0]),
        key=lambda c: (review_eligibility(c)[1] == "coined_term_fallback",
                       not c["breakout"], -(c["growth"] or 0), normalize(c["q"])),
    )
    start_requests = client.requests
    reviewed = 0

    def fetch(slot: str, term: str, timeframe: str, keywords: list[str], work: dict) -> bool:
        if slot in work:
            return True
        if client.requests - start_requests + 2 > request_budget:
            return False
        result = client.timeline(keywords, timeframe)
        work[slot] = result
        return True

    try:
        for cand in pending:
            display_term = cand["q"]
            key = normalize(display_term)
            work = progress.setdefault(key, {"q": display_term, "windows": {}})
            term = work["q"]  # keep persisted series keys stable across casing variants
            windows = work["windows"]
            if not fetch("yearly", term, "today 12-m", [term], windows):
                break
            if not fetch("five_year", term, "today 5-y", [term], windows):
                break
            yearly = windows["yearly"].get(term, [])
            five_year = windows["five_year"].get(term, [])
            preliminary = classify(yearly, [], [], [], five_year)
            monthly = []
            if preliminary["verdict"] == "revived":
                if not fetch("monthly", term, "today 1-m", [term], windows):
                    break
                monthly = windows["monthly"].get(term, [])
            weekly, weekly_gpts = [], []
            if preliminary["verdict"] in {"new", "revived"}:
                if not fetch("weekly", term, "now 7-d", [term, "gpts"], windows):
                    break
                weekly = windows["weekly"].get(term, [])
                weekly_gpts = windows["weekly"].get("gpts", [])
            result = classify(yearly, monthly, weekly, weekly_gpts, five_year)
            result.update({
                "q": display_term, "reviewed_at": iso_now(),
                "growth": cand["growth"], "breakout": cand["breakout"],
                "formatted": cand["formatted"],
                "roots": cand["roots"], "windows": cand["windows"],
            })
            reviews[key] = result
            progress.pop(key, None)
            reviewed += 1
    except RateLimited:
        return reviewed, True
    return reviewed, False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--root-limit", type=int, default=ROOTS_PER_RUN)
    parser.add_argument("--review-budget", type=int, default=REVIEW_REQUEST_BUDGET)
    args = parser.parse_args()

    roots = json.loads((ROOT / "state" / "roots.json").read_text(encoding="utf-8"))["roots"]
    state = load_json(STATE_FILE, {"roots": {}, "reviews": {}})
    client = TrendsClient()
    rate_limited = False

    candidates = build_candidates(state)
    pending_before = sum(normalize(c["q"]) not in state.get("reviews", {})
                         and review_eligibility(c)[0] for c in candidates)
    backlog_mode = (pending_before > BACKLOG_THRESHOLD
                    and args.root_limit == ROOTS_PER_RUN
                    and args.review_budget == REVIEW_REQUEST_BUDGET)
    root_limit = BACKLOG_ROOT_LIMIT if backlog_mode else args.root_limit
    review_budget = BACKLOG_REVIEW_BUDGET if backlog_mode else args.review_budget
    # Refresh roots first: review backlog must not starve daily discovery.
    refreshed, rising, hit = collect_roots(client, roots, state, root_limit, args.force)
    rate_limited |= hit
    candidates = build_candidates(state)
    reviewed = 0
    if not rate_limited:
        reviewed, hit = review_candidates(client, candidates, state, review_budget)
        rate_limited |= hit

    eligible_pending = sum(normalize(c["q"]) not in state.get("reviews", {})
                           and review_eligibility(c)[0] for c in candidates)
    state["meta"] = {"updated": iso_now(), "requests": client.requests,
                     "rate_limited": rate_limited, "reviewed_this_run": reviewed,
                     "review_request_budget": review_budget,
                     "root_limit": root_limit, "backlog_mode": backlog_mode,
                     "eligible_pending": eligible_pending}
    save_json(STATE_FILE, state)
    if not (refreshed or reviewed):
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
