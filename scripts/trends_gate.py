#!/usr/bin/env python3
"""Google Trends interest gate for new root candidates.

Before a candidate root (Reddit AI monitor, GitHub radar, ...) enters the
root pool, check that the term has *some* search interest on Google Trends.
Terms with zero interest across the window are skipped so they don't consume
rotation budget in the hourly collection.

One term per explore call: timeline values are relative 0-100 *within* a
chart, so batching several terms together could zero-out a small-but-real
term next to a big one. Candidate volumes are low (a few per day), so the
extra requests are affordable.

A term passes only with >= 2 non-zero points: Google sometimes returns a
single spurious 100-spike for zero-volume terms, and one isolated blip is
not evidence of real interest either way.

On HTTP 429 the gate fails OPEN for the remaining terms (returns None) so a
transient rate limit never drops a genuine candidate. Callers decide how to
handle None; recommended: add without validation (previous behaviour).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trends import RateLimited, TrendsClient  # noqa: E402

TIMEFRAME = "now 7-d"


def validate_roots_interest(terms: list[str], timeframe: str = TIMEFRAME,
                            client: TrendsClient | None = None
                            ) -> dict[str, bool | None]:
    """Check Google Trends interest for candidate roots.

    Returns {term: True}  term has some search interest in the window,
            {term: False} term has zero interest (skip it),
            {term: None}  could not check (rate limited) -- fail open.
    """
    result: dict[str, bool | None] = {}
    queue = list(dict.fromkeys(terms))  # dedup, keep order
    if not queue:
        return result
    client = client or TrendsClient()
    for i, term in enumerate(queue):
        try:
            timelines = client.timeline([term], timeframe)
        except RateLimited:
            for t in queue[i:]:
                result[t] = None
            break
        points = timelines.get(term) or []
        hits = sum(1 for p in points if (p.get("value") or 0) > 0)
        result[term] = hits >= 2
    return result
