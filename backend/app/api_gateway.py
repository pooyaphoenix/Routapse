"""OpenAI-compatible data plane. Point any OpenAI SDK at /v1 and use model="router:<id>"."""
import json, time, uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import engine
from .auth import gateway_auth
from .schemas import ChatRequest, Message
from .store import store

api = APIRouter(prefix="/v1", dependencies=[Depends(gateway_auth)])


async def _sse(cid: str, model: str, chunks, info):
    def chunk(delta, finish=None, **extra):
        return "data: " + json.dumps({"id": cid, "object": "chat.completion.chunk",
                                      "created": int(time.time()), "model": model,
                                      "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                                      **extra}) + "\n\n"
    yield chunk({"role": "assistant", "content": ""})
    try:
        async for text in chunks:
            yield chunk({"content": text})
    except Exception as e:  # noqa: BLE001 - headers are already sent, so report inside the stream
        detail = str(getattr(e, "detail", None) or type(e).__name__)
        yield "data: " + json.dumps({"error": {"message": detail, "type": "upstream_error"}}) + "\n\n"
        return
    yield chunk({}, "stop", routapse=info)
    yield "data: [DONE]\n\n"


@api.get("/models")
def list_models():
    ids = [f"router:{r['id']}" for r in store.list("routers")] + [m["id"] for m in store.list("models")]
    return {"object": "list", "data": [{"id": i, "object": "model", "owned_by": "routapse"} for i in ids]}


@api.post("/chat/completions")
async def chat(req: ChatRequest, request: Request):
    messages = [m.model_dump() for m in req.messages]
    params = {"temperature": req.temperature, "max_tokens": req.max_tokens, "top_p": req.top_p}
    router = engine.load_router(req.model.split(":", 1)[1]) if req.model.startswith("router:") else None
    meta = {"client": request.client.host if request.client else None, "stream": req.stream}
    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    if req.stream:
        info, model, chunks = await engine.run_stream("gateway", messages, params, router=router,
                                                      model_id=None if router else req.model, meta=meta)
        shown = model or req.model
        headers = {"X-Routapse-Route": info["label"] if info else "direct", "X-Routapse-Model": shown}
        return StreamingResponse(_sse(cid, shown, chunks, info), media_type="text/event-stream", headers=headers)
    info, out = await engine.run("gateway", messages, params, router=router,
                                 model_id=None if router else req.model, meta=meta)
    shown = out["model"] or req.model
    headers = {"X-Routapse-Route": info["label"] if info else "direct", "X-Routapse-Model": shown}
    return JSONResponse({
        "id": cid, "object": "chat.completion", "created": int(time.time()), "model": shown,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": out["text"]}}],
        "usage": out["usage"], "routapse": info}, headers=headers)


@api.post("/route/{router_id}")
async def route_only(router_id: str, messages: list[Message], request: Request):
    """Middle-model mode: get the routing decision (and Laya signals) without calling any LLM."""
    if not messages:
        raise HTTPException(400, "messages must not be empty")
    info, _ = await engine.run("gateway-route", [m.model_dump() for m in messages], {},
                               router=engine.load_router(router_id), call_model=False,
                               meta={"client": request.client.host if request.client else None})
    return info
