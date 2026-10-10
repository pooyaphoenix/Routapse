import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api_admin import routers_put
from app.schemas import Route, RouterDef, Rule, Signal


class RouterValidationTests(unittest.TestCase):
    def router(self, **changes):
        data = {
            "id": "support",
            "name": "Support",
            "routes": [Route(label="answer", model_id="fast-model")],
        }
        data.update(changes)
        return RouterDef(**data)

    def assert_invalid(self, router, message):
        with patch("app.api_admin.store.get", return_value={"id": "fast-model"}), self.assertRaises(HTTPException) as error:
            routers_put(router.id, router)
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(error.exception.detail, message)

    def test_empty_router_is_rejected(self):
        self.assert_invalid(self.router(routes=[]), "router must have at least one route")

    def test_unknown_fallback_is_rejected(self):
        self.assert_invalid(self.router(fallback_label="missing"), "fallback points at unknown lane 'missing'")

    def test_forward_route_requires_a_model(self):
        self.assert_invalid(
            self.router(routes=[Route(label="answer")]),
            "route 'answer' must have a target model",
        )

    def test_jev_does_not_accept_laya_signals(self):
        self.assert_invalid(
            self.router(
                router_model="jev",
                signals=[Signal(name="urgency", type="score", instructions="Score urgency")],
            ),
            "signals and rules require the Laya router model",
        )

    def test_valid_laya_router_is_saved(self):
        router = self.router(
            signals=[Signal(name="urgency", type="score", instructions="Score urgency")],
            rules=[Rule(signal="urgency", op="gte", value="0.8", route_label="answer")],
        )
        with patch("app.api_admin.store.get", return_value={"id": "fast-model"}), patch(
            "app.api_admin.store.put"
        ) as put:
            self.assertEqual(routers_put(router.id, router), router)
        put.assert_called_once_with("routers", "support", router.model_dump())


if __name__ == "__main__":
    unittest.main()
