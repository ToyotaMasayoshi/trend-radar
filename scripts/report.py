#!/usr/bin/env python3
"""Render the daily report: site/data/daily.json + reports/YYYY-MM-DD.md.

Sections mirror the reference daily:
  1. title + round summary
  2. high-rise table (Breakout or >=1000%)
  3. new terms
  4. revived terms
  5. short spikes
  6. reviewed-but-excluded / watch
  7. event clusters
  8. raw per-root rising lists
  9. data boundaries

Never writes credentials, cookies, tracebacks, or network internals.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from collect import build_candidates, is_entertainment, normalize
from cluster import tag_candidate

STATE_FILE = ROOT / "state" / "state.json"
CLUSTERS_FILE = ROOT / "state" / "clusters.json"

VERDICT_CN = {"new": "新词", "revived": "老词二次爆火", "spike": "短时尖峰",
              "watch": "待观察", "pending": "待复核"}


def trends_link(term: str) -> str:
    q = urllib.parse.quote(term)
    return f"https://trends.google.com/trends/explore?date=now%207-d&q={q}"


def sparkline(values: list) -> str:
    peak = max(values, default=0)
    blocks = "▁▂▃▄▅▆▇"
    if not peak:
        return "".join("·" for _ in values)
    return "".join(blocks[min(6, int(v / peak * 6))] for v in values)


def format_vs_gpts(value) -> str:
    if value is None:
        return "—"
    if 0 < value < 0.001:
        return "<0.001×"
    return f"{value:.4f}".rstrip("0").rstrip(".") + "×"


def apply_policy(item: dict, reviewed: bool) -> dict:
    row = dict(item)
    row["reviewed"] = reviewed
    row["entertainment"] = is_entertainment(row.get("q", ""), row.get("roots", []))
    if row["entertainment"]:
        trend_verdict = row.get("verdict") if reviewed else None
        row["trend_verdict"] = trend_verdict
        row["verdict"] = "watch"
        row["note"] = ("娱乐性信息，按方法论列入观察；趋势复核结论："
                       + VERDICT_CN.get(trend_verdict, "尚未复核"))
    row["vs_gpts_display"] = format_vs_gpts(row.get("vs_gpts"))
    row.update(tag_candidate(row, reviewed))
    return row


def ranking_key(row: dict) -> tuple:
    growth = float("inf") if row.get("breakout") else row.get("growth")
    growth = growth if isinstance(growth, (int, float)) else -1
    volume = row.get("vs_gpts")
    volume = volume if isinstance(volume, (int, float)) else -1
    return (-growth, -volume, normalize(row.get("q", "")))


def main() -> int:
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    cluster_history = json.loads(CLUSTERS_FILE.read_text(encoding="utf-8")) if CLUSTERS_FILE.exists() else []
    clusters = [c for c in cluster_history if c.get("status", "active") == "active"]
    reviews = state.get("reviews", {})
    roots_state = state.get("roots", {})
    meta = state.get("meta", {})
    if not roots_state:
        print("No collected roots; refusing to overwrite the last report", file=sys.stderr)
        return 1
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    pending = [apply_policy(dict(c, verdict="pending", note="已采集，等待历史复核"), False)
               for c in build_candidates(state) if normalize(c["q"]) not in reviews]
    reviewed_rows = [apply_policy(r, True) for r in reviews.values()]
    rows = sorted([*reviewed_rows, *pending], key=ranking_key)
    by_verdict: dict[str, list] = {}
    for r in rows:
        by_verdict.setdefault(r["verdict"], []).append(r)
    high = [r for r in rows if not r.get("entertainment") and
            (r.get("breakout") or (r.get("growth") or 0) >= 1000)]
    rising_total = sum(len(rec.get("windows", {}).get(w, []))
                       for rec in roots_state.values() for w in ("now 7-d", "now 1-d"))

    # -- coverage (P0): honest data-completeness annotation ------------------
    roots_pool_file = ROOT / "state" / "roots.json"
    try:
        roots_total = len(json.loads(roots_pool_file.read_text(encoding="utf-8"))["roots"])
    except Exception:
        roots_total = 526
    coverage_pct = round(len(roots_state) / roots_total * 100, 1) if roots_total else 0.0
    data_complete = coverage_pct >= 50 and not meta.get("rate_limited", False)

    # -- spread verification (P1): HN as social-discussion evidence ---------
    spread: dict[str, dict] = {}
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from verify_spread import verify_terms
        top_terms = [r["q"] for r in
                     sorted(rows, key=lambda r: r.get("growth") or 0, reverse=True)[:30]]
        spread = verify_terms(top_terms, limit=30)
    except Exception:
        spread = {}

    # -- daily.json -------------------------------------------------------
    main_clusters = [c for c in clusters if c.get("tier") == "main"]
    watch_clusters = [c for c in clusters if c.get("tier") == "watch"]
    low_clusters = [c for c in clusters if c.get("tier") == "low_signal"]
    top_clusters = sorted(main_clusters, key=lambda c: (
        not c.get("breakout"), -(c.get("max_growth") or 0),
        -c.get("source_root_count", 0), normalize(c.get("canonical_term", ""))))[:5]
    daily = {
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "Google Trends Explore related rising queries and interest over time",
        "windows": ["now 7-d", "now 1-d"],
        "review_windows": ["today 12-m", "today 1-m", "now 7-d vs gpts", "today 5-y"],
        "method": {
            "candidate_rule": "All related rising queries after normalized dedup; "
                              "high-rise subset is Breakout or growth >= 1000%",
            "new_rule": "pre-rise peak < 1% of recent-6-week peak and no 5y history",
            "revived_rule": "history >1y ago, or recent peak >= 5x prior median",
            "spike_rule": "1-2 active days in 30d and already faded",
            "entertainment_rule": "entertainment terms stay in watch regardless of trend verdict",
            "ranking_rule": "growth descending; optional relative-volume sort uses 7d vs gpts",
            "volume_boundary": "vs gpts is a same-chart relative proxy, not absolute search volume",
            "cluster_rule": "shared source root + distinctive token; subset/Jaccard are supporting evidence",
            "relation_rule": "raw Trends hits are audited separately; only token/entity-matched roots count",
            "intent_rule": "local/navigation noise cannot bypass relevance through Breakout",
            "review_rule": "unreviewed related queries stay in watch or low-signal tiers",
            "low_signal_rule": "growth <500%, not Breakout, no source evidence, fewer than 2 relevant roots",
        },
        "stats": {
            "roots_total": roots_total,
            "roots_cached": len(roots_state),
            "coverage_pct": coverage_pct,
            "data_complete": data_complete,
            "rising_terms": rising_total,
            "candidates": len(rows),
            "reviewed": len(reviews),
            "high_rise": len(high),
            "by_verdict": {k: len(v) for k, v in by_verdict.items()},
            "requests": meta.get("requests", 0),
            "rate_limited": meta.get("rate_limited", False),
            "active_clusters": len(clusters),
            "archived_clusters": sum(c.get("status") == "archived" for c in cluster_history),
            "dedup_rate": round(1 - len(clusters) / len(rows), 4) if rows else 0,
            "main_clusters": len(main_clusters),
            "watch_clusters": len(watch_clusters),
            "low_signal_clusters": len(low_clusters),
        },
        "candidates": [
            {"q": r["q"], "verdict": r["verdict"], "verdict_cn": VERDICT_CN[r["verdict"]],
             "growth": r.get("growth"), "breakout": r.get("breakout"),
             "formatted": r.get("formatted"), "roots": r.get("roots", []),
             "windows": r.get("windows", []), "vs_gpts": r.get("vs_gpts"),
             "vs_gpts_display": r.get("vs_gpts_display"),
             "vs_gpts_points": r.get("vs_gpts_points"),
             "reviewed": r.get("reviewed"), "entertainment": r.get("entertainment"),
             "raw_source_root_count": r.get("raw_source_root_count", len(r.get("roots", []))),
             "relevant_roots": r.get("relevant_roots", []),
             "relevant_root_count": r.get("relevant_root_count", 0),
             "relation_status": r.get("relation_status"),
             "search_intent": r.get("search_intent"),
             "review_status": r.get("review_status"),
             "market_status": r.get("market_status"),
             "trend_verdict": r.get("trend_verdict"),
             "recent_to_baseline": r.get("recent_to_baseline"),
             "note": r.get("note"),
             "spread": spread.get(r["q"], {"verdict": "未验证"}),
             "yearly_12m": r.get("yearly", []), "daily_30d": r.get("monthly_30d", [])}
            for r in rows
        ],
        "clusters": clusters,
        "top5": [{"cluster_name": c["canonical_term"], "display_term": c["display_term"],
                  "growth": c.get("max_growth"), "breakout": c.get("breakout"),
                  "roots": c.get("source_root_count", 0)} for c in top_clusters],
    }
    out_json = ROOT / "site" / "data" / "daily.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(daily, ensure_ascii=False), encoding="utf-8")

    # -- markdown ----------------------------------------------------------
    L: list[str] = []
    A = L.append
    new_n = len(by_verdict.get("new", []))
    rev_n = len(by_verdict.get("revived", []))
    A(f"# {today} 新词日报\n")
    coverage_note = ("本轮覆盖率 %.1f%%（数据完整）" % coverage_pct if data_complete
                     else "**本轮覆盖率 %.1f%%，数据不全，以下结论基于部分数据**" % coverage_pct)
    A(f"地区：全球 · 词根已查 {len(roots_state)}/{roots_total} 个 · {coverage_note} · "
      f"时间窗 now 7-d, now 1-d · 上升词 {rising_total} 条 → 候选 {len(rows)} 个 · "
      f"已复核 {len(reviews)} 个 · **新词 {new_n} 个 · 老词二次爆火 {rev_n} 个**\n")
    A("> 趋势数据是 0~100 的相对热度，不是搜索量；「vs gpts」是同一张图里最近 7 天热度的倍数。\n")
    A("> 新词只说明「刚出现」，能不能做还要看搜索量、KD 和 SERP。\n")

    def table(title: str, items: list) -> None:
        A(f"\n## {title}（共 {len(items)} 个）\n")
        A("\n| 上升词 | 涨幅 | 采集/有效词根 | 搜索意图 | 关联状态 | 复核结论 | 近7天 vs gpts | 传播证据 | 备注 |")
        A("\n|---|---|---|---|---|---|---|---|---|")
        for r in items:
            roots = "、".join(r.get("roots", [])[:4])
            sp = spread.get(r["q"], {})
            sp_txt = ("—" if sp.get("verdict") in (None, "未验证")
                      else f"{sp['verdict']}（HN {sp.get('hn_hits', 0)}）")
            A(f"\n| [{r['q']}]({trends_link(r['q'])}) | {r.get('formatted') or '—'} | "
              f"{len(r.get('roots', []))}/{r.get('relevant_root_count', 0)} | "
              f"{r.get('search_intent')} | {r.get('relation_status')} | "
              f"{VERDICT_CN[r['verdict']]} | {r['vs_gpts_display']} | {sp_txt} | {r.get('note') or ''} |")
        A("\n")

    table("高涨幅上升词（≥1000% 或飙升）", high)
    table("新词", by_verdict.get("new", []))
    table("老词二次爆火", by_verdict.get("revived", []))
    table("短时尖峰", by_verdict.get("spike", []))
    table("复核后排除 / 待观察", by_verdict.get("watch", []))
    table("待复核", by_verdict.get("pending", []))

    A("\n## 今日 Top 5 事件\n")
    for index, c in enumerate(top_clusters, 1):
        rise = "飙升" if c.get("breakout") else f"+{c.get('max_growth') or 0}%"
        A(f"\n{index}. **{c['canonical_term']}**：{rise}，{c['variant_count']} 个变体，"
          f"来自 {c['source_root_count']} 个词根。")
    A("\n")

    def cluster_table(title: str, items: list) -> None:
        A(f"\n## {title}（共 {len(items)} 个）\n")
        A("\n| 事件 | 展示词 | 变体数 | 峰值 | 采集/有效词根 | 搜索意图 | 关联 | 首次/最后活跃 |")
        A("\n|---|---|---|---|---|---|---|---|")
        for c in items:
            rise = "飙升" if c.get("breakout") else f"+{c.get('max_growth') or 0}%"
            A(f"\n| {c['canonical_term']} | {c['display_term']} | {c['variant_count']} | {rise} | "
              f"{c.get('raw_source_root_count', 0)}/{c.get('relevant_root_count', 0)} | "
              f"{','.join(c.get('search_intents', []))} | {c.get('relation_status')} | "
              f"{c['first_seen_date']} / {c['last_active_date']} |")
        A("\n")

    cluster_table("主榜事件", main_clusters)
    cluster_table("娱乐 / 观察事件", watch_clusters)
    cluster_table("低信号附录", low_clusters)

    A("\n## 各词根上升词（原始）\n")
    for root in sorted(roots_state):
        rec = roots_state[root]
        parts = []
        for w in ("now 7-d", "now 1-d"):
            for row in rec.get("windows", {}).get(w, []):
                parts.append(f"{row['q']} {row['formatted']}")
        if parts:
            A(f"\n- **{root}**：{'、'.join(parts)}")
    A("\n")

    A("\n## 数据边界说明\n")
    A("\n- Google Trends 数值为同一图表内 0~100 相对热度，不是绝对搜索量。")
    A("\n- `now 7-d` / `now 1-d` 为滚动窗口；历史回放与采集当时不一定是同一快照。")
    A("\n- vs gpts 为同图相对倍数。短时尖峰须由 30 天日线直接证明，证据不足只标待观察。")
    A("\n- 假设类字段（如 AI 发现/推荐机制）为运行假设，标注为 hypothesis，不作为已确认事实。\n")
    A("\n- 传播证据来自 Hacker News 公开故事搜索（免费 Algolia API），仅覆盖英文技术社区讨论；"
      "「未观测到」不等于没有传播。\n")

    out_md = ROOT / "reports" / f"{today}.md"
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("".join(L), encoding="utf-8")
    print(f"wrote {out_json} and {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
