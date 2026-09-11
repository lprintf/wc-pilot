from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from wechat_bot.replies import ReplyBudget, ReplyUnavailable, send_reply
from wechat_bot.store import MessageStore
from wechat_bot.wecom import WeComAPIError


class ReplyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "test.db"
        self.store = MessageStore(self.path)
        self.clock = patch("wechat_bot.replies.time.time", return_value=1000)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.wecom = AsyncMock()
        self.wecom.send_text.return_value = "out-1"
        self.store.observe_customer_message("kf", "customer", "in-1", 1000)

    async def asyncTearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    async def send(self, content: str = "你好") -> str:
        return await send_reply(self.wecom, self.store, "kf", "customer", content)

    async def test_five_sends_then_wait_for_new_customer_message(self) -> None:
        for remaining in range(4, -1, -1):
            await self.send()
            self.assertIn(f"客服剩余回复次数{remaining}，约48小时后清空", self.wecom.send_text.call_args.args[2])
        with self.assertRaises(ReplyUnavailable) as error:
            await self.send()
        self.assertEqual(error.exception.code, "reply_limit_reached")
        self.assertEqual(self.wecom.send_text.await_count, 5)
        self.store.observe_customer_message("kf", "customer", "in-2", 1001)
        await self.send()
        self.assertIn("客服剩余回复次数4", self.wecom.send_text.call_args.args[2])

    async def test_failed_send_does_not_consume_reply(self) -> None:
        self.wecom.send_text.side_effect = RuntimeError("network unavailable")
        with self.assertRaises(RuntimeError):
            await self.send()
        self.assertEqual(self.store.reply_budget("kf", "customer").used, 0)
        self.wecom.send_text.side_effect = None
        await self.send()
        self.assertIn("客服剩余回复次数4", self.wecom.send_text.call_args.args[2])

    async def test_restart_duplicates_and_older_messages_do_not_reset_quota(self) -> None:
        await self.send()
        self.store.close()
        self.store = MessageStore(self.path)
        self.store.observe_customer_message("kf", "customer", "in-1", 1000)
        self.store.observe_customer_message("kf", "customer", "delayed", 999)
        self.assertEqual(self.store.reply_budget("kf", "customer").used, 1)
        # Distinct customer messages in the same second still refresh the quota.
        self.store.observe_customer_message("kf", "customer", "in-2", 1000)
        self.assertEqual(self.store.reply_budget("kf", "customer").used, 0)

    async def test_concurrent_sends_share_five_slots(self) -> None:
        async def slow_send(*_args: str) -> str:
            await asyncio.sleep(0)
            return "sent"
        self.wecom.send_text.side_effect = slow_send
        results = await asyncio.gather(*(self.send() for _ in range(8)), return_exceptions=True)
        self.assertEqual(results.count("sent"), 5)
        self.assertEqual(sum(isinstance(result, ReplyUnavailable) for result in results), 3)
        self.assertEqual(self.wecom.send_text.await_count, 5)

    async def test_long_unicode_body_keeps_complete_footer_in_byte_limit(self) -> None:
        await self.send("中文😀" * 1000)
        text = self.wecom.send_text.call_args.args[2]
        self.assertLessEqual(len(text.encode("utf-8")), 2048)
        self.assertNotIn("\ufffd", text)
        self.assertTrue(text.endswith("\n---\n客服剩余回复次数4，约48小时后清空，回复任意消息重置。"))
        self.assertEqual(text.count("\n---\n"), 1)

    async def test_window_expiry_does_not_call_wechat(self) -> None:
        with patch("wechat_bot.replies.time.time", return_value=1000 + 48 * 3600):
            with self.assertRaises(ReplyUnavailable) as error:
                await self.send()
        self.assertEqual(error.exception.code, "reply_window_expired")
        self.wecom.send_text.assert_not_awaited()

    async def test_upstream_limits_block_until_customer_replies(self) -> None:
        for errcode, code in ((95001, "reply_limit_reached"), (95002, "reply_window_expired")):
            with self.subTest(errcode=errcode):
                self.store.observe_customer_message("kf", "customer", f"reset-{errcode}", 1000)
                self.wecom.send_text.side_effect = WeComAPIError("kf/send_msg", errcode, "limited")
                with self.assertRaises(ReplyUnavailable) as error:
                    await self.send()
                self.assertEqual(error.exception.code, code)
                self.wecom.send_text.reset_mock()
                # A late older message must not clear a server-side block.
                self.store.observe_customer_message("kf", "customer", f"old-{errcode}", 999)
                with self.assertRaises(ReplyUnavailable) as blocked:
                    await self.send()
                self.assertEqual(blocked.exception.code, code)
                self.wecom.send_text.assert_not_awaited()
        self.store.observe_customer_message("kf", "customer", "new", 1001)
        self.wecom.send_text.side_effect = None
        await self.send()

    async def test_other_customer_or_account_does_not_reset_budget(self) -> None:
        await self.send()
        self.store.observe_customer_message("other-kf", "customer", "other-account", 1000)
        self.store.observe_customer_message("kf", "other-customer", "other-user", 1000)
        self.assertEqual(self.store.reply_budget("kf", "customer").used, 1)

    def test_footer_uses_actual_remaining_time(self) -> None:
        self.assertIn("约1.5小时后清空", ReplyBudget(1000, 2).footer(now=1000 + 46.5 * 3600))
        self.assertIn("不足0.1小时后清空", ReplyBudget(1000, 2).footer(now=1000 + 48 * 3600 - 1))

    def test_legacy_history_seeds_quota_once(self) -> None:
        user_id = self.store.get_or_create_customer("legacy", "user", seen_at=1000)
        self.store.mark_sent(msgid="legacy-in", user_id=user_id, open_kfid="legacy",
                             external_userid="user", send_time=1000,
                             customer_content="问题", reply_content="答复", reply_source_message_id="legacy-real-out")
        message, _ = self.store.create_admin_message(user_id=user_id, open_kfid="legacy", content="人工",
                                                      operator_id="agent", request_id="legacy-out")
        self.store.complete_admin_message(message.id, status="sent")
        self.store.close()
        self.store = MessageStore(self.path)
        self.assertEqual(self.store.reply_budget("legacy", "user").used, 2)
        self.store.observe_customer_message("legacy", "user", "legacy-in", 1000)
        self.assertEqual(self.store.reply_budget("legacy", "user").used, 2)

    def test_refresh_counts_manual_send_after_previously_missed_inbound(self) -> None:
        user = self.store.get_or_create_customer("kf", "customer", seen_at=1000)
        message, _ = self.store.create_admin_message(user_id=user, open_kfid="kf", content="manual",
            operator_id="agent", request_id="before-sync")
        self.store.complete_admin_message(message.id, status="sent")
        self.store.observe_customer_message("kf", "customer", "missed", 999)
        self.store.reconcile_reply_usage("kf", "customer")
        self.assertEqual(self.store.reply_budget("kf", "customer").used, 1)
        self.store.reconcile_reply_usage("kf", "customer")
        self.assertEqual(self.store.reply_budget("kf", "customer").used, 1)
