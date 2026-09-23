"""Build the LangGraph ReAct loop for the WeChat customer-service assistant."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, trim_messages
from langchain_core.tools import tool as langchain_tool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from wechat_bot.garden import GardenKnowledgeSource
from wechat_bot.graph.intents import Intent
from wechat_bot.graph.state import CustomerServiceState

LOGGER = logging.getLogger(__name__)
_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
FALLBACK_REPLY = "抱歉，智能客服暂时无法回答，请稍后再试。"
UPSTREAM_REPLY = "抱歉，AI 服务暂时繁忙，请稍后再试。"


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    if path.exists():
        return path.read_text("utf-8")
    return ""


def _make_tools(garden):
    @langchain_tool
    def search_garden(query: str, tags: str = "") -> str:
        """检索数字花园知识库。将客户原话联想为精炼关键词，不要原样粘贴。“你们做什么”→ 产品 能力；“介绍一下产品”→ 产品介绍 系统能力；“你们公司”→ 团队 能力；“产品资料”→ 产品介绍 能力。检索不到相关结果时，换个角度精炼关键词重试，不要编造。"""
        if garden is None:
            return "知识库不可用。"
        tag_list = [t.strip() for t in tags.replace("，", ",").split(",") if t.strip()]
        hits = garden.search(query, top_n=3, tags=tag_list or None)
        if not hits:
            return "没有找到相关内容。"
        lines = []
        for h in hits:
            lines.append(
                f"标题：{h.title}\n"
                f"来源：{h.slug}\n"
                f"标签：{', '.join(h.tags[:8])}\n"
                f"相关文章：{', '.join(h.outgoing[:5])}\n"
                f"被引用：{h.backlink_count} 篇"
            )
        return "\n---\n".join(lines)

    @langchain_tool
    def read_garden_note(slug: str) -> str:
        """读取数字花园某篇文章全文。slug 为 search_garden 返回的来源路径，例如 product/overview。只在确认搜到相关文章后再读取全文。"""
        if garden is None:
            return "知识库不可用。"
        body = garden.read_note(slug)
        if body is None:
            return f"未找到文章：{slug}"
        node = garden._nodes.get(slug)
        header = f"来源：{slug}"
        if node and node.title:
            header = f"标题：{node.title}\n来源：{slug}"
        return f"{header}\n\n{body}"

    @langchain_tool
    def record_business_fact(field: str, value: str) -> str:
        """记录客户透露的一项业务信息。field 是字段名（如 industry/channel/pain/goal），value 是内容。"""
        return f"已记录 {field}：{value}"

    @langchain_tool
    def escalate_to_human(reason: str) -> str:
        """当客户明确表达需要人工跟进的意图时调用，生成明确 CTA。"""
        return "好的，我已经记录你的需求。接下来可以安排一次产品演示或商务沟通，人工客服会继续跟进。"

    return [search_garden, read_garden_note, record_business_fact, escalate_to_human]


def _intent_from_tool_calls(messages: list[Any]) -> str:
    """Derive a display intent from the tool calls made during the conversation."""
    tool_seen: set[str] = set()
    for msg in messages:
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                tool_seen.add(name)
    if "escalate_to_human" in tool_seen:
        return str(Intent.HUMAN_HANDOFF)
    if "search_garden" in tool_seen and "record_business_fact" in tool_seen:
        return str(Intent.BUSINESS_DISCOVERY)
    if "search_garden" in tool_seen or "read_garden_note" in tool_seen:
        return str(Intent.KNOWLEDGE_QA)
    if "record_business_fact" in tool_seen:
        return str(Intent.BUSINESS_DISCOVERY)
    return str(Intent.OTHER)


def _build_agent_model(model, tools):
    if model is None:
        return None
    return model.bind_tools(tools)


def _trim_history(messages, model):
    """Keep recent context within a token budget."""
    if not messages:
        return messages
    try:
        return trim_messages(
            messages,
            max_tokens=6000,
            strategy="last",
            token_counter=model,
            include_system=False,
            start_on="human",
        )
    except Exception:
        LOGGER.debug("trim_messages failed, using full history", exc_info=True)
        return messages



_LLM_MAX_RETRIES = 2
_LLM_BACKOFF_SECONDS = (0.5, 1.5)


def _is_transient_llm_error(exc: Exception) -> bool:
    """Return True for retryable LLM provider errors (rate limit / gateway / timeout)."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if any(k in name for k in ("ratelimit", "timeout", "connection", "apierror")):
        return True
    return any(
        k in text
        for k in ("502", "503", "504", "429", "upstream", "temporarily unavailable", "bad gateway")
    )


async def _invoke_with_retry(model_bound, messages):
    """Call the model, retrying transient upstream errors with backoff."""
    last_exc: Exception | None = None
    for attempt in range(_LLM_MAX_RETRIES + 1):
        try:
            return await model_bound.ainvoke(messages)
        except Exception as exc:
            last_exc = exc
            if attempt >= _LLM_MAX_RETRIES or not _is_transient_llm_error(exc):
                raise
            delay = _LLM_BACKOFF_SECONDS[attempt]
            LOGGER.warning(
                "agent LLM transient error, retrying in %.2fs (attempt %d/%d): %s",
                delay,
                attempt + 1,
                _LLM_MAX_RETRIES,
                exc,
            )
            await asyncio.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("model invocation failed without exception")


async def _agent(state, model_bound):
    """Agent node: calls the bound model with system prompt and message history."""
    if model_bound is None:
        return {
            "messages": [AIMessage(content=FALLBACK_REPLY)],
            "error": "no model configured",
        }
    system_text = _load_prompt("customer_service.md")
    profile = state.get("business_profile") or {}
    if profile:
        system_text += f"\n\n当前业务画像：\n{json.dumps(profile, ensure_ascii=False, indent=2)}"

    history = list(state.get("messages") or [])
    if history:
        history = _trim_history(history, model_bound)

    messages = [SystemMessage(content=system_text)]
    messages.extend(history)

    try:
        response = await _invoke_with_retry(model_bound, messages)
    except Exception as exc:
        if _is_transient_llm_error(exc):
            LOGGER.error("agent LLM call failed (upstream unavailable): %s", exc)
            return {
                "messages": [AIMessage(content=UPSTREAM_REPLY)],
                "error": "upstream_unavailable",
            }
        LOGGER.exception("agent LLM call failed")
        return {
            "messages": [AIMessage(content=FALLBACK_REPLY)],
            "error": "model call failed",
        }
    tool_call_count = len(getattr(response, "tool_calls", []) or [])
    content_len = len(str(response.content) or "") if response.content else 0
    LOGGER.info(
        "agent: tool_calls=%d content_len=%d",
        tool_call_count,
        content_len,
    )
    return {"messages": [response], "error": None}


def _prepare(state: CustomerServiceState) -> dict[str, object]:
    """Run once per invocation: increment conversation round."""
    return {"conversation_round": (state.get("conversation_round") or 0) + 1}


def _finalize_reply(state: CustomerServiceState) -> dict[str, object]:
    """Extract final reply, derive intent and scenario from tool calls."""
    messages = state.get("messages") or []

    reply = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            content = msg.content
            if isinstance(content, list):
                reply = "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ).strip()
            elif isinstance(content, str):
                reply = content.strip()
            if reply:
                break
    if not reply:
        reply = FALLBACK_REPLY

    profile: dict[str, str] = dict(state.get("business_profile") or {})
    scenario = state.get("scenario") or ""
    escalated = False
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        tcs = getattr(msg, "tool_calls", []) or []
        for tc in tcs:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
            args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    args = {}
            if name == "record_business_fact":
                field = str(args.get("field", "")).strip()
                value = str(args.get("value", "")).strip()
                if field and value:
                    profile[field] = value
            elif name == "escalate_to_human":
                escalated = True

    if escalated:
        scenario = "human_handoff"

    intent = _intent_from_tool_calls(messages)

    LOGGER.info(
        "finalize: reply_len=%d profile_keys=%d scenario=%s intent=%s",
        len(reply), len(profile), scenario, intent,
    )

    return {
        "reply_text": reply,
        "business_profile": profile,
        "scenario": scenario,
        "intent": intent,
        "error": state.get("error"),
    }


def build_graph(
    *,
    garden: GardenKnowledgeSource | None = None,
    model: Any = None,
    checkpointer: Any = None,
):
    """Build and compile the ReAct loop graph.

    The graph handles tool-calling cycles (agent -> tools -> agent)
    followed by a finalize step that extracts reply text and business state.
    """
    tools = _make_tools(garden)
    model_bound = _build_agent_model(model, tools)

    async def agent_wrapper(state: CustomerServiceState) -> dict[str, object]:
        return await _agent(state, model_bound)

    workflow = StateGraph(CustomerServiceState)

    workflow.add_node("prepare", _prepare)
    workflow.add_node("agent", agent_wrapper)
    workflow.add_node("tools", ToolNode(tools))
    workflow.add_node("finalize_reply", _finalize_reply)

    workflow.add_edge(START, "prepare")
    workflow.add_edge("prepare", "agent")
    workflow.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", "__end__": "finalize_reply"},
    )
    workflow.add_edge("tools", "agent")
    workflow.add_edge("finalize_reply", END)

    return workflow.compile(checkpointer=checkpointer)


COMPILED_GRAPH = build_graph()