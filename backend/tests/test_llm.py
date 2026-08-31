from __future__ import annotations

import json
import unittest

import httpx

from wechat_bot.llm import ChatMessage, LLMConfig, LLMError, OpenAICompatibleLLM


class LLMConfigTests(unittest.TestCase):
    def test_requires_all_environment_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "LLM_MODEL"):
            LLMConfig.from_mapping(
                {"LLM_API_KEY": "key", "LLM_BASE_URL": "https://llm.example/v1"}
            )

    def test_builds_chat_completions_url(self) -> None:
        config = LLMConfig("key", "https://llm.example/v1/", "model")
        self.assertEqual(
            config.chat_completions_url,
            "https://llm.example/v1/chat/completions",
        )


class OpenAICompatibleLLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_messages_and_returns_assistant_text(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["Authorization"], "Bearer secret")
            body = json.loads(request.content)
            self.assertFalse(body["stream"])
            self.assertEqual(body["model"], "test-model")
            self.assertEqual(body["messages"][-1], {"role": "user", "content": "你好"})
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "  你好，请问有什么可以帮你？  "}}]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            llm = OpenAICompatibleLLM(
                LLMConfig("secret", "https://llm.example/v1", "test-model"),
                http_client=http,
            )
            result = await llm.answer(
                " 你好 ", [ChatMessage("assistant", "欢迎咨询")]
            )

        self.assertEqual(result, "你好，请问有什么可以帮你？")

    async def test_rejects_malformed_response(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            llm = OpenAICompatibleLLM(
                LLMConfig("secret", "https://llm.example/v1", "test-model"),
                http_client=http,
            )
            with self.assertRaisesRegex(LLMError, "no assistant message"):
                await llm.answer("你好")

    async def test_hides_provider_error_body(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="provider details")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            llm = OpenAICompatibleLLM(
                LLMConfig("secret", "https://llm.example/v1", "test-model"),
                http_client=http,
            )
            with self.assertRaisesRegex(LLMError, "HTTP 401") as raised:
                await llm.answer("你好")

        self.assertNotIn("provider details", str(raised.exception))

    async def test_limits_reply_by_utf8_bytes(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "你" * 10}}]}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            llm = OpenAICompatibleLLM(
                LLMConfig(
                    "secret",
                    "https://llm.example/v1",
                    "test-model",
                    max_reply_bytes=10,
                ),
                http_client=http,
            )
            result = await llm.answer("你好")

        self.assertEqual(result, "你" * 3)
