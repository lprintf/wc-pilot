"""Orchestration from a WeCom customer message to an LLM reply."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from wechat_bot.callback import CustomerServiceEvent
from wechat_bot.llm import LLMError, OpenAICompatibleLLM
from wechat_bot.store import MessageStore
from wechat_bot.wecom import WeComClient


LOGGER = logging.getLogger(__name__)
FALLBACK_REPLY = "抱歉，智能客服暂时无法回答，请稍后再试。"


class CustomerServiceProcessor:
    def __init__(
        self,
        wecom: WeComClient,
        llm: OpenAICompatibleLLM,
        store: MessageStore,
        configured_open_kfid: str = "",
    ) -> None:
        self._wecom = wecom
        self._llm = llm
        self._store = store
        self._configured_open_kfid = configured_open_kfid
        self._managed_open_kfid = ""
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def managed_open_kfid(self) -> str:
        return self._managed_open_kfid

    async def bootstrap(self) -> None:
        accounts = await self._wecom.list_accounts()
        open_kfids = [
            str(account.get("open_kfid", ""))
            for account in accounts
            if str(account.get("open_kfid", ""))
        ]
        if self._configured_open_kfid:
            if self._configured_open_kfid not in open_kfids:
                raise RuntimeError("WECHAT_KF_OPEN_KFID is not visible to this application")
            selected = self._configured_open_kfid
        elif len(open_kfids) == 1:
            selected = open_kfids[0]
        elif not open_kfids:
            raise RuntimeError("no customer-service account is visible to the application")
        else:
            raise RuntimeError(
                "multiple customer-service accounts are visible; set WECHAT_KF_OPEN_KFID"
            )

        self._managed_open_kfid = selected
        if self._store.get_cursor(selected) is None:
            baseline = await self._wecom.sync_messages(selected)
            self._store.set_cursor(selected, baseline.next_cursor)
            LOGGER.info(
                "initialized customer-service cursor without replying to %d historical messages",
                len(baseline.messages),
            )
        else:
            LOGGER.info("restored persisted customer-service cursor")

    async def handle_event(self, event: CustomerServiceEvent) -> None:
        if not self._managed_open_kfid:
            raise RuntimeError("customer-service processor is not initialized")
        if event.open_kfid != self._managed_open_kfid:
            raise RuntimeError("callback is for an unmanaged customer-service account")
        lock = self._locks.setdefault(event.open_kfid, asyncio.Lock())
        async with lock:
            cursor = self._store.get_cursor(event.open_kfid)
            if cursor is None:
                raise RuntimeError("customer-service cursor has not been initialized")
            result = await self._wecom.sync_messages(
                event.open_kfid,
                cursor,
                event.token,
            )
            messages = sorted(
                result.messages, key=lambda item: int(item.get("send_time", 0))
            )
            replied = 0
            for message in messages:
                if await self._process_message(event.open_kfid, message):
                    replied += 1
            self._store.set_cursor(event.open_kfid, result.next_cursor)
            LOGGER.info(
                "processed customer-service callback: messages=%d replies=%d pages=%d",
                len(messages),
                replied,
                result.pages,
            )

    async def _process_message(
        self, open_kfid: str, message: dict[str, Any]
    ) -> bool:
        msgid = str(message.get("msgid", ""))
        if not msgid or self._store.is_processed(msgid):
            return False
        if int(message.get("origin", 0)) != 3:
            return False

        external_userid = str(message.get("external_userid", ""))
        send_time = int(message.get("send_time", 0))
        if not external_userid:
            LOGGER.warning("ignored customer message without external_userid")
            return False
        if str(message.get("msgtype", "")) != "text":
            self._store.mark_ignored(
                msgid=msgid,
                open_kfid=open_kfid,
                external_userid=external_userid,
                send_time=send_time,
            )
            return False
        text = message.get("text", {})
        content = str(text.get("content", "")).strip() if isinstance(text, dict) else ""
        if not content:
            self._store.mark_ignored(
                msgid=msgid,
                open_kfid=open_kfid,
                external_userid=external_userid,
                send_time=send_time,
            )
            return False

        history = self._store.history(external_userid)
        try:
            reply = await self._llm.answer(content, history)
        except (LLMError, ValueError):
            LOGGER.exception("LLM request failed; using fallback reply")
            reply = FALLBACK_REPLY
        await self._wecom.send_text(open_kfid, external_userid, reply)
        self._store.mark_sent(
            msgid=msgid,
            open_kfid=open_kfid,
            external_userid=external_userid,
            send_time=send_time,
            customer_content=content,
            reply_content=reply,
        )
        return True
