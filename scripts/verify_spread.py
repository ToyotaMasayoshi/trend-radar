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
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "state" / "state.json"
HN_API = "https://hn.algolia.com/api/v1/search"
HN_DATE_API = "https://hn.algolia.com/api/v1/search_by_date"
DDG_HTML = "https://lite.duckduckgo.com/lite/"
RDAP_COM_API = "https://rdap.verisign.com/com/v1/domain/"
CACHE_TTL_HOURS = 24
UGC_DOMAINS = {
    "reddit.com", "quora.com", "stackoverflow.com", "stackexchange.com",
    "forumotion.com", "discourse.org", "steamcommunity.com",
}


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


def _get_json(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "trend-radar-verify/1.0", **(headers or {})}
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _query_hn_count(term: str, numeric_filter: str | None = None) -> int:
    params = {"query": f'"{term}"', "tags": "story", "hitsPerPage": 1}
    if numeric_filter:
        params["numericFilters"] = numeric_filter
    data = _get_json(f"{HN_DATE_API}?{urllib.parse.urlencode(params)}")
    return int(data.get("nbHits", 0))


def _query_novelty(term: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    six_months_ago = int((now - timedelta(days=183)).timestamp())
    eight_weeks_ago = int((now - timedelta(weeks=8)).timestamp())
    old_hits = _query_hn_count(term, f"created_at_i<{six_months_ago}")
    if old_hits:
        return {"novelty": "old", "hn_old_hits": old_hits,
                "novelty_confidence": "medium"}
    recent_hits = _query_hn_count(term, f"created_at_i>={eight_weeks_ago}")
    if recent_hits:
        return {"novelty": "likely-new", "hn_recent_hits": recent_hits,
                "novelty_confidence": "medium"}
    any_hits = _query_hn_count(term)
    return {
        "novelty": "uncertain" if any_hits else "unknown",
        "hn_hits": any_hits,
        "novelty_confidence": "low",
    }


def _is_ugc(host: str) -> bool:
    host = host.lower().removeprefix("www.")
    return any(host == domain or host.endswith("." + domain)
               for domain in UGC_DOMAINS)


def _query_market_gap(term: str) -> dict:
    params = urllib.parse.urlencode({"q": f'"{term}"'})
    req = urllib.request.Request(
        f"{DDG_HTML}?{params}",
        headers={"User-Agent": "Mozilla/5.0 trend-radar-verify/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        body = response.read().decode("utf-8", "replace")
    links = re.findall(
        r'href=["\']([^"\']+)["\'][^>]+class=["\']result-link["\']', body
    )
    urls = []
    for link in links:
        parsed = urllib.parse.urlparse(link.replace("&amp;", "&"))
        target = urllib.parse.parse_qs(parsed.query).get("uddg", [link])[0]
        url = urllib.parse.unquote(target)
        if (urllib.parse.urlparse(url).hostname or "").endswith("duckduckgo.com"):
            continue
        urls.append(url)
    hosts = [urllib.parse.urlparse(url).hostname or "" for url in urls[:10]]
    ugc_hits = sum(_is_ugc(host) for host in hosts)
    total = len(hosts)
    ratio = round(ugc_hits / total, 2) if total else None
    status = ("gap" if ugc_hits >= 4 else
              "likely_saturated" if total >= 5 and ugc_hits <= 1 else
              "unclear" if total else "unknown")
    return {"ugc_ratio": ratio, "ugc_hits": ugc_hits,
            "organic_results": total, "market_status": status}


def _domain_for(term: str) -> str | None:
    label = re.sub(r"[^a-z0-9]", "", term.lower())
    return f"{label}.com" if 1 < len(label) <= 63 else None


def _query_rdap(term: str) -> dict:
    domain = _domain_for(term)
    if not domain:
        return {"rdap_domain": None, "rdap_status": "unknown"}
    try:
        _get_json(RDAP_COM_API + urllib.parse.quote(domain))
        status = "registered"
    except urllib.error.HTTPError as exc:
        status = "not_found" if exc.code == 404 else "unknown"
    return {"rdap_domain": domain, "rdap_status": status}


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


def verify_opportunities(terms: list[str], limit: int = 20) -> dict[str, dict]:
    """Label selected new opportunities. Cached 24h and never raises."""
    results: dict[str, dict] = {}
    try:
        cache = _load_cache()
        for term in terms[:limit]:
            key = f"opportunity:{term}"
            entry = cache.get(key)
            if entry and _fresh(entry):
                results[term] = entry["result"]
                continue
            result = {"novelty": "unknown", "novelty_confidence": "low",
                      "ugc_ratio": None, "market_status": "unknown",
                      "rdap_domain": _domain_for(term), "rdap_status": "unknown"}
            try:
                result.update(_query_novelty(term))
            except Exception:
                pass
            try:
                result.update(_query_market_gap(term))
            except Exception:
                pass
            try:
                result.update(_query_rdap(term))
            except Exception:
                pass
            cache[key] = {
                "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "result": result,
            }
            results[term] = result
            time.sleep(0.3)
        _save_cache(cache)
    except Exception:
        pass
    return results


if __name__ == "__main__":
    import sys
    terms = sys.argv[1:] or ["mesh avatar studio"]
    print(json.dumps(verify_terms(terms), ensure_ascii=False, indent=1))
