import unittest
from unittest.mock import patch

import httpx

from app import engine
from app.schemas import Route, RouterDef, Rule, Signal


class FakeRouterClient:
    def __init__(self, response):
        self.response = response
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, url, json):
        self.request = {"url": url, "json": json}
        return self.response


def response(status, **kwargs):
    return httpx.Response(status, request=httpx.Request("POST", "http://router/classify"), **kwargs)


class RoutingDecisionTests(unittest.IsolatedAsyncioTestCase):
    def router(self, **changes):
        data = {
            "id": "support",
            "name": "Support",
            "routes": [
                Route(label="standard", model_id="fast-model"),
                Route(label="human", action="respond", response_text="Escalated"),
            ],
            "fallback_label": "standard",
            "min_confidence": 0.7,
        }
        data.update(changes)
        return RouterDef(**data)

    async def decide(self, router, result, messages=None):
        client = FakeRouterClient(response(200, json=result))
        with patch("app.engine.httpx.AsyncClient", return_value=client):
            decision = await engine.decide(router, messages or [{"role": "user", "content": "Help"}])
        return decision, client.request

    async def test_rule_overrides_model_route(self):
        router = self.router(
            signals=[Signal(name="churn", type="noul", instructions="Is the user leaving?")],
            rules=[Rule(signal="churn", op="gte", value="0.7", route_label="human")],
        )
        decision, _ = await self.decide(
            router,
            {"label": "standard", "confidence": 0.99, "signals": {"churn": 0.9}, "source": "laya"},
        )
        self.assertEqual(decision["label"], "human")
        self.assertEqual(decision["reason"], "rule: churn gte 0.7 -> human")

    async def test_low_confidence_uses_fallback(self):
        decision, _ = await self.decide(
            self.router(),
            {"label": "human", "confidence": 0.5, "signals": {}, "source": "jev"},
        )
        self.assertEqual(decision["label"], "standard")
        self.assertIn("fallback -> standard", decision["reason"])

    async def test_unknown_label_uses_fallback(self):
        decision, _ = await self.decide(
            self.router(),
            {"label": "unknown", "confidence": 1.0, "signals": {}, "source": "jev"},
        )
        self.assertEqual(decision["label"], "standard")

    async def test_recent_context_is_sent_to_router(self):
        _, request = await self.decide(
            self.router(),
            {"label": "standard", "confidence": 0.9, "signals": {}, "source": "jev"},
            [
                {"role": "user", "content": "My invoice is wrong."},
                {"role": "assistant", "content": "What is the invoice number?"},
                {"role": "user", "content": "It is INV-42."},
            ],
        )
        self.assertIn("earlier context: My invoice is wrong.", request["json"]["prompt"])
        self.assertTrue(request["url"].endswith("/classify"))

    async def test_router_failure_uses_fallback(self):
        client = FakeRouterClient(response(503))
        with patch("app.engine.httpx.AsyncClient", return_value=client):
            decision = await engine.decide(self.router(), [{"role": "user", "content": "Help"}])
        self.assertEqual(decision["label"], "standard")
        self.assertIn("router unreachable", decision["reason"])


if __name__ == "__main__":
    unittest.main()
