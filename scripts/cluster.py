#!/usr/bin/env python3
"""Build event clusters from reviewed candidates.

An event cluster groups keyword variants that point at the same underlying
event (product launch, repo, incident...), with source evidence, a timeline
(correlation only, never auto-claimed causality), a stage, and labeled
hypotheses.

Merge rules (priority order):
  1. same explicit entity id
  2. same source URL (github repo / article)
  3. co-occurrence on a source page
  4. upstream alias
  5. manual confirmation
Pure string-similarity auto-merge is FORBIDDEN. The MVP fallback groups
variants only when they share at least one source root AND a distinctive
common token (len>=4, not a stopword) -- e.g. "strata qwen" / "niko strata".

Stages: S0 detected -> S1 origin-linked -> S2 spreading -> S3 search-rising
        -> S4 serp-validated -> S5 page-testing -> S6 search-converted.
opportunity is for ordering inside a stage only, never the final verdict.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "state" / "state.json"
CLUSTERS_FILE = ROOT / "state" / "clusters.json"

STOPWORDS = {
    "the", "and", "for", "with", "from", "what", "how", "free", "best",
    "new", "app", "apps", "online", "2026", "2025", "game", "games",
}

# Running hypotheses about AI discovery/recommendation. They are hypotheses:
# shown as hypothesis, never as confirmed facts.
HYPOTHESES = {
    "new_domain_discovery": "hypothesis",   # H1: AI may discover new sites via domains/certs/pages
    "fast_ai_crawl": "hypothesis",          # H2: AI crawlers may fetch new pages faster than Google
    "recommendation_exploration": "hypothesis",  # H3: AI may keep exploring new products per need
    "behavior_feedback": "hypothesis",      # H4: user follow-ups may shift recommendations
}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def tokens(text: str) -> set:
    norm = unicodedata.normalize("NFKC", text or "").casefold()
    words = re.findall(r"[a-z0-9\u4e00-\u9fff]{2,}", norm)
    return {w for w in words if len(w) >= 4 and w not in STOPWORDS}


def distinctive_overlap(a: dict, b: dict) -> set:
    if not set(a.get("roots", [])) & set(b.get("roots", [])):
        return set()
    return tokens(a["q"]) & tokens(b["q"])


def build_clusters(reviews: dict) -> list:
    items = [dict(v, _key=k) for k, v in reviews.items()]
    clusters: list[dict] = []
    used: set = set()

    for item in items:
        if item["_key"] in used:
            continue
        members = [item]
        used.add(item["_key"])
        for other in items:
            if other["_key"] in used:
                continue
            if distinctive_overlap(item, other):
                members.append(other)
                used.add(other["_key"])
        # canonical = shortest variant (usually the entity name)
        members.sort(key=lambda m: len(m["q"]))
        canonical = members[0]["q"]
        variants = [{
            "term": m["q"],
            "verdict": m["verdict"],
            "growth": m.get("growth"),
            "breakout": m.get("breakout"),
            "formatted": m.get("formatted"),
            "roots": m.get("roots", []),
            "first_seen_at": m.get("reviewed_at"),
            "vs_gpts": m.get("vs_gpts"),
        } for m in members]
        roots = sorted({r for m in members for r in m.get("roots", [])})
        breakout_any = any(m.get("breakout") for m in members)
        high = any((m.get("growth") or 0) >= 1000 for m in members)

        if breakout_any or high:
            stage = "S3"
        elif len(members) >= 2 or len(roots) >= 2:
            stage = "S2"
        else:
            stage = "S0"

        timeline = [{
            "at": m.get("reviewed_at"),
            "event": f"variant first seen: {m['q']}",
            "kind": "variant",
        } for m in sorted(members, key=lambda m: m.get("reviewed_at") or "")]
        if breakout_any or high:
            timeline.append({"at": members[0].get("reviewed_at"),
                             "event": "search rising (breakout/>=1000%)",
                             "kind": "search"})

        clusters.append({
            "id": "ev-" + re.sub(r"[^a-z0-9]+", "-", canonical.lower()).strip("-"),
            "canonical_term": canonical,
            "variants": variants,
            "roots": roots,
            "source_evidence": [],       # filled by web/manual sources later
            "timeline": timeline,        # correlation only
            "stage": stage,
            "hypotheses": dict(HYPOTHESES),
            "evidence_confidence": {
                "origin": "unknown",
                "propagation": "likely" if len(members) >= 2 else "unknown",
                "ai_mechanism": "hypothesis",
            },
            "experiment": None,
            "built_at": iso_now(),
        })
    # S3 first, then S2, then S0; inside a stage, more variants first
    order = {"S3": 0, "S2": 1, "S0": 2}
    clusters.sort(key=lambda c: (order.get(c["stage"], 9), -len(c["variants"])))
    return clusters


def main() -> int:
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) \
        if STATE_FILE.exists() else {"reviews": {}}
    clusters = build_clusters(state.get("reviews", {}))
    CLUSTERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CLUSTERS_FILE.write_text(json.dumps(clusters, ensure_ascii=False), encoding="utf-8")
    stages: dict[str, int] = {}
    for c in clusters:
        stages[c["stage"]] = stages.get(c["stage"], 0) + 1
    print(f"clusters {len(clusters)}; stages {stages}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
