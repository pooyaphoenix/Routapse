"""Provider adapters. complete() returns (text, usage); stream() yields text deltas and fills a usage dict."""
import asyncio
import json

import httpx

from .config import settings
from .schemas import Provider

client = httpx.AsyncClient(timeout=120)
PARAMS = ("temperature", "max_tokens", "top_p")


def _split_system(messages: list[dict]):
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    return system, [m for m in messages if m["role"] != "system"]


def _openai_request(p: Provider, model: str, messages, params):
    if p.kind == "ollama":
        base = (p.base_url or settings.ollama_url).rstrip("/") + "/v1"
    else:
        base = (p.base_url or "https://api.openai.com/v1").rstrip("/")
    return f"{base}/chat/completions", {
        "headers": {"Authorization": f"Bearer {p.api_key or 'none'}"},
        "json": {"model": model, "messages": messages, **params}}


def _anthropic_request(p: Provider, model: str, messages, params):
    system, msgs = _split_system(messages)
    body = {"model": model, "messages": msgs, "max_tokens": params.get("max_tokens", 1024)}
    if system:
        body["system"] = system
    for k in ("temperature", "top_p"):
        if k in params:
            body[k] = params[k]
    return (p.base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages", {
        "headers": {"x-api-key": p.api_key, "anthropic-version": "2023-06-01"}, "json": body}


def _gemini_request(p: Provider, model: str, messages, params, method="generateContent"):
    system, msgs = _split_system(messages)
    body = {"contents": [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in msgs]}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    cfg = {}
    if "temperature" in params: cfg["temperature"] = params["temperature"]
    if "top_p" in params: cfg["topP"] = params["top_p"]
    if "max_tokens" in params: cfg["maxOutputTokens"] = params["max_tokens"]
    if cfg:
        body["generationConfig"] = cfg
    base = (p.base_url or "https://generativelanguage.googleapis.com").rstrip("/")
    return f"{base}/v1beta/models/{model}:{method}", {"params": {"key": p.api_key}, "json": body}


def _anthropic_usage(u: dict) -> dict:
    return {"prompt_tokens": u.get("input_tokens"), "completion_tokens": u.get("output_tokens")}


def _gemini_usage(u: dict) -> dict:
    return {"prompt_tokens": u.get("promptTokenCount"), "completion_tokens": u.get("candidatesTokenCount")}


RETRY_STATUS = {408, 429, 500, 502, 503, 504}
MAX_BACKOFF = 8.0


def _retryable(e: httpx.HTTPError) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code in RETRY_STATUS
    return isinstance(e, httpx.TransportError)


def _backoff(e: httpx.HTTPError, attempt: int) -> float:
    delay = 0.5 * 2 ** attempt
    if isinstance(e, httpx.HTTPStatusError):
        try:
            delay = max(delay, float(e.response.headers.get("retry-after", 0)))
        except ValueError:
            pass
    return min(delay, MAX_BACKOFF)


async def _post(p: Provider, url: str, kw: dict) -> httpx.Response:
    """POST with the provider's timeout, retrying transient failures with exponential backoff."""
    for attempt in range(p.retries + 1):
        try:
            r = await client.post(url, timeout=p.timeout, **kw)
            r.raise_for_status()
            return r
        except httpx.HTTPError as e:
            if attempt == p.retries or not _retryable(e):
                raise
            await asyncio.sleep(_backoff(e, attempt))


async def _openai_like(p: Provider, model: str, messages, params):
    url, kw = _openai_request(p, model, messages, params)
    d = (await _post(p, url, kw)).json()
    return d["choices"][0]["message"]["content"], d.get("usage", {})


async def _anthropic(p: Provider, model: str, messages, params):
    url, kw = _anthropic_request(p, model, messages, params)
    d = (await _post(p, url, kw)).json()
    text = "".join(b.get("text", "") for b in d["content"] if b["type"] == "text")
    return text, _anthropic_usage(d.get("usage", {}))


async def _gemini(p: Provider, model: str, messages, params):
    url, kw = _gemini_request(p, model, messages, params)
    d = (await _post(p, url, kw)).json()
    text = "".join(x.get("text", "") for x in d["candidates"][0]["content"]["parts"])
    return text, _gemini_usage(d.get("usageMetadata", {}))


async def complete(p: Provider, model: str, messages: list[dict], params: dict):
    params = {k: v for k, v in params.items() if k in PARAMS and v is not None}
    fn = {"anthropic": _anthropic, "gemini": _gemini}.get(p.kind, _openai_like)
    return await fn(p, model, messages, params)


async def _events(r: httpx.Response):
    """Yield the JSON payload of every `data:` line of a server-sent-event response."""
    async for line in r.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data and data != "[DONE]":
            yield json.loads(data)


def _openai_delta(ev: dict, usage: dict) -> str:
    if ev.get("usage"):
        usage.update(ev["usage"])
    choices = ev.get("choices") or []
    return (choices[0].get("delta") or {}).get("content") or "" if choices else ""


def _anthropic_delta(ev: dict, usage: dict) -> str:
    kind = ev.get("type")
    if kind == "message_start":
        usage.update(_anthropic_usage((ev.get("message") or {}).get("usage") or {}))
    elif kind == "message_delta":
        usage["completion_tokens"] = (ev.get("usage") or {}).get("output_tokens")
    elif kind == "content_block_delta":
        return (ev.get("delta") or {}).get("text") or ""
    return ""


def _gemini_delta(ev: dict, usage: dict) -> str:
    if ev.get("usageMetadata"):
        usage.update(_gemini_usage(ev["usageMetadata"]))
    cands = ev.get("candidates") or []
    parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
    return "".join(x.get("text", "") for x in parts)


async def stream(p: Provider, model: str, messages: list[dict], params: dict, usage: dict):
    """Yield text deltas as the provider produces them; token usage is written into `usage`."""
    params = {k: v for k, v in params.items() if k in PARAMS and v is not None}
    if p.kind == "anthropic":
        url, kw = _anthropic_request(p, model, messages, params)
        kw["json"]["stream"] = True
        delta = _anthropic_delta
    elif p.kind == "gemini":
        url, kw = _gemini_request(p, model, messages, params, "streamGenerateContent")
        kw["params"]["alt"] = "sse"
        delta = _gemini_delta
    else:
        url, kw = _openai_request(p, model, messages, params)
        kw["json"].update(stream=True, stream_options={"include_usage": True})
        delta = _openai_delta
    for attempt in range(p.retries + 1):
        started = False
        try:
            async with client.stream("POST", url, timeout=p.timeout, **kw) as r:
                if r.is_error:
                    await r.aread()  # so the error body is available to the caller
                    r.raise_for_status()
                async for ev in _events(r):
                    text = delta(ev, usage)
                    if text:
                        started = True
                        yield text
            return
        except httpx.HTTPError as e:
            # once text has reached the caller a retry would repeat it, so only retry before that
            if started or attempt == p.retries or not _retryable(e):
                raise
            usage.clear()
            await asyncio.sleep(_backoff(e, attempt))
