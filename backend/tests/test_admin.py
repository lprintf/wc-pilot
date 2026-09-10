from __future__ import annotations

from types import SimpleNamespace
import base64
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import httpx

from wechat_bot.app import create_app
from wechat_bot.store import MessageStore
from wechat_bot.wecom import WeComAPIError


class AdminEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        clock = patch("wechat_bot.replies.time.time", return_value=123)
        clock.start()
        self.addCleanup(clock.stop)
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = MessageStore(Path(self.tempdir.name) / "test.db")
        self.user_id = self.store.get_or_create_customer("wk-account", "external-secret", seen_at=100)
        self.store.update_customer_profile(user_id=self.user_id, external_userid="external-secret", nickname="张三", avatar_url="https://example.test/a.png", gender=1, unionid=None, scene="", scene_param="", fetched_at=100)
        self.store.mark_sent(msgid="in-1", user_id=self.user_id, open_kfid="wk-account", external_userid="external-secret", send_time=101, customer_content="你好", reply_content="你好，请问有什么可以帮您？")
        self.store.observe_customer_message("wk-account", "external-secret", "in-1", 101)
        self.store.consume_reply("wk-account", "external-secret")
        self.sent: list[tuple[str, str, str]] = []
        self.wecom = SimpleNamespace(send_text=self.send_text)
        self.processor = SimpleNamespace(managed_open_kfid="wk-account")
        self.settings = SimpleNamespace(admin_configured=True, admin_username="agent", admin_password="pass")
        self.app = create_app()
        self.app.state.runtime = SimpleNamespace(store=self.store, processor=self.processor, settings=self.settings, wecom=self.wecom)

    async def asyncTearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    async def send_text(self, open_kfid: str, external_userid: str, content: str) -> str:
        self.sent.append((open_kfid, external_userid, content))
        return "wecom-msg-1"

    def headers(self) -> dict[str, str]:
        token = base64.b64encode(b"agent:pass").decode()
        return {"authorization": f"Basic {token}"}

    async def test_auth_and_user_data_scope(self) -> None:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
            denied = await client.get("/api/admin/users")
            response = await client.get("/api/admin/users", headers=self.headers())
            detail = await client.get(f"/api/admin/users/{self.user_id}/conversation", headers=self.headers())
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json()["error"]["code"], "authentication_required")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("external-secret", response.text)
        self.assertEqual(response.json()["users"][0]["id"], self.user_id)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual([m["sender_type"] for m in detail.json()["messages"]], ["user", "ai"])

    async def test_send_is_idempotent_and_failure_can_retry(self) -> None:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
            payload = {"content": "人工回复", "request_id": "req-1"}
            write_headers = {**self.headers(), "x-requested-with": "XMLHttpRequest"}
            first = await client.post(f"/api/admin/users/{self.user_id}/messages", headers=write_headers, json=payload)
            duplicate = await client.post(f"/api/admin/users/{self.user_id}/messages", headers=write_headers, json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(first.json()["message"]["send_status"], "sent")
        self.assertIn("客服剩余回复次数3", self.sent[0][2])
        self.assertEqual(self.store.reply_budget("wk-account", "external-secret").used, 2)

    async def test_limit_errors_return_actionable_conflict(self) -> None:
        async def limited(*_args: str) -> str:
            raise WeComAPIError("kf/send_msg", 95001, "limited")
        self.wecom.send_text = limited
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
            with self.assertLogs("wechat_bot.app", level="WARNING") as logs:
                response = await client.post(f"/api/admin/users/{self.user_id}/messages",
                    headers={**self.headers(), "x-requested-with": "XMLHttpRequest"},
                    json={"content": "人工回复", "request_id": "limit"})
        self.assertIn("code=reply_limit_reached source=wecom errcode=95001", logs.output[0])
        self.assertIn(f"message_id={response.json()['message']['id']}", logs.output[0])
        self.assertNotIn("external-secret", logs.output[0])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "reply_limit_reached")
        self.assertFalse(response.json()["error"]["retryable"])
        self.assertEqual(response.json()["message"]["error_message"], "reply_limit_reached")

    async def test_failed_delivery_can_retry_after_new_customer_message(self) -> None:
        transport = httpx.ASGITransport(app=self.app)
        headers = {**self.headers(), "x-requested-with": "XMLHttpRequest"}
        async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
            with patch("wechat_bot.replies.time.time", return_value=101 + 48 * 3600), self.assertLogs("wechat_bot.app", level="WARNING") as logs:
                failed = await client.post(f"/api/admin/users/{self.user_id}/messages", headers=headers,
                    json={"content": "人工回复", "request_id": "expired"})
            self.assertEqual(failed.status_code, 409)
            self.assertIn("code=reply_window_expired source=local errcode=None", logs.output[0])
            self.assertIn(f"message_id={failed.json()['message']['id']}", logs.output[0])
            self.assertEqual(failed.json()["error"]["code"], "reply_window_expired")
            self.assertEqual(self.sent, [])
            self.store.observe_customer_message("wk-account", "external-secret", "in-2", 123)
            retry = await client.post(f"/api/admin/users/{self.user_id}/messages", headers=headers,
                json={"retry_message_id": failed.json()["message"]["id"], "request_id": "retry"})
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("客服剩余回复次数4", self.sent[0][2])
