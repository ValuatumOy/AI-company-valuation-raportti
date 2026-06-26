"""Seed and refresh the starter pipeline. Every field stays editable.

Validators are loaded from backend/validators_seed/*.py so they live as real,
runnable source, not placeholder strings.
"""
import os

from . import db, store
from .models import DATA_FETCHER_MODEL

_SEED_DIR = os.path.join(os.path.dirname(__file__), "..", "validators_seed")
_PROMPT_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")
DEFAULT_PIPELINE_NAME = "Valuaatio-pipeline (oletus)"

PLACEHOLDER_PREFIX = "[[ LIITÄ VAIHEEN "


def _load_validator(fname):
    path = os.path.join(_SEED_DIR, fname)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_prompt(fname):
    path = os.path.join(_PROMPT_DIR, fname)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _stages():
    return [
        {
            "order": 0,
            "name": "Vaihe 0 - FAKTAT (data fetch)",
            "model": DATA_FETCHER_MODEL,
            "prompt_template": "",
            "expects_json": True,
            "validator_code": _load_validator("stage0_schema.py"),
            "input_mapping": {},
        },
        {
            "order": 1,
            "name": "Vaihe 1 - Enrichment (web haku)",
            "model": "google/gemini-2.5-flash",
            "prompt_template": _load_prompt("1_enrichment.txt"),
            "expects_json": True,
            "validator_code": None,
            "input_mapping": {"input_data": "Vaihe 0 FAKTAT"},
        },
        {
            "order": 2,
            "name": "Vaihe 2 - Profiili, markkina ja kilpailijat",
            "model": "deepseek/deepseek-v4-flash",
            "prompt_template": _load_prompt("2_profiili_kilpailijat.txt"),
            "expects_json": True,
            "validator_code": None,
            "input_mapping": {
                "input_data": "Vaihe 0 FAKTAT",
                "enrichment": "Vaihe 1 enrichment",
            },
        },
        {
            "order": 3,
            "name": "Vaihe 3 - Pisteytys ja numero-osiot",
            "model": "deepseek/deepseek-v4-flash",
            "prompt_template": _load_prompt("3_pisteytys_numero_osiot.txt"),
            "expects_json": True,
            "validator_code": _load_validator("stage3_numbers.py"),
            "input_mapping": {
                "input_data": "Vaihe 0 FAKTAT",
                "growth_assessment": "Vaihe 2 kasvupotentiaali",
            },
        },
        {
            "order": 4,
            "name": "Vaihe 4 - Skenaariot ja odotusarvo",
            "model": "deepseek/deepseek-v4-pro",
            "prompt_template": _load_prompt("4_skenaariot.txt"),
            "expects_json": True,
            "validator_code": _load_validator("stage4_scenarios.py"),
            "input_mapping": {
                "input_data": "Vaihe 0 FAKTAT",
                "enrichment": "Vaihe 1 enrichment",
                "growth_assessment": "Vaihe 2 kasvupotentiaali",
                "scoring": "Vaihe 3 pisteytys",
                "sections_numeric": "Vaihe 3 numero-osiot",
            },
        },
        {
            "order": 5,
            "name": "Vaihe 5 - Analyysi-osiot",
            "model": "deepseek/deepseek-v4-pro",
            "prompt_template": _load_prompt("5_analyysi_osiot.txt"),
            "expects_json": True,
            "validator_code": None,
            "input_mapping": {
                "input_data": "Vaihe 0 FAKTAT",
                "enrichment": "Vaihe 1 enrichment",
                "growth_assessment": "Vaihe 2 kasvupotentiaali",
                "scoring": "Vaihe 3 pisteytys",
                "scenarios": "Vaihe 4 skenaariot",
            },
        },
        {
            "order": 6,
            "name": "Vaihe 6 - Tiivistelmä ja kokoaja",
            "model": "deepseek/deepseek-v4-pro",
            "prompt_template": _load_prompt("6_tiivistelma.txt"),
            "expects_json": True,
            "validator_code": _load_validator("stage6_final.py"),
            "input_mapping": {
                "input_data": "Vaihe 0 FAKTAT",
                "enrichment": "Vaihe 1 enrichment",
                "growth_assessment": "Vaihe 2 kasvupotentiaali",
                "scoring": "Vaihe 3 pisteytys",
                "scenarios": "Vaihe 4 skenaariot",
                "sections_numeric": "Vaihe 3 numero-osiot",
                "sections_analysis": "Vaiheet 2, 4 ja 5 analyysi",
            },
        },
    ]


def _placeholder_stage(stage):
    prompt = stage.get("prompt_template") or ""
    return PLACEHOLDER_PREFIX in prompt and "PROMPTI TÄHÄN" in prompt


def _pipeline_needs_auto_reseed(pipeline):
    by_order = {s["order"]: s for s in pipeline.get("stages", [])}
    if any(order not in by_order for order in range(0, 7)):
        return True
    return any(_placeholder_stage(s) for s in by_order.values())


def reseed_defaults(force=False):
    """Create or refresh the default stage set.

    Without force this is conservative: it updates placeholder stages and adds
    missing default orders. The explicit API endpoint passes force=True to
    restore the vendored prompts.
    """
    db.init_db()
    pipelines = store.list_pipelines()
    if pipelines:
        pipeline = next(
            (p for p in pipelines if p.get("name") == DEFAULT_PIPELINE_NAME),
            pipelines[0],
        )
    else:
        pipeline = store.create_pipeline(DEFAULT_PIPELINE_NAME)

    updated = 0
    created = 0
    by_order = {s["order"]: s for s in pipeline.get("stages", [])}
    for desired in _stages():
        current = by_order.get(desired["order"])
        if current is None:
            store.add_stage(pipeline["id"], desired)
            created += 1
            continue
        if force or _placeholder_stage(current):
            store.update_stage(current["id"], desired)
            updated += 1

    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "pipeline": store.get_pipeline(pipeline["id"]),
    }


def ensure_seeded():
    db.init_db()
    row = db.query_one("SELECT id FROM pipelines LIMIT 1")
    if row:
        pipeline = store.get_pipeline(row["id"])
        if pipeline and _pipeline_needs_auto_reseed(pipeline):
            reseed_defaults(force=False)
        return
    reseed_defaults(force=True)
