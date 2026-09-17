"""Typed state for the WeChat customer-service assistant graph."""

from __future__ import annotations

from typing import Any, TypedDict


class CustomerServiceState(TypedDict, total=False):
    """Mutable state shared by the LangGraph nodes.

    The graph is invoked once per WeChat callback batch.  ``business_profile``
    and ``conversation_round`` persist across invocations through the
    LangGraph checkpoint.
    """

    user_id: int
    conversation_id: int
    open_kfid: str
    external_userid: str

    incoming_messages: list[dict[str, Any]]
    history: list[dict[str, Any]]

    intent: str
    scenario: str

    knowledge_chunks: list[dict[str, Any]]
    business_profile: dict[str, str]
    conversation_round: int

    reply_text: str
    error: str | None

