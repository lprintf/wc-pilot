# 数字花园集成与上下文隔离设计

> 日期：2026-09-19
> 状态：已确认，待实施
> 修订：v3 — 纠正检索流程描述

## 1. 背景

本项目（wechat-bot）需要接入外部数字花园 `C:\Users\lprintf\ai-space\blog`（Synapse Garden），
为微信客服智能体提供专业领域知识检索能力。

外部花园是一个人类可维护的 Markdown 知识库，使用 `[[双向链接]]`（Obsidian 风格）。
Synapse 的 `generate_graph.cjs` 在编译时产生以下 AI 可消费产物：

| 文件 | 用途 |
|------|------|
| `nginx/static/ai/zh/**/*.md` | 完整文章（正文 + 相关链接 + 反向链接段落） |
| `nginx/static/search_index_zh.json` | 倒排索引 `{slug_dict: [...], index: {tag: [slug_idx...]}}` |
| `nginx/static/catalog_zh.json` | 文章目录 `[{slug, title}]` |
| `nginx/static/knowledge-graph_zh.json` | 知识图谱 `{nodes, links, backlinks}` |

当前 bot 已有 `KnowledgeIndex`（`backend/wechat_bot/graph/knowledge.py`），
但只支持扁平 `*.md` 扫描 + H2 分块 + SQLite keyword 检索，
无法处理嵌套目录、`[[链接]]` 解析和图谱遍历。

## 2. 核心决策

### 2.1 不做 MCP，用内部 GardenKnowledgeSource

MCP（Model Context Protocol）解决的是工具跨进程/跨智能体复用问题，
不解决 `[[双向链接]]` 噪声和上下文膨胀问题。

| 方案 | 适用场景 | 当前选择 |
|------|---------|---------|
| 内部工具 | 仅当前 bot 使用 | ✅ 现在 |
| MCP Server | 多个 agent/前端共享 | 以后可选 |

### 2.2 不做分块索引，复用 Synapse 编译产物

数字花园的设计初衷是用**链接结构**代替机械分块——文章是完整的思想单元，
`[[双向链接]]` 和 tags 才是检索线索，而非被切碎的 H2 段落。

Synapse 编译时已产出可直接消费的倒排索引（`search_index_zh.json`），
无需重建。`GardenKnowledgeSource` 不做任何正文切分：

- **检索**：加载 `search_index_zh.json` 倒排索引，agent 关键词命中 tag → slug 列表。
- **上下文**：直接使用 `knowledge-graph_zh.json` 节点/边/反链，不新建图。
- **正文**：需要时从 `ai/zh/{slug}.md` 取回完整文章，保持原样，不截断。

### 2.3 不引入向量数据库

顺提：向量库对“频繁人工更新 MD”场景精度不如确定性组合拳，且增加运维复杂度。

### 2.4 源目录选择

选用 `nginx/static/ai/zh/*.md` 作为正文源，搭配已有 JSON 编译产物。
内容更新只需在花园 repo 运行 `generate_graph.cjs` 后重启 bot。

## 3. GardenKnowledgeSource 设计

### 3.1 数据模型

完全围绕 Synapse 编译产物，无自有存储：

```
GardenKnowledgeSource（纯内存 + 文件读取）
├── _slug_dict: list[str]                     # 来自 search_index_zh.json
├── _inverted_index: dict[str, list[int]]      # tag → slug_dict 下标
├── _catalog: dict[slug, title]                # 来自 catalog_zh.json
├── _nodes: dict[slug → name, aliases, group]  # 来自 knowledge-graph_zh.json
├── _outgoing: dict[slug → list[slug]]         # links 推导
├── _backlinks: dict[slug → list[slug]]        # 来自 knowledge-graph_zh.json
└── _articles_dir: Path                        # ai/zh/ 文章目录
```

**无 SQLite。无分块。4 个 JSON 文件合计约 70KB，纯内存加载。**

### 3.2 倒排索引与检索

`search_index_zh.json` 由 `generate_graph.cjs` 在第 4 遍归约中生成：

```
for each article:
  for each tag in meta.tags:        ← tags 来自 tags.txt 的 human: + ai: 行
    inverted_index[tag].push(slug_id)
```

即倒排索引的 key 是花园维护者录入的标签词条。

**搜索流程**：

```
search_garden(query)
  ├── agent 提供 query 关键词（空格分隔，如"软件工程 人月神话"）
  ├── 每个关键词在 _inverted_index 中匹配 key：
  │     - 完全命中优先
  │     - 向后备降：部分命中（关键词是 key 的字串，或 key 是关键词的字串）
  ├── 合并所有命中的 slug_id，按命中次数降序排序
  ├── 取 top_n，从 _catalog 解析 title
  ├── 从 _nodes 聚合 tags（group 字段）
  ├── 从 _outgoing 获取 outgoing_links（只取邻居 title）
  ├── 从 _backlinks 获取 backlink_count
  └── 若入参 tags 不为空，仅保留 nodes.group 匹配的项（补充过滤）
```

返回：`[{slug, title, tags, outgoing_links, backlink_count}]`，不含正文片段。

### 3.3 文章阅读

```
read_garden_note(slug)
  ├── 读取 ai/zh/{slug}.md 全文
  ├── 剥离末尾「## 相关链接」「## 反向链接」段落
  ├── 正文中 [[A|B]] → 用 _nodes[A].name 或 B 替换为可读文本
  └── 返回正文（含挖空说明）+ outgoing / backlink 按需附带
```

### 3.4 检索语义总结

- Agent 决定什么时候搜、搜什么词。tags 只是按领域（group）过滤的补充维度。
- 倒排索引 = 花园维护者录入的关键词标签，确定性匹配，无需分词器。
- 文章不切不分不摘要。模型确认相关后主动调 `read_garden_note` 展开全文。

## 4. 工具合约

```python
@tool
def search_garden(query: str, tags: str = "", top_n: int = 3) -> str:
    """检索数字花园。query 用 3-5 个关键词空格分隔；
       tags 逗号分隔（如 FDE,软件工程）。
       返回每篇的 slug / title / tags / 邻居链接 / 反链数量，不含正文。"""

@tool
def read_garden_note(slug: str) -> str:
    """读取数字花园中的完整文章（已解析 [[链接]]）。slug 如 fde/AI落地难点。"""
```

## 5. 上下文隔离

### 5.1 当前问题

- Agent 的 `messages` 通过 LangGraph checkpointer 按 `thread_id` 永久累积。
- 工具输出散落在消息历史中，随对话轮次膨胀。
- 目前无任何裁剪或摘要机制。

### 5.2 分层策略

**第一层：工具输出天然克制（已满足）**

`search_garden` 返回结构化摘要，不含正文片段。
`read_garden_note` 返回单篇正文（已剥离 backlinks），已是完整文章，模型有意选择读取说明有需求。

**第二层：消息裁剪（实施）**

在 `_agent` 调用模型前，对 `state["messages"]` 做裁剪：

```python
from langchain_core.messages import trim_messages

trimmed = trim_messages(
    state["messages"],
    max_tokens=6000,
    strategy="last",
    token_counter=model,
    include_system=True,
)
```

- 裁剪后的消息仅用于本轮模型调用，不写回 state。
- `business_profile` 始终通过 system prompt 注入，不依赖历史原文。

**第三层：Sub-Agent 深检索（后置）**

当模型需要多跳跨文档探索，可用 `garden_research` 子 agent 隔离上下文。
触发条件：模型连续调用 ≥3 次 `search_garden` 或 ≥2 次 `read_garden_note`。
暂不实施（参见 `docs/specs/260918/context-and-subagents.md`）。

### 5.3 对比

| 层级 | 改动量 | 收益 | 风险 |
|------|--------|------|------|
| 工具输出契约 | 无额外（设计自带） | 立竿见影 | — |
| trim_messages | 中（agent 节点加裁剪） | 控制历史膨胀 | 可能丢失前文中客户重要表述 |
| Sub-Agent | 大（新增 subgraph + tool） | 隔离深检索 | 增加延迟与复杂度 |

第一层已内置，实施只做第二层。

## 6. 提示词更新

`backend/prompts/customer_service.md` 需增加花园检索指引：

```
## 知识来源

你有两个知识来源：
- search_knowledge：产品介绍、定价、实施方式等 bot 自身知识
- search_garden：数字花园文章，覆盖软件工程、FDE、AI 落地、行业报告等专业领域

当客户询问行业分析、方法论、案例参考等深度问题时，优先检索花园。
若首次未命中，换角度重新提炼关键词。
search_garden 只返回摘要，确认相关后可用 read_garden_note 展开全文。
```

## 7. 实施计划

1. 实现 `GardenKnowledgeSource`（`backend/wechat_bot/graph/garden.py`）
   - 加载 4 个 JSON → 倒排查找 + 图谱解析 + 文章读取
   - 单元测试
2. 在 `graph.py` 注册 `search_garden` + `read_garden_note` 工具
3. 更新 system prompt
4. 加 `trim_messages` 到 agent 节点
5. 打桩验证：日志确认花园命中率和裁剪效果

## 8. 未来可选

- MCP 化：将 `GardenKnowledgeSource` 暴露为 MCP server（`langchain-mcp-adapters`），
  供 Codex、前端等其他消费者使用。
- `garden_backlinks(slug)` 独立工具：按需查看反向链接详情。
- 花园更新自动重索引：watch 目录或提供 admin API 触发。