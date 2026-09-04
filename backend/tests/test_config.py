from __future__ import annotations

import unittest

from wechat_bot.config import Settings


def complete_settings() -> dict[str, str]:
    return {
        "CorpID": "corp",
        "APP_AGENT_ID": "1000002",
        "APP_AGENT_SECRET": "secret",
        "PUBLIC_BASE_URL": "https://customer.example.test/",
        "LLM_API_KEY": "llm-key",
        "LLM_BASE_URL": "https://llm.example.test/v1",
        "LLM_MODEL": "model",
    }


class SettingsTests(unittest.TestCase):
    def test_loads_https_public_base_url(self) -> None:
        settings = Settings.from_mapping(complete_settings())
        self.assertEqual(
            settings.public_base_url, "https://customer.example.test"
        )

    def test_rejects_non_https_public_base_url(self) -> None:
        values = complete_settings()
        values["PUBLIC_BASE_URL"] = "http://customer.example.test"
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            Settings.from_mapping(values)
