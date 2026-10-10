"""Routing engine: classify -> rules -> pick route -> (optionally) call the target model -> log."""
import time

import httpx
from fastapi import HTTPException

from . import providers
from .config import settings
from .reqlog import log_event
from .schemas import ModelDef, Provider, RouterDef, Route
from .store import store


def load_router(router_id: str) -> RouterDef:
    d = store.get("routers", router_id)
    if not d:
        raise HTTPException(404, f"router '{router_id}' not found")
    return RouterDef(**d)


def _rule_hit(rule, signals: dict) -> bool:
    v = signals.get(rule.signal)
    if v is None:
        return False
    if rule.op == "is":
        return str(v).strip().lower() == rule.value.strip().lower()
    try:
        return float(v) >= float(rule.value) if rule.op == "gte" else float(v) <= float(rule.value)
    except (TypeError, ValueError):
        return False


async def decide(router: RouterDef, messages: list[dict], force_label: str | None = None) -> dict:
    if not router.routes:
        raise HTTPException(400, f"router '{router.id}' has no routes")
    by_label = {x.label: x for x in router.routes}
    if force_label:
        if force_label not in by_label:
            raise HTTPException(400, f"lane '{force_label}' does not exist")
        return {"label": force_label, "confidence": 1.0, "reason": "lane chosen by hand", "latency_ms": 0,
                "route": by_label[force_label], "signals": {}, "laya_model": None}

    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    prompt = last_user
    if len(messages) > 1:  # a little context helps follow-up questions
        prior = " ".join(m["content"] for m in messages[-4:-1] if m["role"] != "system")[-800:]
        if prior:
            prompt = f"(earlier context: {prior})\n{last_user}"
    signals_def = [s.model_dump() for s in router.signals]
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=120) as c:  # first Laya call may download a checkpoint
            r = await c.post(f"{settings.router_url}/classify", json={
                "prompt": prompt, "model": router.router_model, "signals": signals_def,
                "candidates": [{"label": x.label, "description": x.description, "examples": x.examples}
                               for x in router.routes]})
            r.raise_for_status()
            res = r.json()
    except httpx.HTTPError as e:
        res = {"label": None, "confidence": 0.0, "source": f"router unreachable ({type(e).__name__})"}
    label, conf, reason = res.get("label"), res.get("confidence") or 0.0, res.get("source", "")
    signals = res.get("signals") or {}

    hit = next((r for r in router.rules if r.route_label in by_label and _rule_hit(r, signals)), None)
    if hit:
        label, reason = hit.route_label, f"rule: {hit.signal} {hit.op} {hit.value} -> {hit.route_label}"
    elif label not in by_label or conf < router.min_confidence:
        fb = router.fallback_label if router.fallback_label in by_label else router.routes[0].label
        reason = f"fallback -> {fb} ({reason}, confidence {conf:.2f})"
        label = fb
    return {"label": label, "confidence": conf, "reason": reason, "signals": signals,
            "laya_model": res.get("laya_model"), "route": by_label[label],
            "latency_ms": int((time.perf_counter() - t0) * 1000)}


def resolve_model(model_id: str) -> tuple[Provider, ModelDef]:
    m = store.get("models", model_id)
    if not m:
        raise HTTPException(400, f"model '{model_id}' is not defined")
    md = ModelDef(**m)
    p = store.get("providers", md.provider_id)
    if not p:
        raise HTTPException(400, f"provider '{md.provider_id}' is not defined")
    return Provider(**p), md


def _provider_error(provider: Provider, e: httpx.HTTPError) -> HTTPException:
    if isinstance(e, httpx.HTTPStatusError):
        return HTTPException(502, f"{provider.kind} returned {e.response.status_code}: {e.response.text[:300]}")
    return HTTPException(502, f"could not reach {provider.kind}: {type(e).__name__}")


def _target(route: Route, messages: list[dict], model_id: str | None = None):
    model_id = model_id or route.model_id
    if not model_id:
        raise HTTPException(400, f"route '{route.label}' has no target model")
    provider, md = resolve_model(model_id)
    msgs = list(messages)
    if route.system_prompt:
        msgs = [{"role": "system", "content": route.system_prompt}] + msgs
    return provider, md, msgs


def _candidates(route: Route) -> list[str | None]:
    return [route.model_id] + ([route.fallback_model_id] if route.fallback_model_id else [])


def _fallback_note(route: Route, primary_error: str | None) -> dict:
    return {"fallback_from": route.model_id, "fallback_reason": primary_error} if primary_error else {}


async def execute(route: Route, messages: list[dict], params: dict) -> dict:
    if route.action == "respond":
        return {"text": route.response_text, "model": None, "usage": {}}
    primary_error = None
    for i, model_id in enumerate(_candidates(route)):
        try:
            provider, md, msgs = _target(route, messages, model_id)
        except HTTPException:
            if i == 0:
                raise
            break  # a missing fallback must not hide the primary failure
        try:
            text, usage = await providers.complete(provider, md.model_name, msgs, params)
        except httpx.HTTPError as e:
            err = _provider_error(provider, e)
            if i == len(_candidates(route)) - 1:
                raise err if i == 0 else HTTPException(502, f"{primary_error}; fallback {md.id}: {err.detail}")
            primary_error = err.detail
            continue
        return {"text": text, "model": md.id, "usage": usage, **_fallback_note(route, primary_error)}
    raise HTTPException(502, primary_error)


def public(d: dict) -> dict:
    r = d["route"]
    return {"label": d["label"], "confidence": d["confidence"], "reason": d["reason"],
            "latency_ms": d["latency_ms"], "action": r.action, "model_id": r.model_id,
            "signals": d.get("signals") or {}, "laya_model": d.get("laya_model")}


async def run(source: str, messages: list[dict], params: dict, router: RouterDef | None = None,
              model_id: str | None = None, force_label: str | None = None,
              call_model: bool = True, meta: dict | None = None):
    """Single entry point for gateway, studio and tests. Always writes one log line."""
    t0 = time.perf_counter()
    ev = {"source": source, "router_id": router.id if router else None,
          "requested_model": f"router:{router.id}" if router else model_id,
          "request": {"messages": messages, "params": {k: v for k, v in params.items() if v is not None}},
          **(meta or {})}
    info = out = None
    try:
        if router:
            d = await decide(router, messages, force_label)
            route, info = d["route"], public(d)
        else:
            route = Route(label="direct", model_id=model_id)
        if call_model:
            out = await execute(route, messages, params)
    except Exception as e:  # noqa: BLE001 - log, then let FastAPI answer
        log_event({**ev, "decision": info, "status": "error",
                   "error": str(getattr(e, "detail", None) or repr(e)),
                   "latency_ms": int((time.perf_counter() - t0) * 1000)})
        raise
    log_event({**ev, "decision": info, "status": "ok", "target_model": out["model"] if out else None,
               "response": {"text": out["text"], "usage": out["usage"]} if out else None,
               **({k: out[k] for k in ("fallback_from", "fallback_reason") if k in out} if out else {}),
               "latency_ms": int((time.perf_counter() - t0) * 1000)})
    return info, out


async def run_stream(source: str, messages: list[dict], params: dict, router: RouterDef | None = None,
                     model_id: str | None = None, meta: dict | None = None):
    """Streaming twin of run(). Returns (info, model, chunks) once the first chunk has arrived, so routing
    and provider connection errors still become normal HTTP errors. The log line is written when the
    stream ends, fails or is abandoned by the client."""
    t0 = time.perf_counter()
    ev = {"source": source, "router_id": router.id if router else None,
          "requested_model": f"router:{router.id}" if router else model_id,
          "request": {"messages": messages, "params": {k: v for k, v in params.items() if v is not None}},
          **(meta or {})}

    def finish(info, model, parts, usage, status, error=None):
        log_event({**ev, "decision": info, "status": status, "target_model": model,
                   "response": {"text": "".join(parts), "usage": usage},
                   **({"error": error} if error else {}),
                   "latency_ms": int((time.perf_counter() - t0) * 1000)})

    info = provider = None
    try:
        if router:
            d = await decide(router, messages)
            route, info = d["route"], public(d)
        else:
            route = Route(label="direct", model_id=model_id)
        if route.action == "respond":
            model, source_iter, first = None, None, route.response_text
            usage = {}
        else:
            primary_error = None
            for i, candidate_id in enumerate(_candidates(route)):
                provider, md, msgs = _target(route, messages, candidate_id)
                usage = {}
                source_iter = providers.stream(provider, md.model_name, msgs, params, usage)
                try:
                    first = await anext(source_iter, None)
                except httpx.HTTPError as e:
                    await source_iter.aclose()
                    err = _provider_error(provider, e)
                    if i == len(_candidates(route)) - 1:
                        raise err if i == 0 else HTTPException(502, f"{primary_error}; fallback {md.id}: {err.detail}")
                    primary_error = err.detail
                    continue
                model = md.id
                if primary_error:
                    info = {**(info or {}), **_fallback_note(route, primary_error)}
                break
    except Exception as e:  # noqa: BLE001 - log, then let FastAPI answer
        if isinstance(e, httpx.HTTPError) and provider:
            e = _provider_error(provider, e)
        log_event({**ev, "decision": info, "status": "error",
                   "error": str(getattr(e, "detail", None) or repr(e)),
                   "latency_ms": int((time.perf_counter() - t0) * 1000)})
        raise e

    async def chunks():
        parts, status, error = [], "cancelled", None
        usage_out = {} if source_iter is None else usage
        try:
            if first:
                parts.append(first)
                yield first
            if source_iter is not None:
                async for text in source_iter:
                    parts.append(text)
                    yield text
            status = "ok"
        except httpx.HTTPError as e:
            status, error = "error", _provider_error(provider, e).detail
            raise
        except Exception as e:  # noqa: BLE001
            status, error = "error", repr(e)
            raise
        finally:
            if source_iter is not None:
                await source_iter.aclose()
            finish(info, model, parts, usage_out, status, error)

    return info, model, chunks()
