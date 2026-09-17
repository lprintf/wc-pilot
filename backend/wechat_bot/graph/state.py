"""Typed state for the WeChat customer-service assistant graph."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class CustomerServiceState(TypedDict, total=False):
    """Mutable state shared by the LangGraph ReAct loop.

    ``messages`` is reduced by LangGraph's ``add_messages`` so tool messages
    and model replies accumulate within a turn.  ``business_profile`` and
    ``conversation_round`` persist across invocations through the checkpoint.
    """

    user_id: int
    conversation_id: int
    open_kfid: str
    external_userid: str

    messages: Annotated[list[Any], add_messages]

    intent: str
    scenario: str

    business_profile: dict[str, str]
    conversation_round: int

    reply_text: str
    error: str | None
