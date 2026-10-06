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
REQUEST_DELAY = 2.0  # seconds between requests; pacing, not evasion
MAX_RETRIES = 2


class RateLimited(RuntimeError):
    """Google Trends returned HTTP 429. Stop the round."""


def _google_json(payload: bytes) -> dict:
    text = payload.decode("utf-8", "replace")
    text = re.sub(r"^\)\]\}',?\s*", "", text)  # strip anti-XSSI prefix
    return json.loads(text)


class TrendsClient:
    def __init__(self, delay: float = REQUEST_DELAY, retries: int = MAX_RETRIES) -> None:
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.delay = delay
        self.retries = retries
        self.requests = 0
        request = urllib.request.Request("https://www.google.com/", headers={
            "User-Agent": "Mozilla/5.0 (compatible; TrendRadar/1.0)",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._read(request, "Google session")

    def _read(self, request: urllib.request.Request, label: str) -> bytes:
        """Read once, with bounded 429 retries that respect Retry-After."""
        for attempt in range(self.retries + 1):
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
                    wait = 4 * (2 ** attempt) + random.uniform(0.2, 1.0)
                time.sleep(min(60, max(1, wait)))
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
            time.sleep(self.delay)
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

    def related_rising(self, root: str, timeframe: str) -> list:
        """Related *rising* queries for one root in one window.

        Returns rows: {"query", "growth" (percent int or None), "breakout" (bool)}.
        """
        widgets = self._explore([root], timeframe)
        payload = self._widget(widgets, "RELATED_QUERIES", "relatedsearches")
        ranked = payload.get("default", {}).get("rankedList", [])
        rows = ranked[1].get("rankedKeyword", []) if len(ranked) > 1 else []
        out = []
        for row in rows:
            query = (row.get("query") or "").strip()
            if not query:
                continue
            out.append({
                "query": query,
                "growth": row.get("value"),       # int percent, or None
                "breakout": row.get("value") is None or str(row.get("formattedValue", "")).lower() == "breakout",
            })
        return out

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
