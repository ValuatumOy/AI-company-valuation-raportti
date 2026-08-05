"""OpenRouter wrapper. Key stays server-side. OpenAI-compatible chat API.

Docs: https://openrouter.ai/docs  (chat/completions is OpenAI-compatible;
reasoning is passed as {"reasoning": {"effort": "high"}}).
"""
import asyncio
import json
import os
import re
from pathlib import Path

import httpx

BASE = "https://openrouter.ai/api/v1"
GOOGLE_BASE = "https://generativelanguage.googleapis.com/v1beta"
_models_cache: list[dict] = []
_price_index: dict[str, dict] = {}

_FAKE_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "_fixtures"
_FAKE_FALLBACK_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "runs"

_DIRECT_GOOGLE_MODELS = [
    {
        "id": "google/gemini-3.1-pro-preview",
        "name": "Google Gemini 3.1 Pro Preview (direct)",
        "context_length": None,
        "prompt_price": 0.0,
        "completion_price": 0.0,
    },
]


def _headers() -> dict:
    key = os.getenv("OPENROUTER_API_KEY", "")
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    ref = os.getenv("OPENROUTER_REFERER")
    title = os.getenv("OPENROUTER_TITLE")
    if ref:
        h["HTTP-Referer"] = ref
    if title:
        h["X-Title"] = title
    return h


def _google_api_key() -> str:
    return (
        os.getenv("GEMINI_API_KEY", "")
        or os.getenv("GOOGLE_API_KEY", "")
        or os.getenv("GOOGLE_GENERATIVE_AI_API_KEY", "")
    )


def _google_headers() -> dict:
    key = _google_api_key()
    if not key:
        raise RuntimeError(
            "Google Gemini API key puuttuu. Aseta GEMINI_API_KEY tai GOOGLE_API_KEY."
        )
    return {"x-goog-api-key": key, "Content-Type": "application/json"}


def _is_google_gemini(model: str) -> bool:
    m = (model or "").lower()
    return m.startswith("google/gemini") or m.startswith("gemini-")


def _google_model_id(model: str) -> str:
    return model.split("/", 1)[1] if model.startswith("google/") else model


def _finish_reason_google(raw: str | None) -> str:
    r = (raw or "STOP").lower()
    return {
        "stop": "stop",
        "max_tokens": "length",
        "safety": "safety",
        "recitation": "recitation",
    }.get(r, r)


def _add_direct_google_models(out: list[dict], idx: dict[str, dict]):
    for model in _DIRECT_GOOGLE_MODELS:
        if model["id"] not in idx:
            out.append(model)
            idx[model["id"]] = model


async def refresh_models() -> list[dict]:
    """Fetch + cache the live model list. Tolerates being offline / no key."""
    global _models_cache, _price_index
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{BASE}/models", headers=_headers())
            r.raise_for_status()
            data = r.json().get("data", [])
    except Exception:
        if _models_cache:
            return _models_cache  # keep whatever we had; frontend allows manual id
        out, idx = [], {}
        _add_direct_google_models(out, idx)
        _models_cache, _price_index = out, idx
        return out
    out = []
    idx = {}
    for m in data:
        pricing = m.get("pricing", {}) or {}
        item = {
            "id": m.get("id"),
            "name": m.get("name", m.get("id")),
            "context_length": m.get("context_length"),
            "prompt_price": float(pricing.get("prompt", 0) or 0),
            "completion_price": float(pricing.get("completion", 0) or 0),
        }
        out.append(item)
        idx[item["id"]] = item
    _add_direct_google_models(out, idx)
    out.sort(key=lambda x: (x["name"] or "").lower())
    _models_cache, _price_index = out, idx
    return out


def models() -> list[dict]:
    return _models_cache


def cost_for(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    p = _price_index.get(model)
    if not p:
        return 0.0
    return prompt_tokens * p["prompt_price"] + completion_tokens * p["completion_price"]


def runs_paused() -> bool:
    """Global kill switch: RUNS_PAUSED=1 blocks every LLM call (all providers).

    EMERGENCY DEFAULT (2026-07-05): while RUNS_PAUSED is unset, production
    (= APP_TOKEN set) is PAUSED. Set RUNS_PAUSED=0 in Railway to resume.
    Local dev/tests (no APP_TOKEN) stay unpaused."""
    flag = os.getenv("RUNS_PAUSED", "").strip().lower()
    if flag:
        return flag in ("1", "true", "yes")
    return bool(os.getenv("APP_TOKEN"))


def fake_llm_enabled() -> bool:
    if os.getenv("FAKE_LLM", "").strip().lower() not in ("1", "true", "yes"):
        return False
    if os.getenv("DATABASE_URL", "").strip():
        raise RuntimeError(
            "FAKE_LLM on päällä mutta DATABASE_URL osoittaa tuotantokantaan. "
            "Fixture-toisto on tarkoitettu vain paikalliseen kehitykseen."
        )
    return True


def _fake_dir() -> Path:
    configured = os.getenv("FAKE_LLM_DIR", "").strip()
    return Path(configured) if configured else _FAKE_DIR_DEFAULT


def _fake_fixture() -> dict:
    company = os.getenv("FAKE_LLM_COMPANY", "").strip()
    names = [f"{company}.json"] if company else []
    for directory in (_fake_dir(), _FAKE_FALLBACK_DIR):
        if not directory.is_dir():
            continue
        for name in names:
            path = directory / name
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        available = sorted(directory.glob("*.json"))
        if available:
            return json.loads(available[0].read_text(encoding="utf-8"))
    raise RuntimeError(
        f"FAKE_LLM on päällä mutta fixtureita ei löytynyt hakemistoista "
        f"{_fake_dir()} / {_FAKE_FALLBACK_DIR}."
    )


async def _fake_chat(model: str, prompt: str, expects_json: bool) -> dict:
    fixture = _fake_fixture()
    writer_output = fixture.get("writer_output")
    if _is_writer_prompt(prompt) and writer_output is not None:
        text = json.dumps(writer_output, ensure_ascii=False)
    elif expects_json:
        text = json.dumps(
            {"_fake": True, "notes": [], "sources": []}, ensure_ascii=False
        )
    else:
        text = "(FAKE_LLM: tallenteesta toistettu vaihe, ei oikeaa mallikutsua.)"
    await asyncio.sleep(0)
    return {
        "text": text,
        "finish_reason": "stop",
        "tokens_prompt": 0,
        "tokens_completion": 0,
        "request_payload": {"model": model, "fake_llm": True,
                            "fixture_company": fixture.get("company")},
    }


def _is_writer_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    return "machine_readable" in p or "sections" in p


async def chat(
    model: str,
    prompt: str,
    temperature: float = 0.2,
    max_tokens: int = 16000,
    reasoning_effort: str | None = None,
    expects_json: bool = True,
    web_search: bool = False,
) -> dict:
    """One-shot completion. Returns dict with text, usage, finish_reason, payload."""
    if fake_llm_enabled():
        return await _fake_chat(model, prompt, expects_json)
    if runs_paused():
        raise RuntimeError(
            "Ajot on väliaikaisesti keskeytetty ylläpidon toimesta (RUNS_PAUSED)."
        )
    if _is_google_gemini(model):
        return await _google_chat(
            model=model,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            expects_json=expects_json,
            web_search=web_search,
        )
    return await _openrouter_chat(
        model=model,
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        expects_json=expects_json,
        web_search=web_search,
    )


async def _openrouter_chat(
    model: str,
    prompt: str,
    temperature: float = 0.2,
    max_tokens: int = 16000,
    reasoning_effort: str | None = None,
    expects_json: bool = True,
    web_search: bool = False,
) -> dict:
    messages = [{"role": "user", "content": prompt}]
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if expects_json:
        # Soft nudge; not all models honor it, the tolerant extractor backs it up.
        payload["response_format"] = {"type": "json_object"}
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    if web_search:
        # OpenRouter web plugin — live web results injected before generation.
        # https://openrouter.ai/docs/guides/features/plugins/web-search
        payload["plugins"] = [{"id": "web"}]

    # Retry transient failures so a single blip doesn't kill a 6-stage run:
    # 429 rate limit, 5xx, network/timeout, AND a 200 carrying a malformed or
    # truncated body (z-ai/glm-5.2 occasionally returns one — that crashed stage 3
    # with "Expecting value: line N column 1").
    # A heavy single-writer stage (large max_tokens, usually with web search
    # injecting a lot of context via the native agentic engine) can legitimately
    # run well past 10 min. Give such stages headroom — the run is a server-side
    # background task, so a long request is fine. Ordinary stages keep 600s.
    # 2700s, not 1500: a full Fable report at ~60-90k completion tokens runs
    # 20-35 min. A client timeout here retries the call — and the provider bills
    # the aborted generation anyway — so an undersized timeout multiplies cost.
    client_timeout = 2700 if (max_tokens or 0) >= 40000 else 600
    body = None
    for attempt in range(4):
        last = attempt == 3
        try:
            async with httpx.AsyncClient(timeout=client_timeout) as client:
                r = await client.post(
                    f"{BASE}/chat/completions", headers=_headers(), json=payload
                )
            if r.status_code in (429, 500, 502, 503, 504):
                if last:
                    raise RuntimeError(f"OpenRouter {r.status_code}: {r.text[:800]}")
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"OpenRouter {r.status_code}: {r.text[:800]}")
            try:
                body = r.json()
            except Exception:
                if last:
                    raise RuntimeError(
                        "OpenRouter palautti virheellisen/katkenneen vastauksen "
                        f"(ei kelvollista JSONia): {(r.text or '')[:500]}"
                    )
                await asyncio.sleep(2 ** attempt)
                continue
            break
        except (httpx.TimeoutException, httpx.TransportError):
            if last:
                raise
            await asyncio.sleep(2 ** attempt)
            continue
    choice = (body.get("choices") or [{}])[0]
    msg = choice.get("message", {}) or {}
    text = msg.get("content") or ""
    if not text and msg.get("reasoning"):  # some models leak only reasoning
        text = msg["reasoning"]
    usage = body.get("usage", {}) or {}
    return {
        "text": text,
        "finish_reason": choice.get("finish_reason", "stop"),
        "tokens_prompt": int(usage.get("prompt_tokens", 0) or 0),
        "tokens_completion": int(usage.get("completion_tokens", 0) or 0),
        "request_payload": payload,
    }


async def _google_chat(
    model: str,
    prompt: str,
    temperature: float = 0.2,
    max_tokens: int = 16000,
    expects_json: bool = True,
    web_search: bool = False,
) -> dict:
    """Gemini Developer API generateContent call, bypassing OpenRouter."""
    model_id = _google_model_id(model)
    payload: dict = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if expects_json:
        payload["generationConfig"]["responseMimeType"] = "application/json"
    if web_search:
        payload["tools"] = [{"google_search": {}}]

    # 2700s, not 1500: a full Fable report at ~60-90k completion tokens runs
    # 20-35 min. A client timeout here retries the call — and the provider bills
    # the aborted generation anyway — so an undersized timeout multiplies cost.
    client_timeout = 2700 if (max_tokens or 0) >= 40000 else 600
    body = None
    endpoint = f"{GOOGLE_BASE}/models/{model_id}:generateContent"
    for attempt in range(4):
        last = attempt == 3
        try:
            async with httpx.AsyncClient(timeout=client_timeout) as client:
                r = await client.post(endpoint, headers=_google_headers(), json=payload)
            if r.status_code in (429, 500, 502, 503, 504):
                if last:
                    raise RuntimeError(f"Google Gemini {r.status_code}: {r.text[:800]}")
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Google Gemini {r.status_code}: {r.text[:800]}")
            try:
                body = r.json()
            except Exception:
                if last:
                    raise RuntimeError(
                        "Google Gemini palautti virheellisen/katkenneen vastauksen "
                        f"(ei kelvollista JSONia): {(r.text or '')[:500]}"
                    )
                await asyncio.sleep(2 ** attempt)
                continue
            break
        except (httpx.TimeoutException, httpx.TransportError):
            if last:
                raise
            await asyncio.sleep(2 ** attempt)
            continue

    candidates = body.get("candidates") or []
    if not candidates:
        feedback = body.get("promptFeedback") or body.get("prompt_feedback") or {}
        raise RuntimeError(
            "Google Gemini ei palauttanut vastausta. "
            + (json.dumps(feedback, ensure_ascii=False)[:800] if feedback else "")
        )
    choice = candidates[0]
    content = choice.get("content", {}) or {}
    parts = content.get("parts") or []
    text = "".join(
        p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")
    )
    usage = body.get("usageMetadata", {}) or {}
    prompt_tokens = int(usage.get("promptTokenCount", 0) or 0)
    completion_tokens = int(usage.get("candidatesTokenCount", 0) or 0) + int(
        usage.get("thoughtsTokenCount", 0) or 0
    )
    return {
        "text": text,
        "finish_reason": _finish_reason_google(choice.get("finishReason")),
        "tokens_prompt": prompt_tokens,
        "tokens_completion": completion_tokens,
        "request_payload": {
            "provider": "google",
            "model": model_id,
            **payload,
        },
    }


# ---- tolerant JSON extraction ----------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str):
    """Strip fences, take the largest balanced {...} block, parse. None on fail."""
    if not text:
        return None
    candidates = [text]
    candidates += _FENCE.findall(text)
    for cand in candidates:
        try:
            return json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            pass
    # largest balanced object
    blk = _largest_balanced(text)
    if blk:
        try:
            return json.loads(blk)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _largest_balanced(s: str):
    best = None
    stack = []
    start = None
    for i, ch in enumerate(s):
        if ch == "{":
            if not stack:
                start = i
            stack.append("{")
        elif ch == "}" and stack:
            stack.pop()
            if not stack and start is not None:
                blk = s[start : i + 1]
                if best is None or len(blk) > len(best):
                    best = blk
    return best
