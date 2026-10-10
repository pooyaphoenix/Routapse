import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app import providers, reqlog
from app.main import app
from app.store import store
from tests.test_engine import FakeRouterClient, response

REAL_ASYNC_CLIENT = httpx.AsyncClient  # classify_as() patches the class globally
PROVIDER = {"id": "local", "kind": "openai_compat", "base_url": "http://llm/v1", "api_key": "secret", "retries": 0}
MODEL = {"id": "fast", "provider_id": "local", "model_name": "llama"}
ROUTER = {
    "id": "support",
    "name": "Support",
    "routes": [
        {"label": "answer", "model_id": "fast"},
        {"label": "refuse", "action": "respond", "response_text": "No."},
    ],
    "fallback_label": "answer",
}
BACKUP_PROVIDER = {"id": "backup", "kind": "openai_compat", "base_url": "http://backup/v1", "retries": 0}
BACKUP_MODEL = {"id": "spare", "provider_id": "backup", "model_name": "mistral"}
RESILIENT = {
    "id": "resilient",
    "name": "Resilient",
    "routes": [{"label": "answer", "model_id": "fast", "fallback_model_id": "spare"}],
}
COMPLETION = {"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 3}}


def sse_reply(events):
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, name, value in (
            (store, "path", os.path.join(self.tmp.name, "store.json")),
            (reqlog.settings, "log_dir", os.path.join(self.tmp.name, "logs")),
            (reqlog.settings, "log_bodies", True),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        store.put("providers", "local", PROVIDER)
        store.put("models", "fast", MODEL)
        store.put("routers", "support", ROUTER)
        store.put("providers", "backup", BACKUP_PROVIDER)
        store.put("models", "spare", BACKUP_MODEL)
        store.put("routers", "resilient", RESILIENT)
        self.client = TestClient(app)

    def classify_as(self, label, confidence=0.9):
        result = {"label": label, "confidence": confidence, "signals": {}, "source": "jev"}
        return patch("app.engine.httpx.AsyncClient", return_value=FakeRouterClient(response(200, json=result)))

    def provider_stream(self, handler):
        mock = REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))
        return patch.object(providers, "client", mock)

    def provider_replies(self):
        reply = httpx.Response(200, json=COMPLETION, request=httpx.Request("POST", "http://llm"))
        return patch.object(providers.client, "post", AsyncMock(return_value=reply))
    def stream(self, model="router:support"):
        r = self.client.post("/v1/chat/completions", json={
            "model": model, "stream": True, "messages": [{"role": "user", "content": "hi"}]})
        events = [line[6:] for line in r.text.splitlines() if line.startswith("data: ")]
        return r, [e if e == "[DONE]" else json.loads(e) for e in events]


class GatewayTests(ApiTestCase):
    def test_models_lists_routers_and_models(self):
        ids = [m["id"] for m in self.client.get("/v1/models").json()["data"]]
        self.assertEqual(ids, ["router:support", "router:resilient", "fast", "spare"])

    def test_router_model_forwards_to_lane_model(self):
        with self.classify_as("answer"), self.provider_replies():
            r = self.client.post("/v1/chat/completions", json={
                "model": "router:support", "messages": [{"role": "user", "content": "hi"}]})
        body = r.json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], "hello")
        self.assertEqual(body["model"], "fast")
        self.assertEqual(body["routapse"]["label"], "answer")
        self.assertEqual(r.headers["x-routapse-route"], "answer")

    def test_respond_lane_never_calls_a_provider(self):
        post = AsyncMock()
        with self.classify_as("refuse"), patch.object(providers.client, "post", post):
            r = self.client.post("/v1/chat/completions", json={
                "model": "router:support", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.json()["choices"][0]["message"]["content"], "No.")
        post.assert_not_called()

    def test_direct_model_pass_through(self):
        with self.provider_replies():
            r = self.client.post("/v1/chat/completions", json={
                "model": "fast", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.json()["routapse"], None)
        self.assertEqual(r.headers["x-routapse-route"], "direct")

    def test_stream_forwards_provider_tokens_as_they_arrive(self):
        calls = []

        def handler(request):
            calls.append(json.loads(request.content))
            return sse_reply([
                {"choices": [{"delta": {"role": "assistant", "content": ""}}]},
                {"choices": [{"delta": {"content": "he"}}]},
                {"choices": [{"delta": {"content": "llo"}}]},
                {"choices": [], "usage": {"total_tokens": 3}},
            ])

        with self.classify_as("answer"), self.provider_stream(handler):
            r, events = self.stream()
        deltas = [e["choices"][0]["delta"].get("content") for e in events[:-1]]
        self.assertEqual(deltas, ["", "he", "llo", None])
        self.assertEqual(events[-2]["choices"][0]["finish_reason"], "stop")
        self.assertEqual(events[-2]["routapse"]["label"], "answer")
        self.assertEqual(events[-1], "[DONE]")
        self.assertEqual(r.headers["x-routapse-model"], "fast")
        self.assertTrue(calls[0]["stream"])
        entry = reqlog.read_logs()[0]
        self.assertEqual((entry["status"], entry["response"]["text"]), ("ok", "hello"))
        self.assertEqual(entry["response"]["usage"], {"total_tokens": 3})

    def test_stream_respond_lane_sends_canned_text_without_provider(self):
        def handler(request):
            raise AssertionError("provider must not be called")

        with self.classify_as("refuse"), self.provider_stream(handler):
            r, events = self.stream()
        self.assertEqual(events[1]["choices"][0]["delta"]["content"], "No.")
        self.assertEqual(events[-1], "[DONE]")
        self.assertEqual(r.headers["x-routapse-model"], "router:support")
        self.assertEqual(reqlog.read_logs()[0]["status"], "ok")

    def test_stream_provider_error_before_first_token_is_a_502(self):
        with self.provider_stream(lambda request: httpx.Response(500, text="boom")):
            r = self.client.post("/v1/chat/completions", json={
                "model": "fast", "stream": True, "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.status_code, 502)
        self.assertIn("boom", r.json()["detail"])
        self.assertEqual(reqlog.read_logs()[0]["status"], "error")

    def test_stream_failure_midway_reports_error_and_logs_partial_text(self):
        async def body():
            yield b'data: {"choices": [{"delta": {"content": "par"}}]}\n\n'
            raise httpx.ReadError("connection lost")

        with self.provider_stream(lambda request: httpx.Response(200, content=body())):
            r, events = self.stream("fast")
        self.assertEqual(events[1]["choices"][0]["delta"]["content"], "par")
        self.assertIn("error", events[-1])
        self.assertNotIn("[DONE]", events)
        entry = reqlog.read_logs()[0]
        self.assertEqual((entry["status"], entry["response"]["text"]), ("error", "par"))

    def test_route_only_skips_the_model(self):
        post = AsyncMock()
        with self.classify_as("answer"), patch.object(providers.client, "post", post):
            r = self.client.post("/v1/route/support", json=[{"role": "user", "content": "hi"}])
        self.assertEqual(r.json()["label"], "answer")
        post.assert_not_called()

    def test_unknown_router_and_model_errors(self):
        messages = [{"role": "user", "content": "hi"}]
        self.assertEqual(self.client.post("/v1/chat/completions", json={
            "model": "router:missing", "messages": messages}).status_code, 404)
        self.assertEqual(self.client.post("/v1/chat/completions", json={
            "model": "missing", "messages": messages}).status_code, 400)

    def test_provider_failure_is_a_logged_502(self):
        reply = httpx.Response(500, text="boom", request=httpx.Request("POST", "http://llm"))
        with patch.object(providers.client, "post", AsyncMock(return_value=reply)):
            r = self.client.post("/v1/chat/completions", json={
                "model": "fast", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.status_code, 502)
        self.assertEqual(reqlog.read_logs()[0]["status"], "error")

    def test_gateway_key_is_enforced(self):
        with patch("app.auth.settings.gateway_key", "key"):
            self.assertEqual(self.client.get("/v1/models").status_code, 401)
            self.assertEqual(self.client.get("/v1/models", headers={"Authorization": "Bearer key"}).status_code, 200)


def primary_down_backup_up(handler_log=None):
    def handler(request):
        if handler_log is not None:
            handler_log.append(request.url.host)
        if request.url.host == "llm":
            return httpx.Response(503, text="primary down")
        return httpx.Response(200, json={"choices": [{"message": {"content": "from backup"}}]})
    return handler


class FallbackTests(ApiTestCase):
    def chat(self, **extra):
        return self.client.post("/v1/chat/completions", json={
            "model": "router:resilient", "messages": [{"role": "user", "content": "hi"}], **extra})

    def test_failed_primary_is_replaced_by_fallback_and_logged(self):
        hosts = []
        with self.classify_as("answer"), self.provider_stream(primary_down_backup_up(hosts)):
            r = self.chat()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["choices"][0]["message"]["content"], "from backup")
        self.assertEqual((r.json()["model"], r.headers["x-routapse-model"]), ("spare", "spare"))
        self.assertEqual(hosts, ["llm", "backup"])
        entry = reqlog.read_logs()[0]
        self.assertEqual((entry["status"], entry["target_model"], entry["fallback_from"]), ("ok", "spare", "fast"))
        self.assertIn("503", entry["fallback_reason"])

    def test_healthy_primary_never_touches_fallback(self):
        hosts = []

        def handler(request):
            hosts.append(request.url.host)
            return httpx.Response(200, json=COMPLETION)

        with self.classify_as("answer"), self.provider_stream(handler):
            r = self.chat()
        self.assertEqual((r.json()["model"], hosts), ("fast", ["llm"]))
        self.assertNotIn("fallback_from", reqlog.read_logs()[0])

    def test_both_failing_reports_both_errors(self):
        with self.classify_as("answer"), self.provider_stream(lambda r: httpx.Response(500, text=f"{r.url.host} broke")):
            r = self.chat()
        self.assertEqual(r.status_code, 502)
        self.assertIn("llm broke", r.json()["detail"])
        self.assertIn("backup broke", r.json()["detail"])
        self.assertEqual(reqlog.read_logs()[0]["status"], "error")

    def test_client_errors_still_use_fallback_because_the_model_is_unusable(self):
        def handler(request):
            if request.url.host == "llm":
                return httpx.Response(401, text="bad key")
            return httpx.Response(200, json=COMPLETION)

        with self.classify_as("answer"), self.provider_stream(handler):
            self.assertEqual(self.chat().json()["model"], "spare")

    def test_deleted_fallback_model_surfaces_the_primary_error(self):
        store.delete("models", "spare")
        with self.classify_as("answer"), self.provider_stream(primary_down_backup_up()):
            r = self.chat()
        self.assertEqual(r.status_code, 502)
        self.assertIn("primary down", r.json()["detail"])

    def test_stream_falls_back_before_the_first_token(self):
        def handler(request):
            if request.url.host == "llm":
                return httpx.Response(503, text="primary down")
            return sse_reply([{"choices": [{"delta": {"content": "from backup"}}]}])

        with self.classify_as("answer"), self.provider_stream(handler):
            r, events = self.stream("router:resilient")
        self.assertEqual(r.headers["x-routapse-model"], "spare")
        self.assertEqual(events[1]["choices"][0]["delta"]["content"], "from backup")
        self.assertEqual(events[-2]["routapse"]["fallback_from"], "fast")
        self.assertEqual(events[-1], "[DONE]")
        entry = reqlog.read_logs()[0]
        self.assertEqual((entry["status"], entry["target_model"]), ("ok", "spare"))

    def test_stream_does_not_switch_models_after_output_started(self):
        async def body():
            yield b'data: {"choices": [{"delta": {"content": "par"}}]}\n\n'
            raise httpx.ReadError("lost")

        hosts = []

        def handler(request):
            hosts.append(request.url.host)
            return httpx.Response(200, content=body())

        with self.classify_as("answer"), self.provider_stream(handler):
            r, events = self.stream("router:resilient")
        self.assertEqual(hosts, ["llm"])
        self.assertIn("error", events[-1])
        self.assertEqual(reqlog.read_logs()[0]["response"]["text"], "par")

    def test_both_failing_stream_is_a_502(self):
        with self.classify_as("answer"), self.provider_stream(lambda r: httpx.Response(500, text="broke")):
            r = self.client.post("/v1/chat/completions", json={
                "model": "router:resilient", "stream": True, "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.status_code, 502)


class AdminTests(ApiTestCase):
    def test_admin_token_is_enforced(self):
        with patch("app.auth.settings.admin_token", "token"):
            self.assertEqual(self.client.get("/admin/routers").status_code, 401)
            self.assertEqual(self.client.get("/admin/routers", headers={"Authorization": "Bearer token"}).status_code, 200)

    def test_provider_keys_are_masked_and_preserved(self):
        self.assertEqual(self.client.get("/admin/providers").json()[0]["api_key"], "***")
        r = self.client.put("/admin/providers/local", json={**PROVIDER, "api_key": "***", "base_url": "http://new/v1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(store.get("providers", "local")["api_key"], "secret")
        self.assertEqual(store.get("providers", "local")["base_url"], "http://new/v1")

    def test_path_and_body_ids_must_match(self):
        self.assertEqual(self.client.put("/admin/models/other", json=MODEL).status_code, 400)

    def test_model_requires_existing_provider(self):
        r = self.client.put("/admin/models/x", json={**MODEL, "id": "x", "provider_id": "nope"})
        self.assertEqual(r.status_code, 400)

    def test_used_provider_and_model_cannot_be_deleted(self):
        self.assertEqual(self.client.delete("/admin/providers/local").status_code, 409)
        self.assertEqual(self.client.delete("/admin/models/fast").status_code, 409)
        self.assertEqual(self.client.delete("/admin/routers/support").status_code, 200)
        self.assertEqual(self.client.delete("/admin/models/fast").status_code, 409)
        self.assertEqual(self.client.delete("/admin/routers/resilient").status_code, 200)
        self.assertEqual(self.client.delete("/admin/models/fast").status_code, 200)
        self.assertEqual(self.client.delete("/admin/providers/local").status_code, 200)

    def test_fallback_model_must_exist_and_differ_from_the_target(self):
        for fallback, text in (("fast", "must differ"), ("ghost", "does not exist")):
            route = {"label": "answer", "model_id": "fast", "fallback_model_id": fallback}
            r = self.client.put("/admin/routers/x", json={**ROUTER, "id": "x", "routes": [route]})
            self.assertEqual(r.status_code, 400)
            self.assertIn(text, r.json()["detail"])

    def test_model_used_as_fallback_cannot_be_deleted(self):
        r = self.client.delete("/admin/models/spare")
        self.assertEqual(r.status_code, 409)
        self.assertIn("Resilient", r.json()["detail"])

    def test_provider_timeout_and_retries_are_validated_and_stored(self):
        ok = self.client.put("/admin/providers/local", json={**PROVIDER, "timeout": 30, "retries": 4})
        self.assertEqual((ok.json()["timeout"], ok.json()["retries"]), (30, 4))
        for bad in ({"timeout": 0}, {"timeout": 9999}, {"retries": -1}, {"retries": 6}):
            self.assertEqual(self.client.put("/admin/providers/local", json={**PROVIDER, **bad}).status_code, 422)

    def test_router_validation_over_http(self):
        r = self.client.put("/admin/routers/bad", json={**ROUTER, "id": "bad", "fallback_label": "missing"})
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(store.get("routers", "bad"))

    def test_test_endpoint_accepts_unsaved_draft(self):
        with self.classify_as("answer"):
            r = self.client.post("/admin/test", json={"router": ROUTER, "prompt": "hi"})
        self.assertEqual(r.json()["decision"]["label"], "answer")
        self.assertNotIn("response", r.json())

    def test_studio_forced_lane_skips_the_classifier(self):
        r = self.client.post("/admin/chat", json={
            "router_id": "support", "force_label": "refuse", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.json()["response"]["text"], "No.")
        self.assertEqual(r.json()["decision"]["reason"], "lane chosen by hand")

    def test_studio_requires_router_or_model(self):
        r = self.client.post("/admin/chat", json={"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.status_code, 400)

    def test_logs_endpoint_returns_recorded_requests(self):
        with self.provider_replies():
            self.client.post("/v1/chat/completions", json={
                "model": "fast", "messages": [{"role": "user", "content": "hi"}]})
        logs = self.client.get("/admin/logs", params={"source": "gateway"}).json()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["target_model"], "fast")
        self.assertEqual(len(self.client.get("/admin/logs/info").json()["files"]), 1)


if __name__ == "__main__":
    unittest.main()
