"""Intent enumeration with minimal deterministic routing.

Only system-level commands (profile) are hardcoded; all other intents
are derived by the graph from the conversation context (tool calls, etc.).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Sequence


class Intent(StrEnum):
    GREETING = "greeting"
    CAPABILITIES = "capabilities"
    KNOWLEDGE_QA = "knowledge_qa"
    LEAD_GEN = "lead_gen"
    AFTER_SALES = "after_sales"
    BUSINESS_DISCOVERY = "business_discovery"
    COST_FEASIBILITY = "cost_feasibility"
    HUMAN_HANDOFF = "human_handoff"
    PROFILE = "profile"
    OTHER = "other"


PROFILE_KEYWORDS: frozenset[str] = frozenset(
    {"\u4e2a\u4eba\u4e2d\u5fc3", "\u6211\u7684\u4fe1\u606f", "\u6211\u7684\u6d88\u606f"}
)


def is_profile_command(text: str) -> bool:
    """True when the text matches a known system-level profile command."""
    content = text.strip()
    return content in PROFILE_KEYWORDS


def merge_question_text(
    messages: Sequence[dict[str, str]],
) -> str:
    """Combine multiple customer messages into one prompt-friendly string.

    Timestamps and sender labels should have already been applied by the
    caller (see ``ChatMessage.as_dict``).  This helper simply concatenates
    content fields of messages whose role is ``user``.
    """
    parts: list[str] = []
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "").strip()
            if content:
                parts.append(content)
    return "\n\n".join(parts)
