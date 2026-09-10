"""Shared WeChat KF reply quota and message footer."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import TYPE_CHECKING

from wechat_bot.wecom import WeComAPIError, WeComClient

if TYPE_CHECKING:
    from wechat_bot.store import MessageStore

REPLY_LIMIT = 5
REPLY_WINDOW_SECONDS = 48 * 60 * 60
TEXT_LIMIT_BYTES = 2048
LOGGER = logging.getLogger(__name__)


class ReplyUnavailable(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReplyBudget:
    received_at: int
    used: int
    blocked_code: str | None = None

    def footer(self, *, now: float | None = None) -> str:
        seconds = self.received_at + REPLY_WINDOW_SECONDS - (time.time() if now is None else now)
        if self.received_at <= 0 or seconds <= 0 or self.blocked_code == "reply_window_expired":
            raise ReplyUnavailable("reply_window_expired", "客服回复窗口已过期，请等待客户回复任意消息后再发送")
        if self.used >= REPLY_LIMIT:
            raise ReplyUnavailable("reply_limit_reached", "客服回复次数已用尽，请等待客户回复任意消息后再发送")
        hours = f"{min(seconds / 3600, 48):.1f}".rstrip("0").rstrip(".")
        duration = f"约{hours}小时" if hours != "0" else "不足0.1小时"
        return f"\n---\n客服剩余回复次数{REPLY_LIMIT - self.used - 1}，{duration}后清空，回复任意消息重置。"


async def send_reply(
    wecom: WeComClient, store: MessageStore,
    open_kfid: str, external_userid: str, content: str,
) -> str:
    # Serialize quota checks and successful sends across both AI and admin paths.
    async with store.reply_lock(open_kfid):
        budget = store.reply_budget(open_kfid, external_userid)
        footer = budget.footer()
        available = TEXT_LIMIT_BYTES - len(footer.encode("utf-8"))
        body = content.encode("utf-8")
        if len(body) > available:
            content = body[:available - len("…".encode("utf-8"))].decode("utf-8", errors="ignore") + "…"
        try:
            msgid = await wecom.send_text(open_kfid, external_userid, content + footer)
        except WeComAPIError as exc:
            LOGGER.warning("WeChat KF reply failed: errcode=%s", exc.errcode)
            if exc.errcode in {95001, 95002}:
                store.exhaust_reply_budget(open_kfid, external_userid, expired=exc.errcode == 95002)
                code = "reply_limit_reached" if exc.errcode == 95001 else "reply_window_expired"
                reason = "次数已用尽" if exc.errcode == 95001 else "窗口已过期"
                raise ReplyUnavailable(code, f"客服回复{reason}，请等待客户回复任意消息后再发送") from exc
            raise
        store.consume_reply(open_kfid, external_userid)
        return msgid
