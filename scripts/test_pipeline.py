#!/usr/bin/env python3
"""Offline regression tests. No network. All fixtures are synthetic --
nothing is copied from the reference daily reports.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect import build_candidates, classify, collect_roots, normalize, review_candidates
from cluster import build_clusters, HYPOTHESES
from trends import RateLimited, TrendsClient
import report

ROOT = Path(__file__).resolve().parents[1]
PASS = []


def check(name: str, cond: bool) -> None:
    PASS.append(cond)
    print(("ok  " if cond else "FAIL") + " " + name)


def pts(values: list) -> list:
    return [{"time": f"t{i}", "value": v} for i, v in enumerate(values)]


def test_classify_new():
    yearly = pts([0] * 46 + [0, 0, 0, 0, 80, 100])
    r = classify(yearly, pts([0] * 30), pts([50, 60]), pts([10, 10]),
                 pts([0] * 260))
    check("classify: new term", r["verdict"] == "new")


def test_classify_revived_history():
    yearly = pts([0] * 46 + [0, 0, 0, 0, 80, 100])
    five = pts([0] * 260)
    five[10] = {"time": "t10", "value": 70}  # visible heat >1y ago
    r = classify(yearly, pts([0] * 30), pts([50]), pts([10]), five)
    check("classify: revived via 5y history", r["verdict"] == "revived")


def test_classify_revived_multiple():
    yearly = pts([10] * 46 + [10, 10, 10, 10, 80, 90])
    r = classify(yearly, pts([5] * 30), pts([40]), pts([10]), pts([0] * 260))
    check("classify: revived via 5x baseline", r["verdict"] == "revived"
          and r["recent_to_baseline"] >= 5)


def test_classify_spike():
    yearly = pts([10] * 46 + [10, 10, 10, 10, 80, 90])
    monthly = pts([0] * 28 + [100, 20])  # 2 active days, faded
    r = classify(yearly, monthly, pts([40]), pts([10]), pts([0] * 260))
    check("classify: short spike", r["verdict"] == "spike")


def test_classify_watch():
    yearly = pts([10] * 46 + [10, 10, 12, 14, 20, 30])
    r = classify(yearly, pts([5] * 30), pts([20]), pts([10]), pts([0] * 260))
    check("classify: watch when rules unmet", r["verdict"] == "watch")


def test_vs_gpts_relative():
    r = classify(pts([0] * 46 + [0, 0, 0, 0, 80, 100]), pts([0] * 30),
                 pts([80, 100]), pts([10, 10]), pts([0] * 260))
    check("classify: vs_gpts is a relative multiple",
          r["vs_gpts"] is not None and r["vs_gpts"] > 1)


def test_dedup_keeps_roots():
    state = {"roots": {
        "ai": {"windows": {"now 7-d": [
            {"q": "Strata Qwen", "growth": None, "breakout": True, "formatted": "飙升"}]}},
        "agent": {"windows": {"now 7-d": [
            {"q": "strata  qwen", "growth": 500, "breakout": False, "formatted": "+500%"}]}},
    }}
    cands = build_candidates(state)
    check("dedup: one candidate", len(cands) == 1)
    check("dedup: both roots kept", set(cands[0]["roots"]) == {"ai", "agent"})
    check("dedup: strongest growth kept", cands[0]["growth"] == 500)


def test_cluster_merge_and_split():
    reviews = {
        "strata qwen": {"q": "strata qwen", "verdict": "new", "roots": ["ai"],
                        "reviewed_at": "2026-10-05T00:00:00Z", "growth": None,
                        "breakout": True, "formatted": "飙升"},
        "niko strata": {"q": "niko strata", "verdict": "new", "roots": ["ai"],
                        "reviewed_at": "2026-10-05T01:00:00Z", "growth": 800,
                        "breakout": False, "formatted": "+800%"},
        "banana bread": {"q": "banana bread", "verdict": "watch", "roots": ["food"],
                         "reviewed_at": "2026-10-05T02:00:00Z", "growth": 50,
                         "breakout": False, "formatted": "+50%"},
    }
    clusters = build_clusters(reviews)
    check("cluster: two clusters", len(clusters) == 2)
    strata = next(c for c in clusters if c["canonical_term"] in ("strata qwen", "niko strata"))
    check("cluster: variants merged", len(strata["variants"]) == 2)
    check("cluster: stage S2/S3 for spreading", strata["stage"] in ("S2", "S3"))
    check("cluster: hypotheses labeled not confirmed",
          all(v == "hypothesis" for v in strata["hypotheses"].values()))
    check("cluster: timeline is correlation-only",
          all("event" in t for t in strata["timeline"]))


def test_rate_limited_stops():
    class DeadClient:
        def related_rising(self, root, window):
            raise RateLimited("429")
    refreshed, rising, hit = collect_roots(DeadClient(), ["ai"], {}, 526, True)
    check("429: stops round, reports flag", hit and refreshed == 0 and rising == 0)


def test_trends_client_warms_google_session():
    opener = MagicMock()
    response = MagicMock()
    response.__enter__.return_value = response
    opener.open.return_value = response
    with patch("trends.urllib.request.build_opener", return_value=opener):
        client = TrendsClient(delay=0)
    request = opener.open.call_args.args[0]
    check("trends: warms Google session",
          request.full_url == "https://www.google.com/" and client.requests == 1)


def test_review_prioritizes_breakout():
    class FakeClient:
        def timeline(self, keywords, timeframe):
            return {keyword: pts([1]) for keyword in keywords}

    candidates = [
        {"q": "low", "growth": 10, "breakout": False, "formatted": "+10%",
         "roots": ["a"], "windows": ["now 7-d"]},
        {"q": "breakout", "growth": None, "breakout": True, "formatted": "飙升",
         "roots": ["a"], "windows": ["now 7-d"]},
    ]
    state = {}
    reviewed, limited = review_candidates(FakeClient(), candidates, state, 1)
    check("review: breakout first",
          reviewed == 1 and not limited and "breakout" in state["reviews"])


def test_empty_report_is_rejected():
    state_f = ROOT / "state" / "state.json"
    out_f = ROOT / "site" / "data" / "daily.json"
    bak_s = state_f.read_text(encoding="utf-8") if state_f.exists() else None
    bak_o = out_f.read_text(encoding="utf-8") if out_f.exists() else None
    try:
        state_f.unlink(missing_ok=True)
        out_f.parent.mkdir(parents=True, exist_ok=True)
        out_f.write_text("last successful report", encoding="utf-8")
        check("report: empty state rejected", report.main() == 1)
        check("report: last output preserved",
              out_f.read_text(encoding="utf-8") == "last successful report")
    finally:
        if bak_s is None:
            state_f.unlink(missing_ok=True)
        else:
            state_f.write_text(bak_s, encoding="utf-8")
        if bak_o is None:
            out_f.unlink(missing_ok=True)
        else:
            out_f.write_text(bak_o, encoding="utf-8")


def test_report_renders():
    state_f = ROOT / "state" / "state.json"
    clu_f = ROOT / "state" / "clusters.json"
    bak_s = state_f.read_text(encoding="utf-8") if state_f.exists() else None
    bak_c = clu_f.read_text(encoding="utf-8") if clu_f.exists() else None
    try:
        state_f.write_text(json.dumps({
            "roots": {"ai": {"windows": {"now 7-d": [
                {"q": "strata qwen", "growth": None, "breakout": True,
                 "formatted": "飙升"},
                {"q": "pending term", "growth": 500, "breakout": False,
                 "formatted": "+500%"}]}}},
            "reviews": {"strata qwen": {
                "q": "strata qwen", "verdict": "new", "note": "t",
                "growth": None, "breakout": True, "formatted": "飙升",
                "roots": ["ai"], "windows": ["now 7-d"], "vs_gpts": 3.2,
                "recent_to_baseline": 100.0, "reviewed_at": "2026-10-05T00:00:00Z",
                "yearly": [0] * 52, "monthly_30d": [0] * 30}},
            "meta": {"requests": 5, "rate_limited": False},
        }), encoding="utf-8")
        clu_f.write_text(json.dumps(build_clusters(
            json.loads(state_f.read_text())["reviews"])), encoding="utf-8")
        assert report.main() == 0
        daily = json.loads((ROOT / "site" / "data" / "daily.json").read_text(encoding="utf-8"))
        md = (ROOT / "reports").glob("*.md")
        check("report: daily.json valid", daily["stats"]["candidates"] == 2)
        check("report: unreviewed candidates stay pending",
              any(row["q"] == "pending term" and row["verdict"] == "pending"
                  for row in daily["candidates"]))
        check("report: markdown written", any(md))
        check("report: no secrets leaked",
              "Cookie" not in json.dumps(daily) and "Traceback" not in json.dumps(daily))
    finally:
        if bak_s is None:
            state_f.unlink(missing_ok=True)
        else:
            state_f.write_text(bak_s, encoding="utf-8")
        if bak_c is None:
            clu_f.unlink(missing_ok=True)
        else:
            clu_f.write_text(bak_c, encoding="utf-8")
        # remove synthetic test outputs
        (ROOT / "site" / "data" / "daily.json").unlink(missing_ok=True)
        for p in (ROOT / "reports").glob("*.md"):
            p.unlink()


def main() -> int:
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    failed = PASS.count(False)
    print(f"\n{len(PASS) - failed}/{len(PASS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
