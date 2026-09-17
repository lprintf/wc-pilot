"""Build the LangGraph for the WeChat customer-service assistant."""

from __future__ import annotations

import json
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from wechat_bot.graph.intents import (
    Intent,
    classify_intent_with_llm,
    is_profile_command,
    merge_question_text,
)
from wechat_bot.graph.knowledge import KnowledgeIndex
from wechat_bot.graph.state import CustomerServiceState

import logging

LOGGER = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"

def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    if path.exists():
        return path.read_text("utf-8")
    return ""

def _load_skill(name: str) -> str:
    path = _SKILLS_DIR / name
    if path.exists():
        return path.read_text("utf-8")
    return ""

SYSTEM_PROMPT = _load_prompt("customer_service.md")
FOLLOW_UP_PROMPT = _load_prompt("follow_up.md")

def load_conversation(state: CustomerServiceState) -> dict[str, object]:
    """Load previous conversation state from the LangGraph checkpoint."""
    return {}

async def detect_intent(state: CustomerServiceState) -> dict[str, object]:
    """Route profile commands deterministically; delegate everything else to LLM."""
    if state.get("intent"):
        return {"error": None}
    question = merge_question_text(state.get("incoming_messages", []))
    if is_profile_command(question):
        return {"intent": str(Intent.PROFILE), "error": None}
    llm_client = state.get("llm_client")
    if llm_client is None:
        return {"intent": str(Intent.OTHER), "error": None}
    intent = await classify_intent_with_llm(llm_client, question, state.get("history", []))
    LOGGER.info("detect_intent: user_id=%s intent=%s question_len=%d", state.get("user_id"), intent, len(question))
    return {"intent": str(intent), "error": None}

def route_intent(state: CustomerServiceState) -> dict[str, object]:
    return {}

def retrieve_knowledge(state: CustomerServiceState) -> dict[str, object]:
    """Retrieve knowledge chunks using query from incoming messages."""
    question = merge_question_text(state.get("incoming_messages", []))
    index = state.get("knowledge_index")
    if index is None:
        return {"knowledge_chunks": []}
    chunks = index.search(question, top_n=5)
    LOGGER.info("retrieve_knowledge: chunks=%d query=%.80s", len(chunks), question)
    return {
        "knowledge_chunks": [
            {
                "document_path": chunk.document_path,
                "title": chunk.title,
                "heading": chunk.heading,
                "content": chunk.content,
                "ordinal": chunk.ordinal,
                "source_label": chunk.source_label,
            }
            for chunk in chunks
        ]
    }

def answer_with_kb(state: CustomerServiceState) -> dict[str, object]:
    chunks = state.get("knowledge_chunks", [])
    if not chunks:
        return {"reply_text": "抱歉，我暂时没有在知识库中找到相关内容。"}
    LOGGER.info("answer_with_kb: sources=%d", len(chunks))
    parts: list[str] = ["以下回答来自知识库："]
    seen: set[str] = set()
    for chunk in chunks:
        source = chunk.get("source_label") or chunk.get("document_path", "")
        content = chunk.get("content", "").strip()
        if not content or source in seen:
            continue
        seen.add(source)
        parts.append(f"\n{source}\n\n{content}")
    return {"reply_text": "\n".join(parts)}

def describe_capabilities(state: CustomerServiceState) -> dict[str, object]:
    LOGGER.info("describe_capabilities: user_id=%s", state.get("user_id"))
    index = state.get("knowledge_index")
    if index is None:
        return {"reply_text": "我可以帮助企业搭建获客引流、售后、知识库问答和人工协同系统。请描述你的业务场景。"}
    chunks = index.search("获客引流 售后 知识库问答 人工协同", top_n=5)
    if not chunks:
        return {"reply_text": "我可以帮助企业搭建获客引流、售后、知识库问答和人工协同系统。请描述你的业务场景。"}
    parts: list[str] = ["我们目前展示以下能力："]
    seen: set[str] = set()
    for ch in chunks:
        if ch.content in seen:
            continue
        seen.add(ch.content)
        parts.append(f"\n{ch.source_label}\n\n{ch.content}")
    return {"reply_text": "\n".join(parts)}

def engage_conversation(state: CustomerServiceState) -> dict[str, object]:
    """Open a natural business conversation based on intent."""
    intent = state.get("intent", "")
    scenario = intent
    question = merge_question_text(state.get("incoming_messages", []))
    conversation_round = (state.get("conversation_round") or 0) + 1
    skill_name = {
        str(Intent.LEAD_GEN): "lead_gen.md",
        str(Intent.AFTER_SALES): "after_sales.md",
        str(Intent.BUSINESS_DISCOVERY): "business_analysis.md",
        str(Intent.COST_FEASIBILITY): "business_analysis.md",
    }.get(intent)
    skill_text = _load_skill(skill_name) if skill_name else ""
    index = state.get("knowledge_index")
    chunks_raw: list[dict] = []
    if index is not None:
        for c in index.search(question, top_n=3):
            chunks_raw.append({"source": c.source_label, "content": c.content})
    LOGGER.info("engage_conversation: intent=%s round=%d", intent, conversation_round)
    return {
        "scenario": scenario,
        "conversation_round": conversation_round,
        "business_profile": {},
        "knowledge_chunks": chunks_raw,
        "reply_text": (
            "好的，关于" + _intent_label(intent) + "，能简单聊聊你的业务吗？"
            "比如你是什么行业的，主要在哪个渠道做客服？"
        ),
    }

async def follow_up(state: CustomerServiceState) -> dict[str, object]:
    """LLM-driven natural follow-up."""
    llm_client = state.get("llm_client")
    question = merge_question_text(state.get("incoming_messages", []))
    business_profile = dict(state.get("business_profile") or {})
    conversation_round = (state.get("conversation_round") or 0) + 1
    intent = state.get("intent", "")
    scenario = state.get("scenario", "")
    skill_name = {
        str(Intent.LEAD_GEN): "lead_gen.md",
        str(Intent.AFTER_SALES): "after_sales.md",
        str(Intent.BUSINESS_DISCOVERY): "business_analysis.md",
        str(Intent.COST_FEASIBILITY): "business_analysis.md",
    }.get(intent, "business_analysis.md")
    skill_text = _load_skill(skill_name)
    index = state.get("knowledge_index")
    chunks_raw: list[dict] = []
    if index is not None:
        profile_keywords = " ".join(business_profile.values())
        search_q = (question + " " + profile_keywords).strip()
        for c in index.search(search_q, top_n=3):
            chunks_raw.append({"source": c.source_label, "content": c.content})
    if llm_client is None:
        return {"reply_text": "抱歉，智能客服暂时无法回答，请稍后再试。"}
    prompt = FOLLOW_UP_PROMPT
    if not prompt:
        prompt = _build_follow_up_prompt_fallback()
    try:
        from wechat_bot.llm import ChatMessage
        result_text = await llm_client.answer(
            json.dumps({
                "business_profile": business_profile,
                "conversation_round": conversation_round,
                "intent": intent,
                "user_message": question,
                "skill_guide": skill_text[:1500],
                "knowledge_context": chunks_raw[:2],
            }, ensure_ascii=False),
            system_prompt=prompt,
        )
        parsed = _parse_llm_json(result_text)
    except Exception:
        LOGGER.exception("follow_up LLM call failed")
        return {"reply_text": "抱歉，智能客服暂时无法回答，请稍后再试。"}
    reply = parsed.get("reply", "") or "能再详细说说吗？"
    new_profile = parsed.get("business_profile") or business_profile
    should_show = parsed.get("should_show_case", False)
    should_propose = parsed.get("should_propose", False)
    LOGGER.info("follow_up: round=%d profile_keys=%d show=%s propose=%s", conversation_round, len(new_profile), should_show, should_propose)
    result: dict[str, object] = {
        "reply_text": reply,
        "business_profile": new_profile,
        "conversation_round": conversation_round,
        "knowledge_chunks": chunks_raw,
        "scenario": scenario or intent,
    }
    if should_show and conversation_round >= 2:
        result["_route"] = "show_capability"
    elif should_propose and conversation_round >= 3:
        result["_route"] = "propose_next"
    else:
        result["_route"] = "finalize"
    return result

def show_capability(state: CustomerServiceState) -> dict[str, object]:
    """Present a relevant case/capability from the knowledge base."""
    business_profile = state.get("business_profile") or {}
    index = state.get("knowledge_index")
    if index is None:
        return {"reply_text": state.get("reply_text") or "基于我们聊的内容，AI 可以在你的场景中落地。"}
    profile_kw = " ".join(business_profile.values())
    question = merge_question_text(state.get("incoming_messages", []))
    search_q = (profile_kw + " " + question).strip()
    chunks = index.search(search_q, top_n=3)
    if not chunks:
        return {"reply_text": state.get("reply_text")}
    parts = [state.get("reply_text") or "以下是我们相关的案例和能力："]
    seen = set()
    for c in chunks:
        if c.content in seen:
            continue
        seen.add(c.content)
        parts.append(f"\n{c.source_label}\n\n{c.content}")
    return {"reply_text": "\n".join(parts)}

def finalize_reply(state: CustomerServiceState) -> dict[str, object]:
    LOGGER.info("finalize_reply: intent=%s reply_len=%d", state.get("intent", "?"), len(state.get("reply_text", "") or ""))
    if not state.get("reply_text"):
        return {"reply_text": "抱歉，我暂时无法回答，请换一种方式描述你的问题。"}
    return {}

def escalate_to_human(state: CustomerServiceState) -> dict[str, object]:
    LOGGER.info("escalate_to_human: user_id=%s", state.get("user_id"))
    return {
        "reply_text": "好的，我已经记录了你的兴趣。接下来可以安排一次产品演示或商务沟通，人工客服会继续跟进。",
    }

def _intent_label(intent: str) -> str:
    return {
        str(Intent.LEAD_GEN): "获客引流",
        str(Intent.AFTER_SALES): "售后客服",
        str(Intent.BUSINESS_DISCOVERY): "业务咨询",
        str(Intent.COST_FEASIBILITY): "落地评估",
    }.get(intent, "你的需求")

def _parse_llm_json(text: str) -> dict:
    """Best-effort JSON parse from LLM output."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    import re
    m = re.search(r"{[\s\S]*}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {}

def _build_follow_up_prompt_fallback() -> str:
    return "你是 AI 客服展示助手的追问节点。根据客户回复更新 business_profile 并生成下一轮回复。"

_INTENT_ROUTES: dict[str, str] = {
    str(Intent.PROFILE): "profile",
    str(Intent.GREETING): "greeting",
    str(Intent.CAPABILITIES): "capabilities",
    str(Intent.KNOWLEDGE_QA): "knowledge_qa",
    str(Intent.LEAD_GEN): "engage",
    str(Intent.AFTER_SALES): "engage",
    str(Intent.BUSINESS_DISCOVERY): "engage",
    str(Intent.COST_FEASIBILITY): "engage",
    str(Intent.HUMAN_HANDOFF): "human_handoff",
    str(Intent.OTHER): "other",
}

def _route_intent(state: CustomerServiceState) -> str:
    intent = str(state.get("intent", ""))
    return _INTENT_ROUTES.get(intent, "other")

_FOLLOW_UP_ROUTES = {
    "show_capability": "show_capability",
    "propose_next": "finalize_reply",
    "finalize": "finalize_reply",
}

def _after_follow_up(state: CustomerServiceState) -> str:
    route = str(state.get("_route", "finalize"))
    return _FOLLOW_UP_ROUTES.get(route, "finalize")

def build_graph(*, knowledge_index=None, llm_client=None, checkpointer=None):
    graph = StateGraph(CustomerServiceState)
    graph.add_node("load_conversation", load_conversation)

    async def _detect(state):
        return await detect_intent(state | {"llm_client": llm_client})
    graph.add_node("detect_intent", _detect)
    graph.add_node("route_intent", route_intent)

    graph.add_node("describe_capabilities", lambda s: describe_capabilities(s | {"knowledge_index": knowledge_index}))
    graph.add_node("retrieve_knowledge", lambda s: retrieve_knowledge(s | {"knowledge_index": knowledge_index}))
    graph.add_node("answer_with_kb", answer_with_kb)

    async def _engage(state):
        return engage_conversation(state | {"knowledge_index": knowledge_index})
    graph.add_node("engage_conversation", _engage)

    async def _follow(state):
        return await follow_up(state | {"llm_client": llm_client, "knowledge_index": knowledge_index})
    graph.add_node("follow_up", _follow)

    graph.add_node("show_capability", lambda s: show_capability(s | {"knowledge_index": knowledge_index}))
    graph.add_node("escalate_to_human", escalate_to_human)
    graph.add_node("finalize_reply", finalize_reply)

    graph.add_edge(START, "load_conversation")
    graph.add_edge("load_conversation", "detect_intent")
    graph.add_edge("detect_intent", "route_intent")

    graph.add_conditional_edges("route_intent", _route_intent, {
        "profile": "finalize_reply",
        "greeting": "describe_capabilities",
        "capabilities": "describe_capabilities",
        "knowledge_qa": "retrieve_knowledge",
        "engage": "engage_conversation",
        "human_handoff": "escalate_to_human",
        "other": "finalize_reply",
    })

    graph.add_edge("describe_capabilities", "finalize_reply")
    graph.add_edge("retrieve_knowledge", "answer_with_kb")
    graph.add_edge("answer_with_kb", "finalize_reply")
    graph.add_edge("engage_conversation", "follow_up")

    graph.add_conditional_edges("follow_up", _after_follow_up, {
        "show_capability": "show_capability",
        "propose_next": "finalize_reply",
        "finalize": "finalize_reply",
    })

    graph.add_edge("show_capability", "finalize_reply")
    graph.add_edge("escalate_to_human", "finalize_reply")
    graph.add_edge("finalize_reply", END)

    return graph.compile(checkpointer=checkpointer)

COMPILED_GRAPH = build_graph()
