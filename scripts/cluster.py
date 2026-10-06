#!/usr/bin/env python3
"""Build deterministic, cross-day event clusters from every candidate."""

from __future__ import annotations

import hashlib
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from collect import build_candidates, is_entertainment, load_json, normalize, save_json

STATE_FILE = ROOT / "state" / "state.json"
CLUSTERS_FILE = ROOT / "state" / "clusters.json"

COMMON = {
    "the", "and", "for", "with", "from", "what", "how", "new", "best",
    "to", "of", "in", "on", "at", "by",
    "ai", "app", "game", "song", "video", "news", "tool", "free", "online",
    "generator", "text", "speech", "download", "login", "official", "website",
}
NAVIGATION = {"download", "login", "official", "website", "官网", "下载", "登录"}

HYPOTHESES = {
    "new_domain_discovery": "hypothesis",
    "fast_ai_crawl": "hypothesis",
    "recommendation_exploration": "hypothesis",
    "behavior_feedback": "hypothesis",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def token_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", normalize(text)))


def signature(text: str) -> str:
    return " ".join(sorted(token_set(text)))


def distinctive(text: str) -> set[str]:
    return token_set(text) - COMMON


def can_merge(a: dict, b: dict) -> bool:
    """Require shared collection evidence and a distinctive entity token."""
    if not set(a.get("roots", [])) & set(b.get("roots", [])):
        return False
    ta, tb = token_set(a["q"]), token_set(b["q"])
    shared = distinctive(a["q"]) & distinctive(b["q"])
    if not ta or not tb or not shared:
        return False
    jaccard = len(ta & tb) / len(ta | tb)
    return ta <= tb or tb <= ta or jaccard >= 0.6 or min(len(ta), len(tb)) <= 2


def _best_cluster(item: dict, clusters: list[dict]) -> dict | None:
    matches = [c for c in clusters if can_merge(c["center"], item)]
    if not matches:
        return None
    tq = token_set(item["q"])
    return max(matches, key=lambda c: (
        len(token_set(c["center"]["q"])),
        len(tq & token_set(c["center"]["q"])) / len(tq | token_set(c["center"]["q"])),
        len(set(item.get("roots", [])) & set(c["center"].get("roots", []))),
        signature(c["center"]["q"]),
    ))


def _old_match(members: list[dict], previous: list[dict], today: str) -> dict | None:
    signatures = {signature(m["q"]) for m in members}
    exact = [c for c in previous if signatures & set(c.get("signatures", []))]
    if exact:
        return sorted(exact, key=lambda c: c["id"])[0]

    roots = sorted({r for m in members for r in m.get("roots", [])})
    probe = {"q": min((m["q"] for m in members), key=lambda q: (len(token_set(q)), len(q))),
             "roots": roots}
    recent = []
    for old in previous:
        try:
            age = (date.fromisoformat(today) - date.fromisoformat(old["last_active_date"])).days
        except (KeyError, ValueError):
            age = 999
        candidate = {"q": old.get("canonical_term", ""), "roots": old.get("roots", [])}
        if age <= 7 and old.get("status") != "archived" and can_merge(candidate, probe):
            recent.append(old)
    return sorted(recent, key=lambda c: c["id"])[0] if recent else None


def build_clusters(items: dict | list, previous: list | None = None,
                   today: str | None = None) -> list:
    """Cluster candidates in O(n²); 500-ish rows do not justify an index."""
    previous = previous or []
    today = today or datetime.now(timezone.utc).date().isoformat()
    rows = list(items.values()) if isinstance(items, dict) else list(items)
    rows = sorted((dict(row) for row in rows),
                  key=lambda row: (len(token_set(row["q"])), normalize(row["q"])))

    # ponytail: O(n²) scan is simpler and ample below a few thousand candidates.
    groups: list[dict] = []
    for row in rows:
        group = _best_cluster(row, groups)
        if group:
            group["members"].append(row)
        else:
            groups.append({"center": row, "members": [row]})

    clusters = []
    matched_old_ids = set()
    for group in groups:
        members = group["members"]
        old = _old_match(members, [c for c in previous if c.get("id") not in matched_old_ids], today)
        if old:
            matched_old_ids.add(old["id"])
        candidates = [m for m in members if distinctive(m["q"])] or members
        canonical = min(candidates, key=lambda m: (len(token_set(m["q"])), len(normalize(m["q"]))))["q"]
        display = max(members, key=lambda m: (
            bool(m.get("breakout")), m.get("growth") if isinstance(m.get("growth"), (int, float)) else -1,
            m.get("vs_gpts") if isinstance(m.get("vs_gpts"), (int, float)) else -1,
        ))
        roots = sorted({r for m in members for r in m.get("roots", [])})
        old_variants = {normalize(v["term"]): v for v in (old or {}).get("variants", [])}
        variants = [{
            "term": m["q"], "verdict": m.get("verdict", "pending"),
            "growth": m.get("growth"), "breakout": bool(m.get("breakout")),
            "formatted": m.get("formatted"), "roots": m.get("roots", []),
            "first_seen_at": old_variants.get(normalize(m["q"]), {}).get("first_seen_at", today),
            "vs_gpts": m.get("vs_gpts"),
        } for m in members]
        breakout = any(v["breakout"] for v in variants)
        numeric_growth = [v["growth"] for v in variants if isinstance(v["growth"], (int, float))]
        source_evidence = (old or {}).get("source_evidence", [])
        entertainment = any(is_entertainment(m["q"], m.get("roots", [])) for m in members)
        navigation = all(token_set(m["q"]) & NAVIGATION for m in members)
        low_signal = (not breakout and max(numeric_growth, default=0) < 500
                      and not source_evidence and len(roots) < 2)
        tier = "watch" if entertainment else "low_signal" if low_signal or navigation else "main"
        stage = "S3" if breakout or max(numeric_growth, default=0) >= 1000 else \
                "S2" if len(members) >= 2 or len(roots) >= 2 else "S0"
        sigs = sorted(set((old or {}).get("signatures", [])) |
                      {signature(m["q"]) for m in members})
        cluster_id = (old or {}).get("id") or "ev-" + hashlib.sha1(sigs[0].encode()).hexdigest()[:10]
        clusters.append({
            "id": cluster_id, "canonical_term": canonical, "display_term": display["q"],
            "signatures": sigs, "variants": variants, "variant_count": len(variants),
            "roots": roots, "source_root_count": len(roots), "source_evidence": source_evidence,
            "max_growth": max(numeric_growth, default=None), "breakout": breakout,
            "entertainment": entertainment, "tier": tier,
            "first_seen_date": (old or {}).get("first_seen_date", today),
            "last_active_date": today, "status": "active", "stage": stage,
            "timeline": [{"at": v["first_seen_at"],
                          "event": f"variant first seen: {v['term']}", "kind": "variant"}
                         for v in variants],
            "hypotheses": dict(HYPOTHESES), "built_at": iso_now(),
        })

    for old in previous:
        if old.get("id") in matched_old_ids:
            continue
        archived = dict(old)
        try:
            age = (date.fromisoformat(today) - date.fromisoformat(old["last_active_date"])).days
        except (KeyError, ValueError):
            age = 999
        archived["status"] = "archived" if age > 7 else "inactive"
        clusters.append(archived)

    order = {"S3": 0, "S2": 1, "S0": 2}
    clusters.sort(key=lambda c: (c.get("status") != "active", c.get("tier") != "main",
                                 order.get(c.get("stage"), 9), -c.get("variant_count", 0)))
    return clusters


def main() -> int:
    state = load_json(STATE_FILE, {"roots": {}, "reviews": {}})
    reviews = state.get("reviews", {})
    items = []
    for candidate in build_candidates(state):
        review = reviews.get(normalize(candidate["q"]), {})
        item = dict(candidate, **review)
        item["roots"] = sorted(set(candidate.get("roots", [])) | set(review.get("roots", [])))
        item["windows"] = sorted(set(candidate.get("windows", [])) | set(review.get("windows", [])))
        items.append(item)
    clusters = build_clusters(items, state.get("cluster_history", []))
    state["cluster_history"] = clusters
    save_json(STATE_FILE, state)
    save_json(CLUSTERS_FILE, clusters)
    active = [c for c in clusters if c["status"] == "active"]
    print(f"clusters {len(active)} active; {len(clusters) - len(active)} historical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
