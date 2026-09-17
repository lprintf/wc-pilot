"""Intent enumeration with minimal deterministic routing.

Only system-level commands (profile) are hardcoded; all other intents
are classified by the LLM to avoid brittle keyword matching.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from wechat_bot.llm import OpenAICompatibleLLM


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
    {"个人中心", "我的信息", "我的消息"}
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

def _load_prompt(name: str) -> str:
    """Load a prompt from backend/prompts/."""
    from pathlib import Path
    prompts_dir = Path(__file__).resolve().parents[2] / "prompts"
    path = prompts_dir / name
    if path.exists():
        return path.read_text("utf-8")
    return ""


CLASSIFY_INTENT_PROMPT = _load_prompt("intent_classifier.md")


async def classify_intent_with_llm(
    llm_client: OpenAICompatibleLLM,
    question: str,
    history: Sequence[dict[str, str]] = (),
) -> Intent:
    """Ask the LLM to classify the user's intent from a fixed enum."""
    from wechat_bot.llm import ChatMessage
    chat_history = [
        ChatMessage(role=m["role"], content=m["content"])
        for m in history
        if m.get("role") in {"user", "assistant"}
    ]
    reply = await llm_client.answer(
        question,
        chat_history,
        system_prompt=CLASSIFY_INTENT_PROMPT,
    )
    normalized = reply.strip().lower()
    valid = set(Intent.__members__.values())
    if normalized in valid:
        return Intent(normalized)
    # fallback: try match after removing punctuation
    import re
    cleaned = re.sub(r"[^a-z_]", "", normalized)
    if cleaned in valid:
        return Intent(cleaned)
    return Intent.OTHER
