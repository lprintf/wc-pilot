# 客服智能体对话设计 —— 启发式需求发现（修订版）

> 取代填表式流程，改为 LLM 驱动的自然对话。

## 重要边界

### 欢迎语在企微配置，不在代码里

客户进入客服对话框时，**企微自动发送**欢迎语。智能体只处理客户主动发来的第一条消息
及后续对话。代码中的 `WELCOME_MESSAGE` 常量不应再被当作"智能体发送欢迎语"使用。

**企微欢迎语配置内容：**

```
你好，我是 AI 客服展示助手。

我可以帮你演示：
1. 知识库问答
2. 获客引流客服
3. 售后客服
4. 业务咨询与落地评估

发送"个人中心"获取一次性登录链接，查看你的客服记录。

直接说出你的业务场景，比如"我们做电商售后"，我会和你一起分析。
```

### 允许模型自由发挥

**不要用 `if/else` 硬编码"该说什么、不该说什么"。**

Graph 的职责是提供**上下文、工具和状态**，对话节奏和内容交给 LLM 判断。
判断型字段（如 `should_show_case`、`should_propose`）只作为 Graph 的路由提示，
不作为 LLM 必须遵循的硬约束。LLM 可以根据实际情况自由决定回复内容和调用工具。

**原则**：

- 不确定时，给 LLM 更多自由，而不是更多规则
- Graph 节点负责"把信息送到 LLM 面前"，LLM 负责"怎么说话"
- 我们只约束安全和边界（不报价、不承诺、不泄露敏感信息），不约束表达方式

## 目录结构建议

```
backend/
├── knowledge/          # 知识库 MD（事实性内容：能力、定价、FAQ、案例）
├── prompts/            # LLM 系统提示词目录
│   ├── customer_service.md      # 主客服智能体 system prompt
│   ├── follow_up.md             # 追问/启发式对话 prompt
│   └── intent_classifier.md     # 意图识别 prompt
└── skills/             # 智能体可调用的能力定义
    ├── lead_gen.md              # 获客引流咨询 skill
    ├── after_sales.md           # 售后咨询 skill
    ├── knowledge_qa.md          # 知识库问答 skill
    └── business_analysis.md     # 业务分析与落地建议 skill
```

**为什么不把 skills 和 knowledge 放一起？**

- `knowledge/` 是**静态事实**（"我们的定价是..."），给 RAG 检索
- `skills/` 是**程序性指令**（"如何引导客户聊出痛点"），给 LLM 做行为参考
- `prompts/` 是**角色定义**（"你是谁、怎么说话、边界在哪"），直接注入 system prompt

三者职责不同，分开维护更清晰。

## 问题诊断

当前 graph 的问题：

- `start_discovery` 一次性列出 5 题 → 客户感觉在填表
- `collect_business_facts` 逐行解析 → 无法理解自然语言
- `estimate_cost_feasibility` 模板化输出 → 没有专业感
- `welcome_customer` 节点重复欢迎 → 企微已经自动发了欢迎语

## 设计原则

1. **客户为中心**——关心客户的业务和困难，不索取字段
2. **循循善诱**——每次只问一个问题，自然延续
3. **展示能力**——在对话中自然穿插案例和能力
4. **允许模糊**——"不太清楚"、"先看看"都是合法状态
5. **自由发挥**——Graph 只提供上下文，LLM 决定说什么

## 新 Graph 节点设计

### 保留
- `detect_intent`（LLM 意图识别）
- `retrieve_knowledge` / `answer_with_kb`（知识库问答）
- `describe_capabilities`（能力概览）
- `escalate_to_human`（转人工）
- `finalize_reply`（组装回复）
- `profile` 命令

### 废弃
- `welcome_customer`（企微已自动发送欢迎语）
- `start_discovery`（填表式）
- `collect_business_facts`（逐行解析）
- `estimate_cost_feasibility`（模板化）

### 新增

| 节点 | 说明 |
|---|---|
| `engage_conversation` | 客户首次表达业务意向时，自然破冰 + 开启对话 |
| `follow_up` | **核心**。LLM 分析回复，更新 business_profile，生成下一轮自然追问或展示 |
| `show_capability` | 从 knowledge 检索相关案例，给 LLM 做参考，由 LLM 自然引用 |
| `propose_next` | 时机成熟时提出落地建议（LLM 判断时机，Graph 不强制） |

## State 模型

```python
class CustomerServiceState(TypedDict, total=False):
    user_id: int
    conversation_id: int
    incoming_messages: list[dict[str, Any]]
    history: list[dict[str, Any]]
    intent: str
    reply_text: str

    business_profile: dict[str, str]  # LLM 动态积累
    conversation_round: int
    knowledge_chunks: list[dict]      # 检索到的参考材料
    matched_skills: list[str]         # 命中的 skills
```

`business_profile` 是动态的，LLM 自己决定存什么 key。

## LLM Prompt 策略

主 system prompt 放在 `prompts/customer_service.md`，包含：

- 角色：AI 客服展示助手
- 知识：了解获客引流、售后、知识库问答、人机协同
- 风格：专业、亲切、循循善诱、不硬推销
- 能力：可引用知识库案例和能力
- 边界：不报价、不承诺效果、不泄露敏感信息

Graph 节点只负责：
1. 检索 knowledge 和 skills 中相关的内容
2. 把这些内容作为上下文注入 LLM
3. 把 LLM 的输出写回 state

LLM 自由决定怎么组织语言、问什么问题、展示什么。

## 多轮持久化

通过已有的 `AsyncSqliteSaver` checkpoint 自动持久化。
每轮只更新增量信息。

## Admin 展示

- `business_profile` 全量
- 对话轮次
- 命中的 skills
- 引用的 knowledge

## 实施优先级

1. **P0**：`prompts/` 目录 + `engage_conversation` + `follow_up` + LLM 集成
2. **P1**：`skills/` 目录 + `show_capability` + Admin 展示
3. **P2**：`propose_next` + 端到端验收
