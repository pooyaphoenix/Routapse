"""Control plane used by the GUI (and usable from scripts)."""
import httpx
from fastapi import APIRouter, Depends, HTTPException

from . import engine, reqlog
from .auth import admin_auth
from .config import settings
from .schemas import ModelDef, Provider, RouterDef, SavedPrompt, StudioRequest, TestRequest
from .store import store

api = APIRouter(prefix="/admin", dependencies=[Depends(admin_auth)])
MASK = "***"


def _mask(p: dict) -> dict:
    return {**p, "api_key": MASK if p.get("api_key") else ""}


# ---- providers ----
@api.get("/providers")
def providers_list():
    return [_mask(p) for p in store.list("providers")]


@api.put("/providers/{id}")
def providers_put(id: str, body: Provider):
    if body.id != id:
        raise HTTPException(400, "id in path and body differ")
    old = store.get("providers", id)
    if body.api_key == MASK and old:   # GUI sent the masked value back: keep the stored key
        body.api_key = old["api_key"]
    store.put("providers", id, body.model_dump())
    return _mask(body.model_dump())


@api.delete("/providers/{id}")
def providers_delete(id: str):
    if any(m["provider_id"] == id for m in store.list("models")):
        raise HTTPException(409, "remove the models that use this provider first")
    store.delete("providers", id)
    return {"ok": True}


# ---- models ----
@api.get("/models")
def models_list():
    return store.list("models")


@api.put("/models/{id}")
def models_put(id: str, body: ModelDef):
    if body.id != id:
        raise HTTPException(400, "id in path and body differ")
    if not store.get("providers", body.provider_id):
        raise HTTPException(400, f"provider '{body.provider_id}' does not exist")
    store.put("models", id, body.model_dump())
    return body


@api.delete("/models/{id}")
def models_delete(id: str):
    used = [r["name"] for r in store.list("routers")
            if any(id in (x.get("model_id"), x.get("fallback_model_id")) for x in r.get("routes", []))]
    if used:
        raise HTTPException(409, f"still used by router(s): {', '.join(used)}")
    store.delete("models", id)
    return {"ok": True}


# ---- routers ----
@api.get("/routers")
def routers_list():
    return store.list("routers")


@api.put("/routers/{id}")
def routers_put(id: str, body: RouterDef):
    if body.id != id:
        raise HTTPException(400, "id in path and body differ")
    labels = [r.label for r in body.routes]
    if not labels:
        raise HTTPException(400, "router must have at least one route")
    if len(set(labels)) != len(labels):
        raise HTTPException(400, "route labels must be unique")
    if body.fallback_label and body.fallback_label not in labels:
        raise HTTPException(400, f"fallback points at unknown lane '{body.fallback_label}'")
    for r in body.routes:
        if r.action == "forward" and not r.model_id:
            raise HTTPException(400, f"route '{r.label}' must have a target model")
        if r.action == "forward" and not store.get("models", r.model_id):
            raise HTTPException(400, f"route '{r.label}': model '{r.model_id}' does not exist")
        if r.fallback_model_id and r.fallback_model_id == r.model_id:
            raise HTTPException(400, f"route '{r.label}': fallback model must differ from the target model")
        if r.fallback_model_id and not store.get("models", r.fallback_model_id):
            raise HTTPException(400, f"route '{r.label}': fallback model '{r.fallback_model_id}' does not exist")
    if body.router_model != "laya" and (body.signals or body.rules):
        raise HTTPException(400, "signals and rules require the Laya router model")
    names = {x.name for x in body.signals}
    for rule in body.rules:
        if rule.signal not in names:
            raise HTTPException(400, f"rule uses unknown signal '{rule.signal}'")
        if rule.route_label not in labels:
            raise HTTPException(400, f"rule points at unknown lane '{rule.route_label}'")
    store.put("routers", id, body.model_dump())
    return body


@api.delete("/routers/{id}")
def routers_delete(id: str):
    store.delete("routers", id)
    return {"ok": True}


# ---- playground (router editor) ----
@api.post("/test")
async def test(req: TestRequest):
    router = req.router or (engine.load_router(req.router_id) if req.router_id else None)
    if not router:
        raise HTTPException(400, "send router_id or a draft router")
    info, out = await engine.run("test", [{"role": "user", "content": req.prompt}], {}, router=router,
                                 call_model=req.execute)
    return {"decision": info, **({"response": out} if out else {})}


# ---- prompt studio ----
@api.post("/chat")
async def studio_chat(req: StudioRequest):
    if not (req.router_id or req.model_id):
        raise HTTPException(400, "choose a router or a model")
    router = engine.load_router(req.router_id) if req.router_id else None
    msgs = ([{"role": "system", "content": req.system}] if req.system.strip() else []) + [m.model_dump() for m in req.messages]
    info, out = await engine.run("studio", msgs, {"temperature": req.temperature, "max_tokens": req.max_tokens},
                                 router=router, model_id=None if router else req.model_id,
                                 force_label=req.force_label, call_model=not req.route_only)
    return {"decision": info, "response": out}


@api.get("/prompts")
def prompts_list():
    return store.list("prompts")


@api.put("/prompts/{id}")
def prompts_put(id: str, body: SavedPrompt):
    if body.id != id:
        raise HTTPException(400, "id in path and body differ")
    store.put("prompts", id, body.model_dump())
    return body


@api.delete("/prompts/{id}")
def prompts_delete(id: str):
    store.delete("prompts", id)
    return {"ok": True}


# ---- request log ----
@api.get("/logs")
def logs(limit: int = 100, offset: int = 0, router_id: str | None = None,
         source: str | None = None, q: str | None = None):
    return reqlog.read_logs(min(limit, 500), offset, router_id or None, source or None, q or None)


@api.get("/logs/info")
def logs_info():
    return reqlog.info()


# ---- Ollama auto-discovery ----
@api.get("/ollama/models")
async def ollama_models(base_url: str | None = None):
    base = (base_url or settings.ollama_url).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(f"{base}/api/tags")
            r.raise_for_status()
    except httpx.HTTPError as e:
        return {"reachable": False, "base_url": base, "models": [], "error": type(e).__name__}
    models = [{"name": m["name"], "bytes": m.get("size"),
               "params": (m.get("details") or {}).get("parameter_size"),
               "family": (m.get("details") or {}).get("family")} for m in r.json().get("models", [])]
    return {"reachable": True, "base_url": base, "models": models}
