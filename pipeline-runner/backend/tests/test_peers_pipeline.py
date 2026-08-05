"""Peers must actually reach the writer's prompt.

The resolution logic is unit-tested in test_peers.py; what this covers is the
wiring between the two stages — enrichment names the competitors, the peers
land in `input_data.peers`, and the writer's substituted prompt carries them.
A wrong context key here would fail silently: the report would simply keep
saying "toimialavertailua ei voida muodostaa".
"""
import asyncio

from app import db, peers, runner, store
from app.models import DATA_FETCHER_MODEL

PEER = {
    "name": "Enento Oyj", "fid": 555, "listed": True, "fiscal_year": 2025,
    "revenue_teur": 142500, "ev_per_ebitda": 9.4,
    "source": "Valuatum /modeldata (fid 555)", "fetched": "2026-08-05",
}


def _pipeline():
    db.init_db()
    pid = store.create_pipeline("peers-wiring")["id"]
    store.add_stage(pid, {"order": 0, "name": "FAKTAT", "model": DATA_FETCHER_MODEL})
    store.add_stage(pid, {"order": 1, "name": "Rikastus", "model": "test/model",
                          "prompt_template": "rikasta {{input_data}}"})
    store.add_stage(pid, {"order": 2, "name": "Kirjoittaja", "model": "test/model",
                          "prompt_template": "kirjoita {{input_data}} {{enrichment}}"})
    return store.get_pipeline(pid)


def _chat_stub(seen):
    async def chat(model, prompt, **kw):
        seen.append(prompt)
        body = ('{"competitors": [{"name": "Enento Oyj", "segment": "ohjelmisto"}]}'
                if "rikasta" in prompt else '{"sections": []}')
        return {"text": body, "request_payload": {}, "finish_reason": "stop",
                "tokens_prompt": 1, "tokens_completion": 1}
    return chat


def test_resolved_peers_reach_the_writer_prompt(monkeypatch):
    prompts: list[str] = []
    monkeypatch.setattr(runner.openrouter, "chat", _chat_stub(prompts))
    monkeypatch.setattr(runner.openrouter, "cost_for", lambda *a: 0.0)

    async def fake_resolve(enrichment, own_name=None):
        assert enrichment["competitors"][0]["name"] == "Enento Oyj"
        assert own_name == "Kohde Oy"
        return [PEER]

    monkeypatch.setattr(peers, "resolve", fake_resolve)

    pipeline = _pipeline()
    rid = store.create_run(
        pipeline["id"],
        {"meta": {"company_name": "Kohde Oy"}, "peers": []},
        True,
    )
    run = store.get_run(rid)

    async def drain():
        async for _ in runner.run_stages(run, pipeline["stages"]):
            pass

    asyncio.run(drain())

    writer_prompt = prompts[-1]
    assert '"ev_per_ebitda": 9.4' in writer_prompt
    assert "Valuatum /modeldata (fid 555)" in writer_prompt
    # And they stay on the enrichment result, so a writer-only rerun still has
    # them without paying for another enrichment call.
    stored = store.get_run(rid)["results"][1]["parsed_json"]
    assert stored["peers"] == [PEER]

    async def rerun_writer_only():
        async for _ in runner.run_stages(store.get_run(rid), pipeline["stages"],
                                         only=2):
            pass

    asyncio.run(rerun_writer_only())
    assert '"ev_per_ebitda": 9.4' in prompts[-1]
