#!/usr/bin/env python3
"""Minimal Google Trends client (standard library only).

Wraps the unofficial trends.google.com API endpoints:
  explore -> widget tokens -> widgetdata/relatedsearches | widgetdata/multiline

The client first opens google.com to establish the normal Google session
cookies required by Trends.

On HTTP 429 a RateLimited error is raised immediately. Callers must stop the
round, keep the last successful output, and never synthesize fake zeros.
"""

from __future__ import annotations

import http.cookiejar
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://trends.google.com/trends/api/"
REQUEST_DELAY = 5.0  # plus 0-1s jitter; pacing, not evasion
MAX_RETRIES = 1


class RateLimited(RuntimeError):
    """Google Trends returned HTTP 429. Stop the round."""


class RequestBudgetExceeded(RuntimeError):
    """The per-run Google request cap was reached."""


def _google_json(payload: bytes) -> dict:
    text = payload.decode("utf-8", "replace")
    text = re.sub(r"^\)\]\}',?\s*", "", text)  # strip anti-XSSI prefix
    return json.loads(text)


class TrendsClient:
    def __init__(self, delay: float = REQUEST_DELAY, retries: int = MAX_RETRIES,
                 max_requests: int | None = None) -> None:
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.delay = delay
        self.retries = retries
        self.max_requests = max_requests
        self.requests = 0
        request = urllib.request.Request("https://www.google.com/", headers={
            "User-Agent": "Mozilla/5.0 (compatible; TrendRadar/1.0)",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._read(request, "Google session")

    def _read(self, request: urllib.request.Request, label: str) -> bytes:
        """Read once, with bounded 429 retries that respect Retry-After."""
        for attempt in range(self.retries + 1):
            max_requests = getattr(self, "max_requests", None)
            if max_requests is not None and self.requests >= max_requests:
                raise RequestBudgetExceeded("Google request budget exhausted")
            self.requests += 1
            try:
                with self.opener.open(request, timeout=25) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                if error.code != 429:
                    raise
                if attempt >= self.retries:
                    raise RateLimited(f"{label} returned HTTP 429") from error
                retry_after = error.headers.get("Retry-After") if error.headers else None
                try:
                    wait = float(retry_after)
                except (TypeError, ValueError):
                    wait = 60 * (attempt + 1) + random.uniform(0, 5)
                time.sleep(max(1, wait))
        raise AssertionError("unreachable")

    def get(self, path: str, params: dict) -> dict:
        url = BASE + path + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; TrendRadar/1.0)",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://trends.google.com/trends/explore",
        })
        result = _google_json(self._read(request, "Google Trends"))
        if self.delay:
            time.sleep(self.delay + random.uniform(0, 1))
        return result

    def _explore(self, keywords: list, timeframe: str) -> list:
        body = {
            "comparisonItem": [
                {"keyword": kw, "geo": "", "time": timeframe} for kw in keywords
            ],
            "category": 0,
            "property": "",
        }
        payload = self.get("explore", {
            "hl": "en-US", "tz": "0",
            "req": json.dumps(body, ensure_ascii=False, separators=(",", ":")),
        })
        return payload.get("widgets", [])

    def _widget(self, widgets: list, widget_id: str, endpoint: str) -> dict:
        widget = next((w for w in widgets if w.get("id") == widget_id), None)
        if not widget:
            return {}
        return self.get("widgetdata/" + endpoint, {
            "hl": "en-US", "tz": "0",
            "req": json.dumps(widget["request"], ensure_ascii=False, separators=(",", ":")),
            "token": widget["token"],
        })

    # -- public API ------------------------------------------------------

    def related_rising_many(self, roots: list[str], timeframe: str) -> dict[str, list]:
        """Related rising queries for up to four roots, sharing one explore call."""
        widgets = [w for w in self._explore(roots, timeframe)
                   if w.get("id") == "RELATED_QUERIES"]
        result = {root: [] for root in roots}
        # Google returns one RELATED_QUERIES widget per comparison item, in order.
        for root, widget in zip(roots, widgets):
            payload = self.get("widgetdata/relatedsearches", {
                "hl": "en-US", "tz": "0",
                "req": json.dumps(widget["request"], ensure_ascii=False,
                                  separators=(",", ":")),
                "token": widget["token"],
            })
            ranked = payload.get("default", {}).get("rankedList", [])
            rows = ranked[1].get("rankedKeyword", []) if len(ranked) > 1 else []
            result[root] = [{
                "query": (row.get("query") or "").strip(),
                "growth": row.get("value"),
                "breakout": row.get("value") is None
                or str(row.get("formattedValue", "")).lower() == "breakout",
            } for row in rows if (row.get("query") or "").strip()]
        return result

    def related_rising(self, root: str, timeframe: str) -> list:
        """Backward-compatible single-root wrapper."""
        return self.related_rising_many([root], timeframe)[root]

    def timeline(self, keywords: list, timeframe: str) -> dict:
        """Interest-over-time for keywords in one window.

        Returns {keyword: [{"time": iso, "value": int}]}. Values are 0-100
        relative within the chart, NOT absolute search volume.
        """
        widgets = self._explore(keywords, timeframe)
        payload = self._widget(widgets, "TIMESERIES", "multiline")
        result = {kw: [] for kw in keywords}
        for point in payload.get("default", {}).get("timelineData", []):
            values = point.get("value", [])
            for index, kw in enumerate(keywords):
                if index < len(values):
                    result[kw].append({"time": point.get("formattedTime", ""), "value": values[index]})
        return result
