from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from wechat_bot.app import Runtime, create_app
from wechat_bot.config import Settings
from wechat_bot.wecom import SyncResult, WeComAPIError


class RuntimeInitializationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        settings = Settings.from_mapping({
            "CorpID": "corp",
            "APP_AGENT_ID": "1000002",
            "APP_AGENT_SECRET": "secret",
            "PUBLIC_BASE_URL": "https://customer.example.test",
            "DATABASE_PATH": str(Path(self.tempdir.name) / "test.db"),
            "WECHAT_KF_CALLBACK_TOKEN": "callback-token",
            "WECHAT_KF_ENCODING_AES_KEY": "a" * 43,
            "LLM_API_KEY": "llm-key",
            "LLM_BASE_URL": "https://llm.example.test/v1",
            "LLM_MODEL": "model",
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "password",
        })
        self.wecom = AsyncMock()
        self.wecom.list_accounts.return_value = [{"open_kfid": "wk-account"}]
        self.wecom.sync_messages.return_value = SyncResult([], "baseline", 1)
        self.llm = AsyncMock()
        with (
            patch("wechat_bot.app.WeComClient", return_value=self.wecom),
            patch("wechat_bot.app.OpenAICompatibleLLM", return_value=self.llm),
        ):
            self.runtime = Runtime(settings)
        retry_delay = patch("wechat_bot.app.INITIALIZATION_RETRY_SECONDS", 0.01)
        retry_delay.start()
        self.addCleanup(retry_delay.stop)
        self.app = create_app()
        self.app.state.runtime = self.runtime
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://customer.example.test",
            auth=("admin", "password"),
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.runtime.close()
        self.tempdir.cleanup()

    async def wait_for_recovery(self) -> None:
        self.assertIsNotNone(self.runtime._initialization_task)
        await asyncio.wait_for(self.runtime._initialization_task, timeout=2)

    async def test_trusted_ip_failure_recovers_admin_without_restart(self) -> None:
        self.wecom.list_accounts.side_effect = [
            WeComAPIError("kf/account/list", 60020, "sensitive upstream details"),
            WeComAPIError("kf/account/list", 60020, "sensitive upstream details"),
            [{"open_kfid": "wk-account"}],
        ]
        # A persisted cursor must survive retries and must not be rebaselined.
        self.runtime.store.set_cursor("wk-account", "persisted-cursor")
        await self.runtime.start()
        self.assertFalse(self.runtime.ready)
        unavailable = await self.client.get("/api/admin/users")
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.json()["error"]["code"], "service_not_ready")
        health = await self.client.get("/health/ready")
        self.assertEqual(health.status_code, 503)
        self.assertIn("errcode=60020", health.text)
        self.assertNotIn("sensitive upstream details", health.text)

        await self.wait_for_recovery()

        self.assertTrue(self.runtime.ready)
        self.assertEqual(self.runtime.errors, [])
        self.assertEqual((await self.client.get("/health/ready")).status_code, 200)
        self.assertEqual((await self.client.get("/api/admin/users")).status_code, 200)
        self.assertEqual(self.runtime.store.get_cursor("wk-account"), "persisted-cursor")
        self.wecom.sync_messages.assert_not_awaited()
        self.wecom.send_text.assert_not_awaited()

    async def test_failed_baseline_does_not_publish_account_until_retry_succeeds(self) -> None:
        self.wecom.sync_messages.side_effect = [
            WeComAPIError("kf/sync_msg", -1, "temporary failure"),
            SyncResult([{"msgid": "historical"}], "baseline", 1),
        ]
        await self.runtime.start()
        self.assertFalse(self.runtime.ready)
        self.assertEqual(self.runtime.processor.managed_open_kfid, "")
        self.assertIsNone(self.runtime.store.get_cursor("wk-account"))

        await self.wait_for_recovery()

        self.assertTrue(self.runtime.ready)
        self.assertEqual(self.runtime.processor.managed_open_kfid, "wk-account")
        self.assertEqual(self.runtime.store.get_cursor("wk-account"), "baseline")
        self.wecom.send_text.assert_not_awaited()
        self.llm.answer.assert_not_awaited()

    async def test_recovery_preserves_callback_configuration_errors(self) -> None:
        self.runtime._config_errors = ["WECHAT_KF_CALLBACK_TOKEN"]
        self.wecom.list_accounts.side_effect = [
            RuntimeError("temporary failure"),
            [{"open_kfid": "wk-account"}],
        ]
        await self.runtime.start()
        await self.wait_for_recovery()
        self.assertFalse(self.runtime.ready)
        self.assertEqual(self.runtime.errors, ["WECHAT_KF_CALLBACK_TOKEN"])

    async def test_shutdown_cancels_in_progress_retry_before_closing_clients(self) -> None:
        retry_started = asyncio.Event()
        retry_cancelled = asyncio.Event()
        attempts = 0

        async def list_accounts():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary failure")
            retry_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.wecom.aclose.assert_not_awaited()
                retry_cancelled.set()
                raise

        self.wecom.list_accounts.side_effect = list_accounts
        await self.runtime.start()
        await asyncio.wait_for(retry_started.wait(), timeout=2)
        await self.runtime.close()
        self.assertTrue(retry_cancelled.is_set())
        self.assertTrue(self.runtime._initialization_task.cancelled())
        self.wecom.aclose.assert_awaited_once()

    async def test_successful_start_does_not_schedule_retries(self) -> None:
        await self.runtime.start()
        self.assertTrue(self.runtime.ready)
        self.assertIsNone(self.runtime._initialization_task)
        self.wecom.list_accounts.assert_awaited_once()
