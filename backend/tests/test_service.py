from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from wechat_bot.auth import AuthManager
from wechat_bot.callback import CustomerServiceEvent
from wechat_bot.llm import LLMError
from wechat_bot.service import (
    CustomerServiceProcessor,
    FALLBACK_REPLY,
    LOGIN_LINK_RATE_LIMIT_REPLY,
    PROFILE_COMMANDS,
)
from wechat_bot.store import MessageStore
from wechat_bot.wecom import SyncResult, WeComCustomer


class FakeWeCom:
    def __init__(
        self, *, profile_fail: bool = False, send_fail: bool = False
    ) -> None:
        self.sync_results = [
            SyncResult([{"msgid": "historical"}], "cursor-1", 1),
            SyncResult(
                [
                    {
                        "msgid": "customer-1",
                        "origin": 3,
                        "external_userid": "customer",
                        "send_time": 123,
                        "msgtype": "text",
                        "text": {"content": "你好"},
                    }
                ],
                "cursor-2",
                1,
            ),
        ]
        self.sent: list[tuple[str, str, str]] = []
        self.customer_batches: list[list[str]] = []
        self.profile_fail = profile_fail
        self.send_fail = send_fail

    async def list_accounts(self) -> list[dict[str, Any]]:
        return [{"open_kfid": "wk-account"}]

    async def sync_messages(
        self,
        open_kfid: str,
        cursor: str = "",
        callback_token: str = "",
        *,
        max_pages: int = 20,
    ) -> SyncResult:
        return self.sync_results.pop(0)

    async def send_text(
        self, open_kfid: str, external_userid: str, content: str
    ) -> str:
        if self.send_fail:
            raise RuntimeError("send failed")
        self.sent.append((open_kfid, external_userid, content))
        return "reply-msgid"

    async def batch_get_customers(
        self, external_userids: list[str]
    ) -> list[WeComCustomer]:
        self.customer_batches.append(external_userids)
        if self.profile_fail:
            raise RuntimeError("profile API unavailable")
        return [
            WeComCustomer(
                external_userid=external_userid,
                nickname="测试用户",
                avatar_url="https://example.test/avatar.png",
                gender=1,
                unionid="union-id",
                scene="custom",
                scene_param="campaign",
            )
            for external_userid in external_userids
        ]


class FakeLLM:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.questions: list[str] = []

    async def answer(self, question: str, history: Any = ()) -> str:
        self.questions.append(question)
        if self.fail:
            raise LLMError("provider failed")
        return "你好，请问有什么可以帮你？"


class CustomerServiceProcessorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = MessageStore(Path(self.tempdir.name) / "test.db")

    async def asyncTearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    async def test_bootstrap_skips_history_then_replies_to_new_text(self) -> None:
        wecom = FakeWeCom()
        llm = FakeLLM()
        processor = CustomerServiceProcessor(wecom, llm, self.store)  # type: ignore[arg-type]

        await processor.bootstrap()
        self.assertEqual(self.store.get_cursor("wk-account"), "cursor-1")
        self.assertEqual(wecom.sent, [])

        await processor.handle_event(
            CustomerServiceEvent(
                "kf_msg_or_event", "temporary-token", "wk-account", 123
            )
        )

        self.assertEqual(llm.questions, ["你好"])
        self.assertEqual(
            wecom.sent,
            [("wk-account", "customer", "你好，请问有什么可以帮你？")],
        )
        self.assertEqual(self.store.get_cursor("wk-account"), "cursor-2")
        self.assertTrue(self.store.is_processed("customer-1"))
        self.assertEqual(wecom.customer_batches, [["customer"]])
        profile = self.store.get_customer_profile(1)
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.nickname, "测试用户")
        self.assertEqual(profile.scene_param, "campaign")

    async def test_llm_error_uses_fallback_reply(self) -> None:
        wecom = FakeWeCom()
        wecom.sync_results.pop(0)
        self.store.set_cursor("wk-account", "cursor-1")
        llm = FakeLLM(fail=True)
        processor = CustomerServiceProcessor(
            wecom, llm, self.store, "wk-account"  # type: ignore[arg-type]
        )
        await processor.bootstrap()

        await processor.handle_event(
            CustomerServiceEvent(
                "kf_msg_or_event", "temporary-token", "wk-account", 123
            )
        )

        self.assertEqual(wecom.sent[0][2], FALLBACK_REPLY)

    async def test_profile_failure_does_not_block_normal_reply(self) -> None:
        wecom = FakeWeCom(profile_fail=True)
        llm = FakeLLM()
        processor = CustomerServiceProcessor(wecom, llm, self.store)  # type: ignore[arg-type]

        await processor.bootstrap()
        await processor.handle_event(
            CustomerServiceEvent(
                "kf_msg_or_event", "temporary-token", "wk-account", 123
            )
        )

        self.assertEqual(llm.questions, ["你好"])
        self.assertEqual(len(wecom.sent), 1)
        self.assertTrue(self.store.is_processed("customer-1"))

    async def test_customer_profiles_are_chunked_in_batches_of_one_hundred(
        self,
    ) -> None:
        wecom = FakeWeCom()
        processor = CustomerServiceProcessor(  # type: ignore[arg-type]
            wecom, FakeLLM(), self.store, "wk-account"
        )
        messages = [
            {
                "origin": 3,
                "external_userid": f"customer-{index}",
                "send_time": 123 + index,
            }
            for index in range(205)
        ]

        customer_user_ids = await processor._prepare_customer_profiles(
            "wk-account", messages
        )

        self.assertEqual(len(customer_user_ids), 205)
        self.assertEqual(
            [len(batch) for batch in wecom.customer_batches], [100, 100, 5]
        )

    async def test_profile_command_sends_one_time_link_without_calling_llm(
        self,
    ) -> None:
        wecom = FakeWeCom()
        wecom.sync_results[1].messages[0]["text"] = {"content": "我的信息"}
        llm = FakeLLM()
        auth = AuthManager(self.store, "https://portal.example.test")
        processor = CustomerServiceProcessor(
            wecom, llm, self.store, auth=auth  # type: ignore[arg-type]
        )

        await processor.bootstrap()
        await processor.handle_event(
            CustomerServiceEvent(
                "kf_msg_or_event", "temporary-token", "wk-account", 123
            )
        )

        self.assertEqual(llm.questions, [])
        sent_reply = wecom.sent[0][2]
        self.assertIn("https://portal.example.test/auth/t/", sent_reply)
        self.assertNotIn("customer", sent_reply)
        history = self.store.history(1)
        self.assertNotIn("/auth/t/", history[-1].content)
        self.assertIn("链接已隐藏", history[-1].content)

    async def test_failed_link_delivery_cancels_the_ticket(self) -> None:
        wecom = FakeWeCom(send_fail=True)
        auth = AuthManager(self.store, "https://portal.example.test")
        processor = CustomerServiceProcessor(
            wecom, FakeLLM(), self.store, auth=auth  # type: ignore[arg-type]
        )
        user_id = self.store.get_or_create_customer(
            "wk-account", "customer", seen_at=123
        )
        message = {
            "msgid": "profile-command",
            "origin": 3,
            "external_userid": "customer",
            "send_time": 123,
            "msgtype": "text",
            "text": {"content": "我的信息"},
        }

        with self.assertRaisesRegex(RuntimeError, "send failed"):
            await processor._process_message(
                "wk-account", message, {"customer": user_id}
            )

        replacement = auth.issue_login_ticket(user_id)
        self.assertIn("/auth/t/", replacement.url)

    def test_profile_command_aliases_include_my_messages(self) -> None:
        self.assertIn("我的消息", PROFILE_COMMANDS)

    async def test_repeated_profile_command_has_generic_rate_limit_message(
        self,
    ) -> None:
        wecom = FakeWeCom()
        auth = AuthManager(self.store, "https://portal.example.test")
        processor = CustomerServiceProcessor(
            wecom, FakeLLM(), self.store, auth=auth  # type: ignore[arg-type]
        )
        user_id = self.store.get_or_create_customer(
            "wk-account", "customer", seen_at=123
        )
        first_message = {
            "msgid": "first-profile-command",
            "origin": 3,
            "external_userid": "customer",
            "send_time": 123,
            "msgtype": "text",
            "text": {"content": "我的信息"},
        }
        repeated_message = {
            **first_message,
            "msgid": "repeated-profile-command",
            "text": {"content": "查看记录"},
        }

        await processor._process_message(
            "wk-account", first_message, {"customer": user_id}
        )
        await processor._process_message(
            "wk-account", repeated_message, {"customer": user_id}
        )

        self.assertEqual(wecom.sent[-1][2], LOGIN_LINK_RATE_LIMIT_REPLY)
        self.assertNotIn("我的信息", LOGIN_LINK_RATE_LIMIT_REPLY)
