# Trend Radar monitoring rules

## Candidate flow

1. Collect Google Trends related rising queries.
2. Run the existing historical review (`new`, `revived`, `spike`, `watch`).
3. Run opportunity verification only for reviewed `new` terms that are not noise or entertainment and whose root relation is `direct` or `evidence`.

Opportunity verification labels candidates; it never deletes a term or changes the historical verdict.

## Novelty labels

- `old`: Hacker News contains a story older than 183 days.
- `likely-new`: no story older than 183 days and at least one story in the last 8 weeks.
- `uncertain`: mentions exist, but only between 8 weeks and 183 days ago.
- `unknown`: no HN evidence or the request failed. No evidence is not treated as proof of novelty.

## Market-gap labels

Use the first 10 DuckDuckGo HTML results for the exact phrase. Count forum/Q&A domains as UGC.

- `gap`: at least 4 UGC results.
- `likely_saturated`: at least 5 results and no more than 1 UGC result.
- `unclear`: any other successful result.
- `unknown`: the public endpoint returned no usable results, rate-limited, or failed.

RDAP checks one deterministic `.com` candidate. `not_found` is supporting evidence only; it does not guarantee registrar availability.

## Cost and failure controls

- Verify at most 20 eligible terms per report.
- Cache every opportunity result for 24 hours in `state/state.json`.
- SERP/UGC checks use a keyless public endpoint and store only derived counts.
- Fail open: API failures produce `unknown` labels and never stop collection or reporting.

## Output

Each `daily.json` candidate includes `novelty`, `novelty_confidence`, `ugc_ratio`, `market_status`, `rdap_domain`, and `rdap_status`.
