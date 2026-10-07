#!/usr/bin/env python3
"""Run the Windows collector shard and publish only this round's delta."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DIR = ROOT / "state" / "local"
LOCAL_STATE = LOCAL_DIR / "state.json"
LOCAL_ROOTS = LOCAL_DIR / "roots.json"
NODE_PATH = "state/nodes/local-windows.json"


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
    }
    publish(args.gh, args.repo, delta)
    print(f"published {len(delta['roots'])} roots and {len(delta['reviews'])} reviews")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
