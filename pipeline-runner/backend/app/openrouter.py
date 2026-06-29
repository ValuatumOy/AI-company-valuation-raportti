"""OpenRouter wrapper. Key stays server-side. OpenAI-compatible chat API.

Docs: https://openrouter.ai/docs  (chat/completions is OpenAI-compatible;
reasoning is passed as {"reasoning": {"effort": "high"}}).
"""
import asyncio
import json
import os
import re

import httpx

BASE = "https://openrouter.ai/api/v1"
_models_cache: list[dict] = []
_price_index: dict[str, dict] = {}


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


async def refresh_models() -> list[dict]:
    """Fetch + cache the live model list. Tolerates being offline / no key."""
    global _models_cache, _price_index
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{BASE}/models", headers=_headers())
            r.raise_for_status()
            data = r.json().get("data", [])
    except Exception:
        return _models_cache  # keep whatever we had; frontend allows manual id
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
    body = None
    for attempt in range(4):
        last = attempt == 3
        try:
            async with httpx.AsyncClient(timeout=600) as client:
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
