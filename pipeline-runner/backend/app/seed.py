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
            "web_search": True,  # enrichment does live web research
            "validator_code": None,
            "input_mapping": {"input_data": "Vaihe 0 FAKTAT"},
        },
        {
            "order": 2,
            "name": "Vaihe 2 - Profiili + kilpailijat",
            "model": "deepseek/deepseek-v4-pro",
            "prompt_template": _load_prompt("2_profiili_kilpailijat.txt"),
            "expects_json": True,
            "validator_code": _load_validator("stage_grounding.py"),
            "input_mapping": {
                "input_data": "Vaihe 0 FAKTAT",
                "enrichment": "Vaihe 1 enrichment",
            },
        },
        {
            "order": 3,
            "name": "Vaihe 3 - Pisteytys + numero-osiot",
            "model": "deepseek/deepseek-v4-pro",
            "prompt_template": _load_prompt("3_pisteytys_numero_osiot.txt"),
            "expects_json": True,
            "max_tokens": 32000,
            "validator_code": _load_validator("stage3_numbers.py"),
            "input_mapping": {
                "input_data": "Vaihe 0 FAKTAT",
                "growth_assessment": "Vaihe 2 kasvupotentiaali",
            },
        },
        {
            "order": 4,
            "name": "Vaihe 4 - Skenaariot",
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
            "model": "anthropic/claude-sonnet-5",  # heaviest writing section
            "prompt_template": _load_prompt("5_analyysi_osiot.txt"),
            "expects_json": True,
            "max_tokens": 32000,  # emits several long analysis sections
            "validator_code": _load_validator("stage_grounding.py"),
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
            "name": "Vaihe 6 - Tiivistelmä + kokoaja",
            "model": "anthropic/claude-sonnet-5",  # client-facing summary writing
            "prompt_template": _load_prompt("6_tiivistelma.txt"),
            "expects_json": True,
            "max_tokens": 32000,  # wrapper + 4 sections + machine_readable in one call
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


def ensure_current_defaults():
    """Repair stale default pipelines before the UI reads them."""
    db.init_db()
    pipelines = store.list_pipelines()
    if not pipelines:
        return reseed_defaults(force=True)

    pipeline = next(
        (p for p in pipelines if p.get("name") == DEFAULT_PIPELINE_NAME),
        pipelines[0],
    )
    if _pipeline_needs_auto_reseed(pipeline):
        return reseed_defaults(force=True)

    return {"ok": True, "created": 0, "updated": 0, "pipeline": pipeline}


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


def sync_code_and_limits():
    """Keep the trust-critical validators and token limits in sync with the repo
    on every boot, WITHOUT touching user-editable prompts / models / toggles.
    This is how validator improvements reach an existing pipeline without a full
    reseed (which would also reset prompts). Only pushes seeded validators; never
    clears a validator the operator added."""
    pipelines = store.list_pipelines()
    if not pipelines:
        return
    pipeline = next(
        (p for p in pipelines if p.get("name") == DEFAULT_PIPELINE_NAME), pipelines[0]
    )
    by_order = {s["order"]: s for s in pipeline.get("stages", [])}
    for desired in _stages():
        cur = by_order.get(desired["order"])
        if not cur:
            continue
        patch = {}
        dv = desired.get("validator_code")
        if dv and (cur.get("validator_code") or "") != dv:
            patch["validator_code"] = dv
        dmax = desired.get("max_tokens")
        if dmax and cur.get("max_tokens") != dmax:
            patch["max_tokens"] = dmax
        if patch:
            store.update_stage(cur["id"], {**cur, **patch})


def _patch_prompt_text(order, text):
    """Surgically apply production prompt wording fixes without a broad reseed.

    Prompt bodies are editable in the UI, so we do not blindly overwrite them on
    boot. These narrow patches insert the report-presentation rules requested
    after the Virnex review while preserving any unrelated operator edits.
    """
    if not isinstance(text, str) or not text:
        return text

    prompt_markers = {
        1: ("LIIKEVAIHDON POIKKEAMAN LISÄHAKU", "1_enrichment.txt"),
        4: ("LIIKEVAIHDON POIKKEAMA", "4_skenaariot.txt"),
        5: ("liikevaihtopoikkeaman mahdollinen selitys", "5_analyysi_osiot.txt"),
    }
    marker_file = prompt_markers.get(order)
    if marker_file and marker_file[0] not in text:
        return _load_prompt(marker_file[1])

    if order == 2:
        if "LIIKEVAIHDON POIKKEAMAN TULKINTA" not in text:
            return _load_prompt("2_profiili_kilpailijat.txt")
        marker = "Mahdollinen puute kuuluu data quality -rajoitteisiin"
        if marker in text:
            return text
        anchor = (
            'Aloita `paragraph`: "Tämä osio yhdistää tilinpäätösanalyysin '
            'käytettävissä oleviin liiketoimintatietoihin. Ulkoisista lähteistä '
            'peräisin olevat tiedot kuvaavat yhtiön omaa tai kolmannen osapuolen '
            'julkaisemaa tietoa, eivät tilintarkastettua dataa." Jos '
            'enrichment.degraded=true: "Yhtiön liiketoimintaprofiili perustuu '
            'pääosin toimiala- ja tilinpäätöstietoihin, koska yrityksen '
            'identiteettiä ei voitu varmentaa julkisista lähteistä riittävällä '
            'varmuudella."'
        )
        insert = (
            '\n- Jos `[input_data].meta.industry` tai `industry_code` puuttuu '
            'mutta `enrichment` sisältää julkisesta lähteestä varmennetun '
            'toimialan, verkkosivun kuvauksen tai liiketoimintaprofiilin, käytä '
            'tätä toimialakuvauksena normaalisti. Älä aloita osion näkyvää '
            'tekstiä muodolla "Ei tiedossa" tai sisäisellä puutehuomiolla. '
            'Mahdollinen puute kuuluu data quality -rajoitteisiin, ei '
            'yhtiöprofiilin ensivaikutelmaksi.'
        )
        return text.replace(anchor, anchor + insert) if anchor in text else text

    if order == 3:
        marker = "DCF/EVA-ekvivalenssi"
        if marker in text:
            return text
        return _load_prompt("3_pisteytys_numero_osiot.txt")

    if order == 6:
        if "LÄHDEMERKINNÄT (jäljitettävyys)" not in text:
            return _load_prompt("6_tiivistelma.txt")
        out = text
        cover_marker = "Kannen pääluku = realistinen base case"
        if cover_marker not in out:
            return _load_prompt("6_tiivistelma.txt")

        industry_marker = "Älä koskaan kirjoita kanteen tai meta.industry-kenttään"
        if industry_marker not in out:
            anchor = "- `meta`: yrityksen nimi, Y-tunnus, toimiala, päivämäärä."
            insert = (
                '\n  - `industry`: käytä ensisijaisesti '
                '`[input_data].meta.industry`-kenttää. Jos se tai '
                '`industry_code` puuttuu mutta `enrichment` sisältää julkisesta '
                'lähteestä varmennetun toimialan, liiketoimintakuvauksen tai '
                'yhtiöprofiilin, kirjoita tähän lukijalle ymmärrettävä toimiala '
                'sen perusteella (esim. "Ohjelmistokehitys ja digitaaliset '
                'palvelut"). Älä koskaan kirjoita kanteen tai '
                'meta.industry-kenttään "Ei tiedossa", "input-datassa puuttuu", '
                '"industry_code puuttuu" tai muuta sisäistä puutehuomiota. Jos '
                'toimialaa ei voida varmentaa edes julkisista lähteistä, jätä '
                '`industry` tyhjäksi ja käsittele puute vain osiossa 2.'
            )
            out = out.replace(anchor, anchor + insert) if anchor in out else out

        cards_marker = "Älä lisää luottamustasoa tai sen perustelua metric_cards-lohkoon"
        if cards_marker not in out:
            old = (
                "- `metric_cards`: Realistinen base case ENSIN (raportin pääluku, "
                "emphasis:true) JA Skenaarioilla painotettu odotusarvo (molemmat "
                "aina), Skenaariohaarukka (pess–opt floorattu), Luottamustaso "
                "(+ määräävä sääntö), Käytetyt/Hylätyt menetelmät. Lisää "
                "Markkinasignaali-kortti jos löytyi/ilmoitettu. Realistinen base "
                "case on raportin ankkuriarvo (perustuu "
                "arvonmääritysmenetelmiin); skenaarioilla painotettu odotusarvo "
                "on todennäköisyyspainotettu vertailuluku, ei pääluku."
            )
            new = (
                "- `metric_cards`: Realistinen base case ENSIN (raportin pääluku, "
                "emphasis:true) JA Skenaarioilla painotettu odotusarvo (molemmat "
                "aina), Skenaariohaarukka (pess–opt floorattu), "
                "Käytetyt/Hylätyt menetelmät. Lisää Markkinasignaali-kortti jos "
                "löytyi/ilmoitettu. Älä lisää luottamustasoa tai sen perustelua "
                "metric_cards-lohkoon; se kuuluu osioon 2, ei ensimmäiseksi "
                "lukijan näkemäksi kortiksi. Realistinen base case on raportin "
                "ankkuriarvo (perustuu arvonmääritysmenetelmiin); skenaarioilla "
                "painotettu odotusarvo on todennäköisyyspainotettu vertailuluku, "
                "ei pääluku."
            )
            out = out.replace(old, new)
        return out

    return text


def sync_prompt_patches():
    """Apply narrow prompt text migrations to the persisted default pipeline."""
    pipelines = store.list_pipelines()
    if not pipelines:
        return
    pipeline = next(
        (p for p in pipelines if p.get("name") == DEFAULT_PIPELINE_NAME), pipelines[0]
    )
    for cur in pipeline.get("stages", []):
        if cur.get("order") not in (1, 2, 3, 4, 5, 6):
            continue
        patched = _patch_prompt_text(cur["order"], cur.get("prompt_template"))
        if patched != cur.get("prompt_template"):
            store.update_stage(cur["id"], {**cur, "prompt_template": patched})


def ensure_seeded():
    db.init_db()
    row = db.query_one("SELECT id FROM pipelines LIMIT 1")
    if row:
        pipeline = store.get_pipeline(row["id"])
        if pipeline and _pipeline_needs_auto_reseed(pipeline):
            reseed_defaults(force=True)
        sync_code_and_limits()
        sync_prompt_patches()
        return
    reseed_defaults(force=True)
    sync_prompt_patches()
