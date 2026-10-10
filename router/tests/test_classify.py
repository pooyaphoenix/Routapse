import unittest
from unittest.mock import AsyncMock, patch

from app import main
from app.main import Candidate, ClassifyIn, SignalIn


def request(**changes):
    data = {
        "prompt": "I was charged twice for my invoice",
        "candidates": [
            Candidate(label="billing", description="invoices payments refunds"),
            Candidate(label="technical", description="bugs outages errors"),
        ],
    }
    data.update(changes)
    return ClassifyIn(**data)


class ParseLabelTests(unittest.TestCase):
    def test_reads_json_label_case_insensitively(self):
        self.assertEqual(main.parse_label('{"label": "Billing", "confidence": 0.8}', ["billing"]), ("billing", 0.8))

    def test_falls_back_to_single_label_mention(self):
        self.assertEqual(main.parse_label("I think billing fits", ["billing", "technical"]), ("billing", 0.4))

    def test_ambiguous_text_returns_no_label(self):
        self.assertEqual(main.parse_label("billing or technical", ["billing", "technical"]), (None, 0.0))


class ExtractTests(unittest.TestCase):
    def test_reads_choice_confidence_and_signals(self):
        req = request(signals=[SignalIn(name="urgency", type="score", instructions="How urgent?")])
        result = {
            "answers": {"_route": {"choice": "billing", "probability": 0.9}, "urgency": {"score": 0.4}},
            "routing": {"model": "laya-large"},
        }
        self.assertEqual(
            main.extract(result, req),
            {"label": "billing", "confidence": 0.9, "signals": {"urgency": 0.4}, "laya_model": "laya-large"},
        )

    def test_reads_confidence_from_probability_map(self):
        result = {"answers": {"_route": {"choice": "billing", "probabilities": {"billing": 0.7}}}}
        self.assertEqual(main.extract(result, request())["confidence"], 0.7)

    def test_unexpected_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            main.extract({"oops": True}, request())


class HeuristicTests(unittest.TestCase):
    def test_picks_label_with_most_overlap(self):
        self.assertEqual(main.heuristic(request())[0], "billing")

    def test_confidence_is_capped(self):
        self.assertLessEqual(main.heuristic(request())[1], 0.7)


class ClassifyEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_lane_skips_the_model(self):
        req = request(candidates=[Candidate(label="only")])
        result = await main.classify(req)
        self.assertEqual((result["label"], result["confidence"], result["source"]), ("only", 1.0, "single lane"))

    async def test_model_choice_is_returned(self):
        answer = {"label": "technical", "confidence": 0.95, "signals": {}, "laya_model": None}
        with patch.object(main, "classify_laya", AsyncMock(return_value=answer)):
            result = await main.classify(request())
        self.assertEqual((result["label"], result["confidence"], result["source"]), ("technical", 0.95, "laya"))

    async def test_missing_laya_package_uses_heuristic(self):
        with patch.object(main, "classify_laya", AsyncMock(side_effect=ImportError)):
            result = await main.classify(request())
        self.assertEqual(result["label"], "billing")
        self.assertIn("laya package not installed", result["source"])

    async def test_model_failure_uses_heuristic(self):
        with patch.object(main, "classify_jev", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await main.classify(request(model="jev"))
        self.assertEqual(result["label"], "billing")
        self.assertIn("jev failed", result["source"])

    async def test_unknown_label_uses_heuristic(self):
        answer = {"label": "nope", "confidence": 1.0, "signals": {}, "laya_model": None}
        with patch.object(main, "classify_laya", AsyncMock(return_value=answer)):
            result = await main.classify(request())
        self.assertEqual(result["label"], "billing")
        self.assertIn("unknown label", result["source"])

    async def test_missing_confidence_defaults_to_one(self):
        answer = {"label": "billing", "confidence": None, "signals": {}, "laya_model": None}
        with patch.object(main, "classify_laya", AsyncMock(return_value=answer)):
            result = await main.classify(request())
        self.assertEqual(result["confidence"], 1.0)
        self.assertIn("no confidence reported", result["source"])


if __name__ == "__main__":
    unittest.main()
