from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from wechat_bot.callback import CustomerServiceEvent
from wechat_bot.llm import LLMError
from wechat_bot.service import CustomerServiceProcessor, FALLBACK_REPLY
from wechat_bot.store import MessageStore
from wechat_bot.wecom import SyncResult


class FakeWeCom:
    def __init__(self) -> None:
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
        self.sent.append((open_kfid, external_userid, content))
        return "reply-msgid"


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
