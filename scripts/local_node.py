#!/usr/bin/env python3
"""Run the Windows collector shard and publish only this round's delta."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DIR = ROOT / "state" / "local"
LOCAL_STATE = LOCAL_DIR / "state.json"
LOCAL_ROOTS = LOCAL_DIR / "roots.json"
LOCAL_SUGGESTIONS = LOCAL_DIR / "seo-suggestions.json"
NODE_PATH = "state/nodes/local-windows.json"
SUGGESTIONS_PER_RUN = 6


def load(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def download_roots(url: str) -> None:
    request = urllib.request.Request(f"{url}?v={int(time.time())}", headers={
        "User-Agent": "TrendRadarLocal/1.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload.get("roots"), list):
        raise RuntimeError("published roots inventory is invalid")
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_ROOTS.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def collect_suggestions(roots: list[str]) -> dict:
    state = load(LOCAL_SUGGESTIONS, {"cursor": 0, "items": {}})
    items = state.setdefault("items", {})
    if not roots:
        return items
    cursor = int(state.get("cursor", 0)) % len(roots)
    selected = [roots[(cursor + i) % len(roots)]
                for i in range(min(SUGGESTIONS_PER_RUN, len(roots)))]
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for index, term in enumerate(selected):
        query = urllib.parse.urlencode({
            "client": "firefox", "hl": "en", "gl": "us", "q": term})
        request = urllib.request.Request(
            "https://suggestqueries.google.com/complete/search?" + query,
            headers={"Accept": "application/json",
                     "User-Agent": "Mozilla/5.0 TrendRadarLocal/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            suggestions = [str(value).strip() for value in payload[1]
                           if str(value).strip()]
            if suggestions:
                items["us|" + term.strip().casefold()] = {
                    "term": term, "gl": "us", "fetched_at": stamp,
                    "items": suggestions[:20],
                }
        except Exception as error:
            print(f"suggestions skipped for {term!r}: {error}")
        if index < len(selected) - 1:
            time.sleep(1)
    state.update({"cursor": (cursor + len(selected)) % len(roots),
                  "updated": stamp})
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_SUGGESTIONS.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    return items


def publish(gh: str, repo: str, node: dict) -> None:
    endpoint = f"repos/{repo}/contents/{NODE_PATH}"
    current = subprocess.run([gh, "api", endpoint], capture_output=True, text=True)
    body = {
        "message": "data: update local collector delta",
        "branch": "main",
        "content": base64.b64encode(json.dumps(
            node, ensure_ascii=False, separators=(",", ":")).encode()).decode(),
    }
    if current.returncode == 0:
        body["sha"] = json.loads(current.stdout)["sha"]
    subprocess.run([gh, "api", "--method", "PUT", endpoint, "--input", "-"],
                   input=json.dumps(body), text=True, check=True,
                   stdout=subprocess.DEVNULL)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gh", default="gh")
    parser.add_argument("--repo", default="ToyotaMasayoshi/trend-radar")
    parser.add_argument("--roots-url",
                        default="https://trend-radar-eif.pages.dev/data/roots.json")
    args = parser.parse_args()

    download_roots(args.roots_url)
    suggestions = collect_suggestions(load(LOCAL_ROOTS, {"roots": []}).get("roots", []))
    before = load(LOCAL_STATE, {"roots": {}, "reviews": {}})
    subprocess.run([
        sys.executable, "-B", str(ROOT / "scripts" / "collect.py"),
        "--state-file", str(LOCAL_STATE), "--roots-file", str(LOCAL_ROOTS),
        "--shard-count", "2", "--shard-index", "1",
        "--collector-id", "local-windows",
    ], cwd=ROOT, check=True)
    after = load(LOCAL_STATE, {})
    delta = {
        "collector_id": "local-windows",
        "updated": after.get("meta", {}).get("updated", ""),
        "meta": after.get("meta", {}),
        "roots": {key: value for key, value in after.get("roots", {}).items()
                  if value != before.get("roots", {}).get(key)},
        "reviews": {key: value for key, value in after.get("reviews", {}).items()
                    if value != before.get("reviews", {}).get(key)},
        "suggestions": suggestions,
    }
    publish(args.gh, args.repo, delta)
    print(f"published {len(delta['roots'])} roots, {len(delta['reviews'])} reviews and {len(suggestions)} suggestion sets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
