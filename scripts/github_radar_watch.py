#!/usr/bin/env python3
"""Hourly GitHub Rising Radar watch -> trend-radar root pool.

Pipeline (all deterministic, no LLM):
  1. Fetch https://github-rising-radar.sgaggjhkjh.workers.dev/api/data
  2. Fake-star screening first: keep only screening.decision == KEEP and not suspicious.
  3. Methodology stage gate: radar stage >= 1 (S1 candidate+).
  4. Audience filter (user requirement): broad audience + low technical barrier.
     - awesome repos excluded
     - dev-only blacklist on description/topics -> excluded
     - user-facing whitelist (or readme demo/screenshot) -> pass
     - empty description / no readme signals -> REVIEW (never auto-added)
  5. Extract root terms from repo name, dedupe against state/roots.json,
     append new ones, optionally push to GitHub.
  6. Feedback loop (P1): new/revived verdicts from the daily report are
     re-added as roots, so newly discovered rising combos become
     tomorrow's roots (methodology loop).

Usage:
  python3 scripts/github_radar_watch.py [--push] [--roots state/roots.json]
Exit 0 always (never break the cron); results printed as JSON summary.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RADAR_API = "https://github-rising-radar.sgaggjhkjh.workers.dev/api/data"
DAILY_JSON_URL = "https://trend-radar-eif.pages.dev/data/daily.json"
# verdicts that feed back into the root pool (methodology: new rising combos)
FEEDBACK_VERDICTS = {"new", "revived"}
# noise that must never become roots (same rules as the email noise filter)
FEEDBACK_NOISE_SUBSTRINGS = ["near me", ".gov", ".com", ".org", ".net", ".io",
                             # AI error messages that trend as queries
                             "i wasn't able", "i'm sorry", "as an ai",
                             "i cannot", "unable to generate", "error on my side",
                             "something went wrong"]
# generic how-to / question phrases make poor roots
FEEDBACK_NOISE_PREFIXES = ["how ", "what ", "why ", "when ", "where ", "which "]
# Big-vendor AI model terms are not auto-added as roots (user 2026-10-07:
# he does not build model-info sites; vendor models are not implementable).
# Exception: a model confirmed to have a free tier / free API may be added
# manually by the user (manual source bypasses this filter).
VENDOR_MODEL_SUBSTRINGS = ["gpt", "chatgpt", "openai", "gemini", "gemma",
                           "claude", "anthropic", "grok", "hunyuan",
                           "leonardo", "copilot", "midjourney"]


def _feedback_noise(q: str) -> bool:
    if any(s in q for s in FEEDBACK_NOISE_SUBSTRINGS):
        return True
    if any(s in q for s in VENDOR_MODEL_SUBSTRINGS):
        return True
    if any(q.startswith(p) for p in FEEDBACK_NOISE_PREFIXES):
        return True
    if len(q.split()) > 6:  # roots are short terms, not sentences
        return True
    return False

# --- filter rules -----------------------------------------------------------
# Pure-developer signals: repo is a lib/tool for developers, not end users.
DEV_BLACKLIST = [
    "library", "framework", "sdk", "api client", "linter", "boilerplate",
    "plugin for", "for agents", "developer tool", "cli tool", "codebase",
    "type checker", "compiler", "typecheck",
]
# End-user-facing signals: broad audience, low technical barrier.
USER_WHITELIST = [
    "app", "tool", "generator", "editor", "maker", "studio",
    "chat", "assistant", "game", "platform", "website", "avatar",
]


def norm(s: str) -> str:
    return " ".join(s.lower().strip().split())


def fetch_radar() -> dict:
    req = urllib.request.Request(RADAR_API, headers={"User-Agent": "trend-radar-watch/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def classify(repo: dict) -> tuple[str, str]:
    """Return (decision, reason). decision in PASS / REVIEW / SKIP."""
    scr = repo.get("screening") or {}
    if scr.get("decision") != "KEEP" or repo.get("suspicious"):
        return "SKIP", f"screening={scr.get('decision')},suspicious={repo.get('suspicious')}"
    if (repo.get("stage") or 0) < 1:
        return "SKIP", f"stage={repo.get('stage')} < 1"
    if repo.get("awesome"):
        return "SKIP", "awesome-list"
    desc = (repo.get("description") or "").strip()
    topics = " ".join(repo.get("topics") or [])
    text = norm(desc + " " + topics)
    if not desc:
        return "REVIEW", "empty description, cannot judge audience"
    if any(k in text for k in DEV_BLACKLIST):
        return "SKIP", "dev-only signals in description/topics"
    readme = repo.get("readme") or {}
    if readme.get("demo") or readme.get("screenshot"):
        return "PASS", "has demo/screenshot (user-facing UI)"
    if any(k in text.split() or k in text for k in USER_WHITELIST):
        return "PASS", "user-facing keywords"
    return "REVIEW", "no clear audience signals"


def repo_to_roots(repo: dict) -> list[str]:
    name = repo["fullName"].split("/")[-1]
    root = name.replace("-", " ").replace("_", " ").strip().lower()
    root = " ".join(root.split())
    return [root] if root else []


def load_roots(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def push_roots(path: Path, message: str) -> bool:
    """Update state/roots.json in GitHub via Contents API. No raw creds printed."""
    sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
    from dynamic_credentials import add_surrogate_to_request, read_json_response

    api = "https://api.github.com/repos/ToyotaMasayoshi/trend-radar/contents/state/roots.json"
    content = path.read_bytes()

    def call(method: str, body: dict | None = None):
        req = urllib.request.Request(api, method=method)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
        add_surrogate_to_request(req, "custom.github", allowed_hosts=["api.github.com"])
        req.data = data
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, read_json_response(r)
        except urllib.error.HTTPError as e:
            return e.code, {"message": e.read().decode("utf-8", "replace")[:200]}

    status, cur = call("GET")
    if status != 200:
        print(f"push: GET failed {status}", file=sys.stderr)
        return False
    body = {
        "message": message,
        "content": base64.b64encode(content).decode(),
        "sha": cur["sha"],
    }
    status, _ = call("PUT", body)
    if status not in (200, 201):
        print(f"push: PUT failed {status}", file=sys.stderr)
        return False
    return True


def feedback_roots(store: dict, existing: set[str]) -> list[str]:
    """P1: feed new/revived terms from the daily report back into the root pool.

    Methodology loop: newly discovered rising combos become tomorrow's roots.
    Only verdicts new/revived qualify; dedup against the existing pool.
    Terms on the user blocklist (root_blocklist) are never re-added.
    Never raises: on fetch failure returns [].
    """
    added: list[str] = []
    try:
        blocklist = {norm(t) for t in (store.get("root_blocklist") or [])}
        req = urllib.request.Request(DAILY_JSON_URL,
                                     headers={"User-Agent": "trend-radar-watch/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            daily = json.loads(r.read().decode("utf-8"))
        for c in daily.get("candidates", []):
            if c.get("verdict") not in FEEDBACK_VERDICTS:
                continue
            q = norm(c.get("q", ""))
            if not q or len(q) < 2 or _feedback_noise(q):
                continue
            if q in blocklist:
                continue
            if q not in existing:
                added.append(q)
                existing.add(q)
    except Exception:
        pass
    return added


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--roots", default=str(ROOT / "state" / "roots.json"))
    args = ap.parse_args()

    summary: dict = {
        "at": datetime.now(timezone.utc).isoformat(),
        "fetched": 0, "passed": [], "review": [], "skipped": 0, "added": [],
        "pushed": False, "error": None,
    }
    try:
        data = fetch_radar()
    except Exception as e:  # noqa: BLE001 - never break the cron
        summary["error"] = f"fetch failed: {e}"
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    top = data.get("top") or []
    summary["fetched"] = len(top)
    passed, review = [], []
    for repo in top:
        decision, reason = classify(repo)
        if decision == "PASS":
            passed.append({"repo": repo["fullName"], "stars": repo.get("stars"),
                           "stage": repo.get("stage"), "reason": reason})
        elif decision == "REVIEW":
            review.append({"repo": repo["fullName"], "stars": repo.get("stars"),
                           "reason": reason})
        else:
            summary["skipped"] += 1
    summary["passed"] = passed
    summary["review"] = review

    roots_path = Path(args.roots)
    store = load_roots(roots_path)
    existing = {norm(r) for r in store["roots"]}
    new_roots: list[str] = []
    radar_added: list[str] = []
    blocklist = {norm(t) for t in (store.get("root_blocklist") or [])}
    for p in passed:
        # re-fetch fullName -> root term
        for repo in top:
            if repo["fullName"] == p["repo"]:
                for rt in repo_to_roots(repo):
                    if norm(rt) not in existing and norm(rt) not in blocklist:
                        new_roots.append(rt)
                        radar_added.append(rt)
                        existing.add(norm(rt))
    # P1: feedback loop — new/revived terms from the daily report become roots
    feedback_added = feedback_roots(store, existing)
    summary["feedback_added"] = feedback_added
    new_roots.extend(feedback_added)
    if new_roots:
        today = datetime.now(timezone.utc).date().isoformat()
        store["roots"].extend(new_roots)
        store["count"] = len(store["roots"])
        sources = store.setdefault("root_sources", {})
        added_at = store.setdefault("root_added_at", {})
        for rt in radar_added:
            sources[rt] = "github-radar"
            added_at[rt] = today
        for rt in feedback_added:
            sources[rt] = "feedback"
            added_at[rt] = today
        src = f"github-radar:{datetime.now(timezone.utc).date().isoformat()}"
        if src not in store["source"]:
            store["source"].append(src)
        store["extraction"] += "; github_radar_watch: broad-audience low-barrier repos (stage>=1, KEEP)"
        if feedback_added:
            store["extraction"] += "; feedback: new/revived terms re-added as roots"
        roots_path.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
        summary["added"] = new_roots
        if args.push:
            ok = push_roots(roots_path, f"chore: add {len(new_roots)} roots (radar+feedback)")
            summary["pushed"] = ok

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
