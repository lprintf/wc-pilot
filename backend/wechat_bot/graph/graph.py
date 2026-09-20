"""Build the LangGraph ReAct loop for the WeChat customer-service assistant."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool as langchain_tool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from wechat_bot.graph.intents import Intent
from wechat_bot.graph.knowledge import KnowledgeIndex
from wechat_bot.graph.state import CustomerServiceState

LOGGER = logging.getLogger(__name__)
_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
FALLBACK_REPLY = "\u62b1\u6b49\uff0c\u667a\u80fd\u5ba2\u670d\u6682\u65f6\u65e0\u6cd5\u56de\u7b54\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002"


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    if path.exists():
        return path.read_text("utf-8")
    return ""


def _make_tools(knowledge_index):
    @langchain_tool
    def search_knowledge(query: str, tags: str = "") -> str:
        """\u68c0\u7d22\u77e5\u8bc6\u5e93\u3002query \u7528 3-5 \u4e2a\u5173\u952e\u8bcd\uff0c\u7a7a\u683c\u5206\u9694\uff1btags \u53ef\u4f20\u4e1a\u52a1\u6807\u7b7e\uff0c\u9017\u53f7\u5206\u9694\uff08\u5982 \u4ea7\u54c1\u4ecb\u7ecd,\u83b7\u5ba2\u5f15\u6d41\uff09\u3002"""
        if knowledge_index is None:
            return "\u77e5\u8bc6\u5e93\u4e0d\u53ef\u7528\u3002"
        tag_list = [t.strip() for t in tags.replace("\uff0c", ",").split(",") if t.strip()]
        chunks = knowledge_index.search(query, top_n=3, tags=tag_list or None)
        if not chunks:
            return "\u6ca1\u6709\u627e\u5230\u76f8\u5173\u5185\u5bb9\u3002"
        return "\n\n".join(f"{c.source_label}\n{c.content}" for c in chunks)

    @langchain_tool
    def record_business_fact(field: str, value: str) -> str:
        """\u8bb0\u5f55\u5ba2\u6237\u900f\u9732\u7684\u4e00\u9879\u4e1a\u52a1\u4fe1\u606f\u3002field \u662f\u5b57\u6bb5\u540d\uff08\u5982 industry/channel/pain/goal\uff09\uff0cvalue \u662f\u5185\u5bb9\u3002"""
        return f"\u5df2\u8bb0\u5f55 {field}\uff1a{value}"

    @langchain_tool
    def escalate_to_human(reason: str) -> str:
        """\u5f53\u5ba2\u6237\u660e\u786e\u8868\u8fbe\u9700\u8981\u4eba\u5de5\u8ddf\u8fdb\u7684\u610f\u56fe\u65f6\u8c03\u7528\uff0c\u751f\u6210\u660e\u786e CTA\u3002"""
        return "\u597d\u7684\uff0c\u6211\u5df2\u7ecf\u8bb0\u5f55\u4f60\u7684\u9700\u6c42\u3002\u63a5\u4e0b\u6765\u53ef\u4ee5\u5b89\u6392\u4e00\u6b21\u4ea7\u54c1\u6f14\u793a\u6216\u5546\u52a1\u6c9f\u901a\uff0c\u4eba\u5de5\u5ba2\u670d\u4f1a\u7ee7\u7eed\u8ddf\u8fdb\u3002"

    return [search_knowledge, record_business_fact, escalate_to_human]


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
    if "search_knowledge" in tool_seen and "record_business_fact" in tool_seen:
        return str(Intent.BUSINESS_DISCOVERY)
    if "search_knowledge" in tool_seen:
        return str(Intent.KNOWLEDGE_QA)
    if "record_business_fact" in tool_seen:
        return str(Intent.BUSINESS_DISCOVERY)
    return str(Intent.OTHER)


def _build_agent_model(model, tools):
    if model is None:
        return None
    return model.bind_tools(tools)


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
        system_text += f"\n\n\u5f53\u524d\u4e1a\u52a1\u753b\u50cf\uff1a\n{json.dumps(profile, ensure_ascii=False, indent=2)}"
    round_num = state.get("conversation_round") or 0
    if round_num:
        system_text += f"\n\n\u5f53\u524d\u662f\u7b2c {round_num} \u8f6e\u5bf9\u8bdd\u3002\n\n\u53ef\u4ee5\u4f7f\u7528\u5de5\u5177\uff1asearch_knowledge\u3001record_business_fact\u3001escalate_to_human\u3002"

    messages = [SystemMessage(content=system_text)]
    messages.extend(state.get("messages") or [])

    try:
        response = await model_bound.ainvoke(messages)
    except Exception:
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
    knowledge_index: KnowledgeIndex | None = None,
    model: Any = None,
    checkpointer: Any = None,
):
    """Build and compile the ReAct loop graph.

    The graph handles tool-calling cycles (agent -> tools -> agent)
    followed by a finalize step that extracts reply text and business state.
    """
    tools = _make_tools(knowledge_index)
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
