"""Update live pipeline prompts through the production backend API.

Usage (PowerShell):
  $env:APP_TOKEN = "..."
  python scripts/update_live_pipeline_prompts.py

Optional:
  $env:API_BASE = "https://valu-pipeline-production-88f2.up.railway.app"

Only stages 2, 3 and 6 are changed. Other stage settings (model, max_tokens,
validator_code, web_search, etc.) are preserved from production.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


API_BASE = os.getenv("API_BASE", "https://valu-pipeline-production-88f2.up.railway.app").rstrip("/")
APP_TOKEN = os.getenv("APP_TOKEN", "")
ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {
    2: ROOT / "pipeline-runner" / "backend" / "prompts" / "2_profiili_kilpailijat.txt",
    3: ROOT / "pipeline-runner" / "backend" / "prompts" / "3_pisteytys_numero_osiot.txt",
    6: ROOT / "pipeline-runner" / "backend" / "prompts" / "6_tiivistelma.txt",
}


def _request(path, method="GET", body=None):
    if not APP_TOKEN:
        raise SystemExit("APP_TOKEN is required; refusing to call production unauthenticated.")
    data = None
    headers = {"Authorization": f"Bearer {APP_TOKEN}"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API_BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            raw = res.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {path} failed: HTTP {e.code}: {detail[:1000]}")


def main():
    pipelines = _request("/api/pipelines")
    if not pipelines:
        raise SystemExit("No pipelines found in production.")
    pipeline = next((p for p in pipelines if p.get("name") == "Valuaatio-pipeline (oletus)"), pipelines[0])
    stages = {s.get("order"): s for s in pipeline.get("stages", [])}
    updated = []
    for order, prompt_path in PROMPTS.items():
        stage = stages.get(order)
        if not stage:
            raise SystemExit(f"Stage order {order} not found in production pipeline {pipeline.get('id')}.")
        prompt = prompt_path.read_text(encoding="utf-8")
        if stage.get("prompt_template") == prompt:
            continue
        payload = dict(stage)
        payload["prompt_template"] = prompt
        _request(f"/api/stages/{stage['id']}", method="PUT", body=payload)
        updated.append(f"{order}: {stage.get('name')}")

    print(json.dumps({
        "ok": True,
        "api_base": API_BASE,
        "pipeline_id": pipeline.get("id"),
        "updated": updated,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
