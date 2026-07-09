"""Seed and refresh the starter pipeline. Every field stays editable.

Validators are loaded from backend/validators_seed/*.py so they live as real,
runnable source, not placeholder strings.
"""
import os

from . import db, store
from .models import DATA_FETCHER_MODEL

_SEED_DIR = os.path.join(os.path.dirname(__file__), "..", "validators_seed")
_PROMPT_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")
# The 6-stage pipeline was the original default; the single-writer mode below
# is now what every real flow (expert self-serve, paid checkout) actually
# uses (see _default_pipeline_id in main.py) — this one is kept around for
# comparison/reference only, not run in production anymore.
DEFAULT_PIPELINE_NAME = "Valuaatio-pipeline (6-vaihe, ei käytössä)"
# Second mode: a cheap/reliable research+writer split. Stage 0 (FAKTAT) is
# shared, stage 1 runs grounded web enrichment, and stage 2 is one strong writer
# with web search off. assemble() still injects the deterministic
# DCF/sensitivity/headcount blocks from the data. This is the real default —
# every self-serve/paid flow generates through this pipeline.
SINGLE_WRITER_PIPELINE_NAME = "Yhden kirjoittajan raportti (oletus)"
# Any pipeline whose name starts with this base is a single-writer pipeline and
# must be kept in sync on reseed — a stale duplicate (e.g. an archived
# "…(oletus, vanha ajohistoria)") once served an OLD prompt to a paid run
# (2026-07-09). Reseed now refreshes ALL of them, not just the canonical name.
SINGLE_WRITER_PIPELINE_PREFIX = "Yhden kirjoittajan raportti"

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
            # The foundation stage: the whole valuation is built on its business
            # understanding, so it runs the strongest grounded search+synthesis
            # model, not the cheapest. A thin foundation here poisons every later
            # stage (the "thinks it's just a calculator" failure). 1M context.
            "model": "google/gemini-3.1-pro-preview",
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


def _single_writer_stages():
    """FAKTAT fetch + grounded enrichment + one writer that consumes the brief.

    The earlier experiment let Fable do native web search inside the writer
    stage. That produced better one-pass prose but made each report slow,
    expensive, and unreliable because the Anthropic web agent injected huge
    token volumes. Keep search in Gemini enrichment, then write without the web
    plugin.
    """
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
            "model": "google/gemini-3.1-pro-preview",
            "prompt_template": _load_prompt("1_enrichment.txt"),
            "expects_json": True,
            "web_search": True,
            # 32k, not the 16k default: Gemini's thinking tokens count toward
            # maxOutputTokens, and a truncated enrichment JSON fails the run.
            "max_tokens": 32000,
            "validator_code": None,
            "input_mapping": {"input_data": "Vaihe 0 FAKTAT"},
        },
        {
            "order": 2,
            "name": "Vaihe 2 - Koko raportti (yksi kirjoittaja)",
            # Fable 5 gave the strongest whole-report prose in testing. It now
            # receives the researched enrichment brief instead of running the
            # costly native web-search agent itself.
            "model": "anthropic/claude-fable-5",
            "prompt_template": _load_prompt("singlewriter.txt"),
            "expects_json": True,
            "web_search": False,
            # Whole report (all sections + tables + charts) in one call. 96k, not
            # 64k: Fable's hidden thinking tokens count toward the cap, and a
            # 'length' truncation triggers a full-price re-run of the whole stage
            # (~2x cost — the 2026-07-08 $6.96 writer call). Headroom is cheaper.
            "max_tokens": 96000,
            "validator_code": _load_validator("stage6_final.py"),
            "input_mapping": {
                "input_data": "Vaihe 0 FAKTAT",
                "enrichment": "Vaihe 1 enrichment",
            },
        },
    ]


def _legacy_single_writer_stage(stage):
    """Detect the old 2-stage preset so boot/reseed can migrate it safely."""
    if not stage or stage.get("order") != 1:
        return False
    prompt = stage.get("prompt_template") or ""
    name = stage.get("name") or ""
    return (
        "Koko raportti" in name
        and "single-writer -tila" in prompt
        and stage.get("web_search") is True
    )


def _ensure_single_writer_pipeline(force=False):
    """Create the canonical single-writer pipeline and, on force, refresh the
    stages of EVERY single-writer pipeline (any name starting with the base
    prefix) — never the 6-stage default. Refreshing all of them, not just the
    canonical name, is what stops a stale duplicate from serving an old prompt
    to a paid run (the wasted-generation incident, 2026-07-09)."""
    pipelines = [
        p for p in store.list_pipelines()
        if str(p.get("name") or "").startswith(SINGLE_WRITER_PIPELINE_PREFIX)
    ]
    canonical = next(
        (p for p in pipelines if p.get("name") == SINGLE_WRITER_PIPELINE_NAME), None
    )
    if canonical is None:
        canonical = store.create_pipeline(SINGLE_WRITER_PIPELINE_NAME)
        pipelines.append(canonical)
    for pipeline in pipelines:
        by_order = {s["order"]: s for s in pipeline.get("stages", [])}
        pforce = force or _legacy_single_writer_stage(by_order.get(1))
        for desired in _single_writer_stages():
            cur = by_order.get(desired["order"])
            if cur is None:
                store.add_stage(pipeline["id"], desired)
            elif pforce or _placeholder_stage(cur):
                store.update_stage(cur["id"], desired)
    return store.get_pipeline(canonical["id"])


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

    _ensure_single_writer_pipeline(force)
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

    # Same code/limits sync for the single-writer pipeline (validator + tokens).
    sw = next((p for p in pipelines
               if p.get("name") == SINGLE_WRITER_PIPELINE_NAME), None)
    if sw:
        sw_by_order = {s["order"]: s for s in sw.get("stages", [])}
        for desired in _single_writer_stages():
            cur = sw_by_order.get(desired["order"])
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
        _ensure_single_writer_pipeline()
        return
    reseed_defaults(force=True)
    sync_prompt_patches()
