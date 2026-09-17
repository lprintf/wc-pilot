"""Orchestration from a WeCom customer message to an LLM reply."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Any

from wechat_bot.auth import AuthManager, LoginTicketRateLimited
from wechat_bot.graph.state import CustomerServiceState
from wechat_bot.callback import CustomerServiceEvent
from langchain_core.messages import HumanMessage
from wechat_bot.llm import LLMError, OpenAICompatibleLLM
from wechat_bot.replies import ReplyUnavailable, send_reply
from wechat_bot.store import MessageStore
from wechat_bot.wecom import WeComClient


LOGGER = logging.getLogger(__name__)
FALLBACK_REPLY = "抱歉，智能客服暂时无法回答，请稍后再试。"
PROFILE_CACHE_SECONDS = 24 * 60 * 60
CUSTOMER_BATCH_SIZE = 100
PROFILE_COMMANDS = frozenset({"我的信息", "我的消息", "个人中心"})
LOGIN_LINK_HISTORY_REPLY = "已发送一次性个人中心登录链接（链接已隐藏）。"
LOGIN_LINK_RATE_LIMIT_REPLY = (
    "为了保护账号安全，登录链接每分钟只能生成一次。"
    "请使用刚才收到的链接，或稍后再试。"
)


class CustomerServiceProcessor:
    def __init__(
        self,
        wecom: WeComClient,
        llm: OpenAICompatibleLLM,
        store: MessageStore,
        configured_open_kfid: str = "",
        auth: AuthManager | None = None,
        graph: object | None = None,
    ) -> None:
        self._wecom = wecom
        self._llm = llm
        self._store = store
        self._configured_open_kfid = configured_open_kfid
        self._auth = auth
        self._graph = graph
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

        if self._store.get_cursor(selected) is None:
            baseline = await self._wecom.sync_messages(selected)
            self._store.set_cursor(selected, baseline.next_cursor)
            LOGGER.info(
                "initialized customer-service cursor without replying to %d historical messages",
                len(baseline.messages),
            )
        else:
            LOGGER.info("restored persisted customer-service cursor")
        self._managed_open_kfid = selected

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
            # All messages in this batch have already arrived at WeChat. Register
            # them before replying so a backlog cannot reset the quota per reply.
            async with self._store.reply_lock(event.open_kfid):
                for message in messages:
                    if int(message.get("origin", 0)) == 3:
                        self._store.observe_customer_message(
                            event.open_kfid, str(message.get("external_userid", "")),
                            str(message.get("msgid", "")), int(message.get("send_time", 0)),
                        )
            customer_user_ids = await self._prepare_customer_profiles(
                event.open_kfid, messages
            )
            batches: dict[str, list[dict[str, Any]]] = {}
            seen: set[str] = set()
            replied = 0
            for message in messages:
                external_userid = str(message.get("external_userid", ""))
                msgid = str(message.get("msgid", ""))
                if not msgid or msgid in seen or self._store.is_processed(msgid):
                    continue
                seen.add(msgid)
                text = message.get("text", {})
                content = str(text.get("content", "")).strip() if isinstance(text, dict) else ""
                if (int(message.get("origin", 0)) == 3 and external_userid
                        and message.get("msgtype") == "text" and content
                        and not (content in PROFILE_COMMANDS and self._auth is not None)):
                    batches.setdefault(external_userid, []).append(message)
                    continue
                if await self._process_message(
                    event.open_kfid, message, customer_user_ids
                ):
                    replied += 1
            for external_userid, batch in batches.items():
                if await self._process_batch(event.open_kfid, external_userid, batch):
                    replied += 1
            self._store.set_cursor(event.open_kfid, result.next_cursor)
            LOGGER.info(
                "processed customer-service callback: messages=%d replies=%d pages=%d",
                len(messages),
                replied,
                result.pages,
            )

    @staticmethod
    def _batch_question(messages: list[dict[str, Any]]) -> str:
        parts = []
        for message in messages:
            send_time = int(message.get("send_time", 0))
            timestamp = datetime.fromtimestamp(send_time, timezone.utc).isoformat() if send_time > 0 else "时间未知"
            parts.append(f"[{timestamp}] 客户\n{str(message['text']['content']).strip()}")
        return "\n\n".join(parts)

    async def _process_batch(self, open_kfid: str, external_userid: str, messages: list[dict[str, Any]]) -> bool:
        async with self._store.customer_lock(open_kfid, external_userid):
            batch = [message for message in messages if not self._store.is_processed(str(message["msgid"]))]
            if not batch:
                return False
            latest_time = int(batch[-1].get("send_time", 0))
            user_id = self._store.get_or_create_customer(open_kfid, external_userid, seen_at=latest_time)
            budget = self._store.reply_budget(open_kfid, external_userid)
            try:
                budget.footer()
                history = self._store.history(user_id, timestamped=True)
                try:
                    reply = await self._invoke_graph(
                        open_kfid, external_userid, user_id,
                        self._store.get_or_create_conversation(user_id, open_kfid),
                        self._batch_question(batch), history,
                    )
                except Exception:
                    LOGGER.exception("Graph invocation failed; using fallback reply")
                    reply = FALLBACK_REPLY
                reply_id = await send_reply(self._wecom, self._store, open_kfid, external_userid, reply)
            except ReplyUnavailable as exc:
                for message in batch:
                    self._record_without_reply(open_kfid, message, user_id)
                LOGGER.info("customer batch reply skipped: reason=%s messages=%d", exc.code, len(batch))
                return False
            self._store.mark_batch_sent(
                messages=[(str(message["msgid"]), int(message.get("send_time", 0)), str(message["text"]["content"]).strip()) for message in batch],
                user_id=user_id, open_kfid=open_kfid, external_userid=external_userid,
                reply_content=reply, reply_source_message_id=reply_id,
            )
            return True

    async def _invoke_graph(
        self,
        open_kfid: str,
        external_userid: str,
        user_id: int,
        conversation_id: int,
        question: str,
        history: Any,
    ) -> str:
        if self._graph is None:
            answer = getattr(self._llm, "answer", None)
            if callable(answer):
                try:
                    return await answer(question, history)
                except (LLMError, ValueError):
                    LOGGER.exception("LLM request failed; using fallback reply")
                    return FALLBACK_REPLY
            return FALLBACK_REPLY
        result = await self._graph.ainvoke(
            {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "open_kfid": open_kfid,
                "external_userid": external_userid,
                "messages": [HumanMessage(content=question)],
            },
            config={"configurable": {"thread_id": str(conversation_id)}},
        )
        reply = str(result.get("reply_text") or "").strip() or FALLBACK_REPLY
        LOGGER.info(
            "graph result: user_id=%s intent=%s scenario=%s reply_len=%d profile_keys=%d round=%s",
            user_id,
            result.get("intent", "?"),
            result.get("scenario", "?"),
            len(reply),
            len(result.get("business_profile") or {}),
            result.get("conversation_round", "?"),
        )
        # Persist graph-derived state to the conversation for admin display
        self._store.update_conversation_graph_state(
            conversation_id,
            intent=str(result.get("intent", "")),
            scenario=str(result.get("scenario", "")),
            business_profile=result.get("business_profile"),
            conversation_round=result.get("conversation_round"),
        )
        return reply

    def _record_without_reply(self, open_kfid: str, message: dict[str, Any], user_id: int) -> None:
        text = message.get("text", {})
        self._store.mark_unanswered(
            msgid=str(message["msgid"]), user_id=user_id, open_kfid=open_kfid,
            external_userid=str(message["external_userid"]), send_time=int(message.get("send_time", 0)),
            customer_content=str(text.get("content", "")) if isinstance(text, dict) else "",
        )

    async def refresh_for_admin(self, target_external_userid: str) -> None:
        """Catch up persisted inbound state without sending historical AI replies."""
        open_kfid = self._managed_open_kfid
        async with self._locks.setdefault(open_kfid, asyncio.Lock()):
            cursor = self._store.get_cursor(open_kfid)
            if cursor is None:
                raise RuntimeError("customer-service cursor has not been initialized")
            result = await self._wecom.sync_messages(open_kfid, cursor)
            messages = sorted(result.messages, key=lambda item: int(item.get("send_time", 0)))
            async with self._store.reply_lock(open_kfid):
                for message in messages:
                    external_userid = str(message.get("external_userid", ""))
                    msgid = str(message.get("msgid", ""))
                    if int(message.get("origin", 0)) != 3 or external_userid != target_external_userid or not msgid:
                        continue
                    send_time = int(message.get("send_time", 0))
                    self._store.observe_customer_message(open_kfid, external_userid, msgid, send_time)
                    if not self._store.is_processed(msgid):
                        user_id = self._store.get_or_create_customer(open_kfid, external_userid, seen_at=send_time)
                        self._record_without_reply(open_kfid, message, user_id)
                    self._store.reconcile_reply_usage(open_kfid, external_userid)
            # Leave the callback cursor intact so other customers still receive
            # their normal automatic replies when the queued callback runs.

    async def _prepare_customer_profiles(
        self, open_kfid: str, messages: list[dict[str, Any]]
    ) -> dict[str, int]:
        customer_user_ids: dict[str, int] = {}
        for message in messages:
            if int(message.get("origin", 0)) != 3:
                continue
            external_userid = str(message.get("external_userid", "")).strip()
            if not external_userid:
                continue
            user_id = self._store.get_or_create_customer(
                open_kfid,
                external_userid,
                seen_at=int(message.get("send_time", 0)),
            )
            customer_user_ids[external_userid] = user_id

        stale_profiles = [
            (external_userid, user_id)
            for external_userid, user_id in customer_user_ids.items()
            if self._store.profile_needs_refresh(
                user_id, max_age_seconds=PROFILE_CACHE_SECONDS
            )
        ]
        for offset in range(0, len(stale_profiles), CUSTOMER_BATCH_SIZE):
            batch = stale_profiles[offset : offset + CUSTOMER_BATCH_SIZE]
            batch_user_ids = dict(batch)
            try:
                customers = await self._wecom.batch_get_customers(
                    list(batch_user_ids)
                )
                fetched_at = int(time.time())
                returned_user_ids: set[int] = set()
                for customer in customers:
                    user_id = batch_user_ids.get(customer.external_userid)
                    if user_id is None:
                        continue
                    self._store.update_customer_profile(
                        user_id=user_id,
                        external_userid=customer.external_userid,
                        nickname=customer.nickname,
                        avatar_url=customer.avatar_url,
                        gender=customer.gender,
                        unionid=customer.unionid,
                        scene=customer.scene,
                        scene_param=customer.scene_param,
                        fetched_at=fetched_at,
                    )
                    returned_user_ids.add(user_id)
                self._store.mark_profiles_fetched(
                    [
                        user_id
                        for user_id in batch_user_ids.values()
                        if user_id not in returned_user_ids
                    ],
                    fetched_at=fetched_at,
                )
                LOGGER.info(
                    "refreshed customer profiles: requested=%d returned=%d",
                    len(batch),
                    len(returned_user_ids),
                )
            except (RuntimeError, ValueError) as exc:
                LOGGER.warning(
                    "customer profile refresh failed; continuing message processing: "
                    "requested=%d error=%s",
                    len(batch),
                    type(exc).__name__,
                )
        return customer_user_ids

    async def _process_message(
        self,
        open_kfid: str,
        message: dict[str, Any],
        customer_user_ids: dict[str, int],
    ) -> bool:
        async with self._store.customer_lock(open_kfid, str(message.get("external_userid", ""))):
            return await self._process_with_budget(open_kfid, message, customer_user_ids)

    async def _process_with_budget(
        self, open_kfid: str, message: dict[str, Any], customer_user_ids: dict[str, int],
    ) -> bool:
        try:
            return await self._process_customer_message(open_kfid, message, customer_user_ids)
        except ReplyUnavailable as exc:
            external_userid = str(message["external_userid"])
            send_time = int(message.get("send_time", 0))
            user_id = self._store.get_or_create_customer(open_kfid, external_userid, seen_at=send_time)
            text = message.get("text", {})
            self._store.mark_unanswered(
                msgid=str(message["msgid"]), user_id=user_id, open_kfid=open_kfid,
                external_userid=external_userid, send_time=send_time,
                customer_content=str(text.get("content", "")) if isinstance(text, dict) else "",
            )
            LOGGER.info("customer reply skipped: reason=%s", exc.code)
            return False

    async def _process_customer_message(
        self,
        open_kfid: str,
        message: dict[str, Any],
        customer_user_ids: dict[str, int],
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
        async with self._store.reply_lock(open_kfid):
            self._store.observe_customer_message(open_kfid, external_userid, msgid, send_time)
        user_id = self._store.get_or_create_customer(open_kfid, external_userid, seen_at=send_time)
        if str(message.get("msgtype", "")) != "text":
            self._store.mark_ignored(
                msgid=msgid,
                user_id=user_id,
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
                user_id=user_id,
                open_kfid=open_kfid,
                external_userid=external_userid,
                send_time=send_time,
            )
            return False

        budget = self._store.reply_budget(open_kfid, external_userid)
        if send_time < budget.received_at:
            self._record_without_reply(open_kfid, message, user_id)
            return False
        budget.footer()
        if content in PROFILE_COMMANDS and self._auth is not None:
            try:
                ticket = self._auth.issue_login_ticket(user_id)
            except LoginTicketRateLimited:
                reply = LOGIN_LINK_RATE_LIMIT_REPLY
                stored_reply = reply
                ticket = None
            else:
                reply = (
                    "请在 10 分钟内打开以下链接进入个人中心：\n"
                    f"{ticket.url}\n"
                    "链接只能使用一次，请勿转发。"
                )
                stored_reply = LOGIN_LINK_HISTORY_REPLY
            try:
                reply_message_id = await send_reply(self._wecom, self._store, open_kfid, external_userid, reply)
            except Exception:
                if ticket is not None:
                    self._auth.cancel_login_ticket(ticket.token)
                raise
            self._store.mark_sent(
                msgid=msgid,
                user_id=user_id,
                open_kfid=open_kfid,
                external_userid=external_userid,
                send_time=send_time,
                customer_content=content,
                reply_content=stored_reply,
                reply_source_message_id=reply_message_id,
            )
            return True

        history = self._store.history(user_id, timestamped=True)
        conversation_id = self._store.get_or_create_conversation(user_id, open_kfid)
        try:
            reply = await self._invoke_graph(
                open_kfid, external_userid, user_id, conversation_id,
                self._batch_question([message]), history,
            )
        except Exception:
            LOGGER.exception("Graph invocation failed; using fallback reply")
            reply = FALLBACK_REPLY
        reply_message_id = await send_reply(self._wecom, self._store, open_kfid, external_userid, reply)
        self._store.mark_sent(
            msgid=msgid,
            user_id=user_id,
            open_kfid=open_kfid,
            external_userid=external_userid,
            send_time=send_time,
            customer_content=content,
            reply_content=reply,
            reply_source_message_id=reply_message_id,
        )
        return True
