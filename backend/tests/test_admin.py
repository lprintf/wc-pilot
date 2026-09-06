from __future__ import annotations

from types import SimpleNamespace
import base64
import tempfile
import unittest
from pathlib import Path

import httpx

from wechat_bot.app import create_app
from wechat_bot.store import MessageStore


class AdminEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = MessageStore(Path(self.tempdir.name) / "test.db")
        self.user_id = self.store.get_or_create_customer("wk-account", "external-secret", seen_at=100)
        self.store.update_customer_profile(user_id=self.user_id, external_userid="external-secret", nickname="张三", avatar_url="https://example.test/a.png", gender=1, unionid=None, scene="", scene_param="", fetched_at=100)
        self.store.mark_sent(msgid="in-1", user_id=self.user_id, open_kfid="wk-account", external_userid="external-secret", send_time=101, customer_content="你好", reply_content="你好，请问有什么可以帮您？")
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
