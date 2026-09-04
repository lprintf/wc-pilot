from __future__ import annotations

from types import SimpleNamespace
import tempfile
import time
import unittest
from pathlib import Path

import httpx

from wechat_bot.app import create_app
from wechat_bot.auth import AuthManager, SESSION_COOKIE_NAME
from wechat_bot.store import MessageStore


class AuthenticationEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = MessageStore(Path(self.tempdir.name) / "test.db")
        self.user_id = self.store.get_or_create_customer(
            "wk-account", "external-user", seen_at=100
        )
        self.store.update_customer_profile(
            user_id=self.user_id,
            external_userid="external-user",
            nickname="测试用户",
            avatar_url="https://example.test/avatar.png",
            gender=1,
            unionid="union-id",
            scene="scene",
            scene_param="secret-campaign",
            fetched_at=100,
        )
        self.store.mark_sent(
            msgid="message-1",
            user_id=self.user_id,
            open_kfid="wk-account",
            external_userid="external-user",
            send_time=1_788_000_000,
            customer_content="请介绍一下你们的服务",
            reply_content="我们提供 AI 客服能力。",
        )
        self.store.mark_sent(
            msgid="message-2",
            user_id=self.user_id,
            open_kfid="wk-account",
            external_userid="external-user",
            send_time=1_788_000_060,
            customer_content="如何查看历史记录？",
            reply_content="发送“查看记录”即可。",
        )
        self.other_user_id = self.store.get_or_create_customer(
            "wk-account", "other-external-user", seen_at=200
        )
        self.store.mark_sent(
            msgid="other-message",
            user_id=self.other_user_id,
            open_kfid="wk-account",
            external_userid="other-external-user",
            send_time=1_788_000_120,
            customer_content="其他用户的私密问题",
            reply_content="其他用户的私密回答",
        )
        self.auth = AuthManager(self.store, "https://customer.example.test")
        self.app = create_app()
        self.app.state.runtime = SimpleNamespace(auth=self.auth, store=self.store)

    async def asyncTearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    async def test_ticket_sets_secure_cookie_and_api_me_hides_identifiers(self) -> None:
        ticket = self.auth.issue_login_ticket(self.user_id)
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://customer.example.test",
            follow_redirects=False,
        ) as client:
            login = await client.get(f"/auth/t/{ticket.token}")
            self.assertEqual(login.status_code, 303)
            self.assertEqual(login.headers["location"], "/me")
            self.assertEqual(login.headers["cache-control"], "no-store")
            self.assertEqual(login.headers["referrer-policy"], "no-referrer")
            cookie = login.headers["set-cookie"]
            self.assertIn(f"{SESSION_COOKIE_NAME}=", cookie)
            self.assertIn("HttpOnly", cookie)
            self.assertIn("Secure", cookie)
            self.assertIn("SameSite=lax", cookie)

            profile = await client.get("/api/me")
            self.assertEqual(profile.status_code, 200)
            self.assertEqual(profile.json()["profile"]["nickname"], "测试用户")
            self.assertNotIn("external-user", profile.text)
            self.assertNotIn("union-id", profile.text)
            self.assertNotIn("secret-campaign", profile.text)

            page = await client.get("/me")
            self.assertEqual(page.status_code, 200)
            self.assertIn("我的客服记录", page.text)
            self.assertIn("请介绍一下你们的服务", page.text)
            self.assertIn("我们提供 AI 客服能力。", page.text)
            self.assertIn("UTC+8", page.text)
            self.assertIn("首次咨询", page.text)
            self.assertIn("最近咨询", page.text)
            self.assertIn("发送时间", page.text)
            self.assertIn("回复时间", page.text)
            self.assertNotIn("external-user", page.text)
            self.assertNotIn("其他用户的私密问题", page.text)
            self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])

            conversations = await client.get("/api/me/conversations")
            self.assertEqual(conversations.status_code, 200)
            conversation_list = conversations.json()["conversations"]
            self.assertEqual(len(conversation_list), 1)
            self.assertEqual(conversation_list[0]["message_count"], 4)
            conversation_id = conversation_list[0]["id"]

            detail = await client.get(
                f"/api/me/conversations/{conversation_id}"
            )
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(len(detail.json()["messages"]), 4)
            self.assertIn("T", detail.json()["messages"][0]["occurred_at"])

            other_conversation_id = self.store.list_conversations(
                self.other_user_id
            )[0].id
            forbidden = await client.get(
                f"/api/me/conversations/{other_conversation_id}"
            )
            self.assertEqual(forbidden.status_code, 404)

            previous_session = client.cookies.get(SESSION_COOKIE_NAME)
            replacement_ticket = self.auth.issue_login_ticket(
                self.user_id, now=int(time.time()) + 60
            )
            replacement_login = await client.get(
                f"/auth/t/{replacement_ticket.token}"
            )
            self.assertEqual(replacement_login.status_code, 303)
            assert previous_session is not None
            self.assertIsNone(
                self.auth.authenticate_session(previous_session)
            )

            replay = await client.get(f"/auth/t/{ticket.token}")
            self.assertEqual(replay.status_code, 400)
            still_authenticated = await client.get("/api/me")
            self.assertEqual(still_authenticated.status_code, 200)

    async def test_api_me_requires_session_cookie(self) -> None:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://customer.example.test"
        ) as client:
            response = await client.get("/api/me")
            page = await client.get("/me")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(page.status_code, 401)
        self.assertIn("登录已失效", page.text)
