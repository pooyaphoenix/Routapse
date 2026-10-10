import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app import providers
from app.schemas import Provider

MESSAGES = [
    {"role": "system", "content": "Be brief."},
    {"role": "user", "content": "Hi"},
    {"role": "assistant", "content": "Hello"},
    {"role": "user", "content": "Bye"},
]


def reply(payload, status=200):
    return httpx.Response(status, json=payload, request=httpx.Request("POST", "http://provider"))


async def complete(provider, payload, params=None):
    post = AsyncMock(return_value=reply(payload))
    with patch.object(providers.client, "post", post):
        result = await providers.complete(provider, "model-x", MESSAGES, params or {})
    return result, post.call_args


class ProviderAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_compatible_request_and_usage(self):
        provider = Provider(id="p", kind="openai_compat", base_url="http://llm/v1/", api_key="secret")
        payload = {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 5}}
        (text, usage), call = await complete(provider, payload, {"temperature": 0.2, "unknown": 1, "top_p": None})
        self.assertEqual((text, usage), ("ok", {"total_tokens": 5}))
        self.assertEqual(call.args[0], "http://llm/v1/chat/completions")
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(call.kwargs["json"], {"model": "model-x", "messages": MESSAGES, "temperature": 0.2})

    async def test_ollama_uses_configured_default_url(self):
        provider = Provider(id="p", kind="ollama")
        payload = {"choices": [{"message": {"content": "ok"}}]}
        with patch.object(providers.settings, "ollama_url", "http://ollama:11434"):
            _, call = await complete(provider, payload)
        self.assertEqual(call.args[0], "http://ollama:11434/v1/chat/completions")

    async def test_anthropic_splits_system_prompt_and_maps_usage(self):
        provider = Provider(id="p", kind="anthropic", api_key="key")
        payload = {
            "content": [{"type": "text", "text": "Hi "}, {"type": "tool_use"}, {"type": "text", "text": "there"}],
            "usage": {"input_tokens": 3, "output_tokens": 4},
        }
        (text, usage), call = await complete(provider, payload, {"max_tokens": 50})
        body = call.kwargs["json"]
        self.assertEqual(text, "Hi there")
        self.assertEqual(usage, {"prompt_tokens": 3, "completion_tokens": 4})
        self.assertEqual(body["system"], "Be brief.")
        self.assertEqual(body["max_tokens"], 50)
        self.assertTrue(all(m["role"] != "system" for m in body["messages"]))
        self.assertEqual(call.kwargs["headers"]["x-api-key"], "key")

    async def test_anthropic_defaults_max_tokens(self):
        provider = Provider(id="p", kind="anthropic")
        payload = {"content": [{"type": "text", "text": "ok"}]}
        _, call = await complete(provider, payload)
        self.assertEqual(call.kwargs["json"]["max_tokens"], 1024)

    async def test_gemini_maps_roles_and_generation_config(self):
        provider = Provider(id="p", kind="gemini", api_key="key")
        payload = {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
        }
        (text, usage), call = await complete(provider, payload, {"top_p": 0.9, "max_tokens": 20})
        body = call.kwargs["json"]
        self.assertEqual((text, usage), ("ok", {"prompt_tokens": 2, "completion_tokens": 1}))
        self.assertEqual([c["role"] for c in body["contents"]], ["user", "model", "user"])
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": "Be brief."}]})
        self.assertEqual(body["generationConfig"], {"topP": 0.9, "maxOutputTokens": 20})
        self.assertEqual(call.kwargs["params"], {"key": "key"})
        self.assertTrue(call.args[0].endswith("/v1beta/models/model-x:generateContent"))

    async def test_http_errors_propagate(self):
        provider = Provider(id="p", kind="openai", retries=0)
        post = AsyncMock(return_value=reply({"error": "bad"}, status=429))
        with patch.object(providers.client, "post", post), self.assertRaises(httpx.HTTPStatusError):
            await providers.complete(provider, "model-x", MESSAGES, {})


async def stream(provider, events, params=None):
    """Run providers.stream against a fake SSE server; returns (texts, usage, the request sent)."""
    seen = []

    def handler(request):
        seen.append(request)
        body = "".join(f"event: x\ndata: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events)
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    usage = {}
    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch.object(providers, "client", mock):
        texts = [t async for t in providers.stream(provider, "model-x", MESSAGES, params or {}, usage)]
    return texts, usage, seen[0]


class ProviderStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_compatible_stream(self):
        provider = Provider(id="p", kind="openai_compat", base_url="http://llm/v1", api_key="secret")
        texts, usage, request = await stream(provider, [
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"total_tokens": 7}},
            "[DONE]",
        ], {"temperature": 0.1})
        body = json.loads(request.content)
        self.assertEqual((texts, usage), (["Hel", "lo"], {"total_tokens": 7}))
        self.assertEqual(str(request.url), "http://llm/v1/chat/completions")
        self.assertEqual(request.headers["authorization"], "Bearer secret")
        self.assertEqual(body["stream"], True)
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertEqual(body["temperature"], 0.1)

    async def test_anthropic_stream(self):
        provider = Provider(id="p", kind="anthropic", api_key="key")
        texts, usage, request = await stream(provider, [
            {"type": "message_start", "message": {"usage": {"input_tokens": 9, "output_tokens": 1}}},
            {"type": "content_block_start", "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi "}},
            {"type": "ping"},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "there"}},
            {"type": "message_delta", "usage": {"output_tokens": 4}},
            {"type": "message_stop"},
        ])
        body = json.loads(request.content)
        self.assertEqual(texts, ["Hi ", "there"])
        self.assertEqual(usage, {"prompt_tokens": 9, "completion_tokens": 4})
        self.assertTrue(body["stream"])
        self.assertEqual(body["system"], "Be brief.")
        self.assertEqual(request.headers["x-api-key"], "key")

    async def test_gemini_stream(self):
        provider = Provider(id="p", kind="gemini", api_key="key")
        texts, usage, request = await stream(provider, [
            {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "lo"}]}}],
             "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3}},
        ])
        self.assertEqual(texts, ["Hel", "lo"])
        self.assertEqual(usage, {"prompt_tokens": 2, "completion_tokens": 3})
        self.assertEqual(request.url.path, "/v1beta/models/model-x:streamGenerateContent")
        self.assertEqual(dict(request.url.params), {"key": "key", "alt": "sse"})

    async def test_error_status_raises_with_body(self):
        provider = Provider(id="p", kind="openai", retries=0)
        mock = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429, text="slow down")))
        with patch.object(providers, "client", mock), self.assertRaises(httpx.HTTPStatusError) as error:
            [t async for t in providers.stream(provider, "model-x", MESSAGES, {}, {})]
        self.assertEqual(error.exception.response.text, "slow down")


class RetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sleep = patch.object(providers.asyncio, "sleep", AsyncMock())
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def serve(self, *replies):
        """A client whose successive requests get the given replies (an exception is raised instead)."""
        queue, calls = list(replies), []

        def handler(request):
            calls.append(request)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls

    async def run_complete(self, provider, *replies):
        mock, calls = self.serve(*replies)
        with patch.object(providers, "client", mock):
            return await providers.complete(provider, "m", MESSAGES, {}), calls

    OK = httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async def test_transient_status_is_retried_until_success(self):
        provider = Provider(id="p", kind="openai", retries=2)
        (text, _), calls = await self.run_complete(provider, httpx.Response(503), httpx.Response(429), self.OK)
        self.assertEqual((text, len(calls)), ("ok", 3))
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [0.5, 1.0])

    async def test_connection_errors_and_timeouts_are_retried(self):
        provider = Provider(id="p", kind="openai", retries=2)
        (text, _), calls = await self.run_complete(
            provider, httpx.ConnectError("down"), httpx.ReadTimeout("slow"), self.OK)
        self.assertEqual((text, len(calls)), ("ok", 3))

    async def test_client_errors_are_not_retried(self):
        provider = Provider(id="p", kind="openai", retries=3)
        mock, calls = self.serve(httpx.Response(401, text="bad key"))
        with patch.object(providers, "client", mock), self.assertRaises(httpx.HTTPStatusError):
            await providers.complete(provider, "m", MESSAGES, {})
        self.assertEqual(len(calls), 1)
        self.sleep.assert_not_called()

    async def test_gives_up_after_configured_retries(self):
        provider = Provider(id="p", kind="openai", retries=1)
        mock, calls = self.serve(httpx.Response(500), httpx.Response(500))
        with patch.object(providers, "client", mock), self.assertRaises(httpx.HTTPStatusError):
            await providers.complete(provider, "m", MESSAGES, {})
        self.assertEqual(len(calls), 2)

    async def test_zero_retries_means_single_attempt(self):
        provider = Provider(id="p", kind="openai", retries=0)
        mock, calls = self.serve(httpx.Response(503))
        with patch.object(providers, "client", mock), self.assertRaises(httpx.HTTPStatusError):
            await providers.complete(provider, "m", MESSAGES, {})
        self.assertEqual(len(calls), 1)

    async def test_retry_after_header_extends_backoff_up_to_cap(self):
        provider = Provider(id="p", kind="openai", retries=2)
        await self.run_complete(
            provider, httpx.Response(429, headers={"retry-after": "3"}),
            httpx.Response(429, headers={"retry-after": "120"}), self.OK)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [3.0, providers.MAX_BACKOFF])

    async def test_provider_timeout_is_applied_per_request(self):
        provider = Provider(id="p", kind="openai", timeout=7)
        post = AsyncMock(return_value=reply({"choices": [{"message": {"content": "ok"}}]}))
        with patch.object(providers.client, "post", post):
            await providers.complete(provider, "m", MESSAGES, {})
        self.assertEqual(post.call_args.kwargs["timeout"], 7)

    async def test_stream_retries_before_first_token(self):
        provider = Provider(id="p", kind="openai", retries=1)
        body = 'data: {"choices": [{"delta": {"content": "hi"}}]}\n\ndata: [DONE]\n\n'
        mock, calls = self.serve(httpx.Response(502, text="bad gateway"), httpx.Response(200, content=body.encode()))
        with patch.object(providers, "client", mock):
            texts = [t async for t in providers.stream(provider, "m", MESSAGES, {}, {})]
        self.assertEqual((texts, len(calls)), (["hi"], 2))

    async def test_stream_never_retries_after_text_was_sent(self):
        provider = Provider(id="p", kind="openai", retries=3)

        async def body():
            yield b'data: {"choices": [{"delta": {"content": "par"}}]}\n\n'
            raise httpx.ReadError("lost")

        mock, calls = self.serve(httpx.Response(200, content=body()))
        got = []
        with patch.object(providers, "client", mock), self.assertRaises(httpx.ReadError):
            async for t in providers.stream(provider, "m", MESSAGES, {}, {}):
                got.append(t)
        self.assertEqual((got, len(calls)), (["par"], 1))


if __name__ == "__main__":
    unittest.main()
