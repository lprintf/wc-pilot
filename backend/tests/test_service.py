from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
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
        return f"reply-msgid-{len(self.sent)}"

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
        self.histories: list[Any] = []

    async def answer(self, question: str, history: Any = ()) -> str:
        self.questions.append(question)
        self.histories.append(list(history))
        if self.fail:
            raise LLMError("provider failed")
        return "你好，请问有什么可以帮你？"


class CustomerServiceProcessorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        clock = patch("wechat_bot.replies.time.time", return_value=123)
        clock.start()
        self.addCleanup(clock.stop)
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

        self.assertEqual(llm.questions, ["[1970-01-01T00:02:03+00:00] 客户\n你好"])
        self.assertEqual(
            wecom.sent,
            [("wk-account", "customer", "你好，请问有什么可以帮你？\n---\n客服剩余回复次数4，约48小时后清空，回复任意消息重置。")],
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

        self.assertEqual(wecom.sent[0][2].split("\n---\n")[0], FALLBACK_REPLY)
        self.assertIn("客服剩余回复次数4", wecom.sent[0][2])

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

        self.assertEqual(llm.questions, ["[1970-01-01T00:02:03+00:00] 客户\n你好"])
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

    async def test_batch_combines_all_questions_with_timestamps_in_one_reply(self) -> None:
        wecom = FakeWeCom()
        template = wecom.sync_results[1].messages[0]
        wecom.sync_results[1].messages[:] = [
            {**template, "msgid": f"batch-{index}", "text": {"content": f"问题{index}"}}
            for index in range(16)
        ]
        llm = FakeLLM()
        processor = CustomerServiceProcessor(wecom, llm, self.store)
        await processor.bootstrap()
        await processor.handle_event(CustomerServiceEvent("kf_msg_or_event", "token", "wk-account", 123))
        self.assertEqual(len(wecom.sent), 1)
        self.assertEqual(len(llm.questions), 1)
        self.assertEqual(llm.questions[0], "\n\n".join(
            f"[1970-01-01T00:02:03+00:00] 客户\n问题{index}" for index in range(16)
        ))
        self.assertEqual(llm.histories, [[]])
        self.assertIn("客服剩余回复次数4", wecom.sent[-1][2])
        self.assertTrue(all(self.store.is_processed(f"batch-{index}") for index in range(16)))
        self.assertEqual(len(self.store.history(1, turns=20)), 17)
        self.assertEqual(self.store.get_cursor("wk-account"), "cursor-2")

    async def test_combined_send_failure_retries_all_questions_without_losing_them(self) -> None:
        wecom = FakeWeCom(send_fail=True)
        template = wecom.sync_results[1].messages[0]
        batch = [{**template, "msgid": f"retry-{i}", "text": {"content": f"问题{i}"}} for i in range(3)]
        wecom.sync_results[1].messages[:] = batch
        llm = FakeLLM()
        processor = CustomerServiceProcessor(wecom, llm, self.store)
        await processor.bootstrap()
        with self.assertRaisesRegex(RuntimeError, "send failed"):
            await processor.handle_event(CustomerServiceEvent("kf_msg_or_event", "token", "wk-account", 123))
        self.assertFalse(any(self.store.is_processed(f"retry-{i}") for i in range(3)))
        self.assertEqual(self.store.get_cursor("wk-account"), "cursor-1")
        wecom.send_fail = False
        wecom.sync_results.append(SyncResult(batch, "cursor-2", 1))
        await processor.handle_event(CustomerServiceEvent("kf_msg_or_event", "token", "wk-account", 123))
        self.assertEqual(llm.questions[0], llm.questions[1])
        self.assertEqual(len(wecom.sent), 1)
        wecom.sync_results.append(SyncResult(batch, "cursor-3", 1))
        await processor.handle_event(CustomerServiceEvent("kf_msg_or_event", "token", "wk-account", 123))
        self.assertEqual(len(wecom.sent), 1)

    async def test_batch_orders_timestamps_and_deduplicates_inbound_ids(self) -> None:
        wecom = FakeWeCom()
        template = wecom.sync_results[1].messages[0]
        earlier = {**template, "msgid": "early", "send_time": 100, "text": {"content": "先问价格"}}
        later = {**template, "msgid": "late", "send_time": 123, "text": {"content": "再问配送"}}
        wecom.sync_results[1].messages[:] = [later, earlier, later]
        llm = FakeLLM()
        processor = CustomerServiceProcessor(wecom, llm, self.store)
        await processor.bootstrap()
        await processor.handle_event(CustomerServiceEvent("kf_msg_or_event", "token", "wk-account", 123))
        self.assertEqual(llm.questions, ["[1970-01-01T00:01:40+00:00] 客户\n先问价格\n\n[1970-01-01T00:02:03+00:00] 客户\n再问配送"])
        self.assertEqual(len(wecom.sent), 1)
        wecom.sync_results.append(SyncResult([later, earlier], "cursor-3", 1))
        await processor.handle_event(CustomerServiceEvent("kf_msg_or_event", "token", "wk-account", 123))
        self.assertEqual(len(wecom.sent), 1)

    async def test_batch_history_includes_human_reply_and_isolates_customers(self) -> None:
        user = self.store.get_or_create_customer("wk-account", "customer", seen_at=100)
        self.store.mark_sent(msgid="previous", user_id=user, open_kfid="wk-account", external_userid="customer",
            send_time=100, customer_content="旧问题", reply_content="旧回答", reply_source_message_id="old-reply")
        manual, _ = self.store.create_admin_message(user_id=user, open_kfid="wk-account", content="人工说明",
            operator_id="agent", request_id="manual")
        self.store.complete_admin_message(manual.id, status="sent")
        wecom = FakeWeCom()
        wecom.sync_results[1].messages.append({**wecom.sync_results[1].messages[0], "msgid": "other-in", "external_userid": "other", "text": {"content": "其他客户的问题"}})
        llm = FakeLLM()
        processor = CustomerServiceProcessor(wecom, llm, self.store)
        await processor.bootstrap()
        await processor.handle_event(CustomerServiceEvent("kf_msg_or_event", "token", "wk-account", 123))
        self.assertEqual(len(wecom.sent), 2)
        self.assertEqual([message.content for message in llm.histories[0]], ["旧问题", "旧回答", "人工说明"])
        self.assertEqual(llm.histories[0][-1].as_dict(), {"role": "assistant", "content": "[1970-01-01T00:02:03+00:00] 人工客服\n人工说明"})
        self.assertEqual(llm.histories[1], [])
        self.assertNotIn("其他客户", llm.questions[0])

    async def test_admin_refresh_catches_up_without_sending_replies(self) -> None:
        wecom = FakeWeCom()
        llm = FakeLLM()
        processor = CustomerServiceProcessor(wecom, llm, self.store)
        await processor.bootstrap()
        self.store.observe_customer_message("wk-account", "customer", "old", 1)
        await processor.refresh_for_admin("customer")
        self.assertEqual(wecom.sent, [])
        self.assertEqual(llm.questions, [])
        self.assertEqual(self.store.reply_budget("wk-account", "customer").received_at, 123)
        self.assertTrue(self.store.is_processed("customer-1"))
        self.assertEqual(self.store.get_cursor("wk-account"), "cursor-1")
        # Replaying the same inbound data must not send a delayed AI reply.
        wecom.sync_results.append(SyncResult([{"msgid": "customer-1", "origin": 3,
            "external_userid": "customer", "send_time": 123, "msgtype": "text", "text": {"content": "你好"}}], "cursor-3", 1))
        await processor.handle_event(CustomerServiceEvent("kf_msg_or_event", "token", "wk-account", 123))
        self.assertEqual(wecom.sent, [])

    async def test_non_text_customer_message_resets_quota(self) -> None:
        wecom = FakeWeCom()
        processor = CustomerServiceProcessor(wecom, FakeLLM(), self.store)
        await processor.bootstrap()
        await processor.handle_event(CustomerServiceEvent("kf_msg_or_event", "token", "wk-account", 123))
        self.assertEqual(self.store.reply_budget("wk-account", "customer").used, 1)
        await processor._process_message("wk-account", {
            "msgid": "image", "origin": 3, "external_userid": "customer",
            "send_time": 124, "msgtype": "image",
        }, {"customer": 1})
        self.assertEqual(self.store.reply_budget("wk-account", "customer").used, 0)
        self.assertEqual(len(wecom.sent), 1)

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

        self.assertEqual(wecom.sent[-1][2].split("\n---\n")[0], LOGIN_LINK_RATE_LIMIT_REPLY)
        self.assertIn("客服剩余回复次数4", wecom.sent[-1][2])
        self.assertNotIn("我的信息", LOGIN_LINK_RATE_LIMIT_REPLY)
