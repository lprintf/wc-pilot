# 知识库迁移差距分析

> 日期：2026-09-21
> 状态：分析完成，待执行迁移

## 1. 背景与目标

本项目当前有两套知识体系：

- **旧知识库**：`backend/knowledge/*.md`（6 篇，Markdown + YAML tags），
  由 `KnowledgeIndex`（SQLite 分块 + 逐字 keyword 检索）索引，
  作为 `search_knowledge` 工具供 Agent 使用。
- **外部数字花园**：`C:\Users\lprintf\ai-space\blog`（Synapse Garden，55 篇），
  由 `generate_graph.cjs` 编译为倒排索引 + 知识图谱。

目标：用花园替代旧知识库，将花园源码迁入 `backend/garden/raw/zh/`，
由 `GardenKnowledgeSource`（纯内存 JSON 倒排索引 + 图谱解析）替代
`KnowledgeIndex`（SQLite 分块）。本文档量化旧方案与新方案的差距，
给出迁移策略。

## 2. 旧知识库清单

| 文件 | 主题 | 标签 | 面向场景 |
|------|------|------|---------|
| `product.md` | 产品介绍、一句话定位、能力、目标、团队、体验方式 | 产品介绍, 获客引流, 售后, 咨询, 客户关系 | 客户问“你们是谁/能做什么” |
| `capabilities.md` | 获客引流客服、售后客服、知识库问答、人工协同、数据分析 | 能力, 获客引流, 售后, 知识库问答, 人工协同 | 客户问“AI 客服能做什么” |
| `scenarios.md` | 电商、教育、房产、物流、SaaS 等行业痛点和方案 | 场景, 电商, 教育, 房产, 医疗, 零售 | 客户说“我做电商/教育” |
| `pricing.md` | 成本构成、量级分档、影响因素、优化建议 | 定价, 成本, 方案 | 客户问“多少钱/怎么收费” |
| `implementation.md` | PoC → 试点 → 生产化三阶段、需确认信息 | 实施, 交付, 对接 | 客户问“怎么开始/要多久” |
| `faq.md` | 系统能做什么、团队是谁、个人中心、是否编造、对接、安全 | FAQ, 常见问题 | 一般性疑问 |

## 3. 外部花园清单（与 bot 场景相关的部分）

### 3.1 高度相关（15 篇）

| Slug | 标题 | 标签 | 对应场景 |
|------|------|------|---------|
| `software-engineering` | AI 对软件工程的影响 | AI, 软件工程, 软件开发, 本质复杂度, 偶然复杂度, 工程管理, 人月神话, AI辅助编程, 软件生产率 | 行业分析、方法论 |
| `software-engineering/效率陷阱与全能幻觉` | AI 效率陷阱与全能幻觉 | AI, 软件工程, FDE培训, 效率陷阱, 全能幻觉, 黑客松, 产品维护, AI落地, 最小可运营产品 | AI 落地评估 |
| `fde` | FDE | FDE, 企业AI, AI落地, 前沿部署工程师, 业务理解, SOP, 持续迭代, AI应用实施 | FDE 概念介绍 |
| `fde/定义` | FDE 定义 | (无显式 tags) | FDE 概念 |
| `fde/价值衡量` | FDE 价值衡量 | (无显式 tags) | 价值评估 |
| `fde/企业落地方案` | FDE 企业落地方案 | FDE, 企业AI, AI落地, 企业培训, AI咨询, 生产交付 | 落地咨询 |
| `fde/深度与价值` | FDE 的深度与价值 | FDE, AI落地, 软件工程, 业务价值, 场景适配, 技术深度, 企业AI, 产品化, 持续采用 | 企业客户深度咨询 |
| `fde/AI落地难点` | AI 落地难点：先改流程，再接入模型 | 企业AI, FDE, AI落地, 流程重构, 业务指标, 数据治理, 人机协同, 持续运营 | AI 落地痛点 |
| `fde/FDE的问题驱动交付` | FDE 的问题驱动交付 | FDE, 企业AI, 需求挖掘, 软件交付, 问题驱动, 业务价值, 生产部署, 产品化 | 方法论 |
| `fde/工作闭环` | FDE 工作闭环 | (无显式 tags) | 方法论 |
| `fde/能力模型` | FDE 能力模型 | (无显式 tags) | 团队能力 |
| `fde/生态` | FDE 生态 | (无显式 tags) | 生态 |
| `fde/职能边界` | FDE 职能边界 | (无显式 tags) | 角色 |
| `ref/江浙沪中小企业AI需求访谈整理` | 江浙沪中小企业 AI 需求访谈整理 | 中小企业, 企业AI, 需求访谈, FDE, 场景发现, 需求验证, 轻量试点, 人工复核 | AI 需求案例 |
| `ref/个人信息保护法` | 个人信息保护法 | 个人信息保护, 企业AI, 数据合规, 目的限定, 自动化决策, 影响评估 | 合规咨询 |

### 3.2 部分相关（引用材料，可选）

| Slug | 标题 | 对应场景 |
|------|------|---------|
| `ref/NIST AI风险管理框架Playbook` | NIST AI 风险管理框架 Playbook | AI 治理 |
| `ref/Palantir 2025年报AI部署与治理` | Palantir 2025 年报：AI 部署与治理 | 行业案例 |
| `ref/DORA 2025 AI辅助软件开发报告` | DORA 2025 AI 辅助软件开发报告 | 行业报告 |
| `ref/METR AI开发效率研究` | METR AI 开发效率研究 | 效率研究 |
| `ref/RAND AI项目失败研究` | RAND AI 项目失败研究 | 失败案例 |
| `ref/企业黑客松项目延续研究` | 企业黑客松项目延续研究 | 黑客松案例 |
| `ref/黑客松项目延续研究` | 黑客松项目延续研究 | 黑客松案例 |
| `ref/企业全员AI化讽刺短片` | 企业全员 AI 化讽刺短片 | 案例辨析 |
| `ref/Alex Karp关于FDE的问题驱动交付视频` | Alex Karp 关于 FDE 的问题驱动交付视频 | 思想材料 |
| `ref/ILO生成式AI与就业全球分析` | ILO 生成式 AI 与就业全球分析 | 就业研究 |
| `ref/IMF人工智能与工作的未来` | IMF 人工智能与工作的未来 | 就业研究 |
| `ref/人月神话` | 人月神话 | 工程经典 |
| `ref/生成式人工智能服务管理暂行办法` | 生成式人工智能服务管理暂行办法 | 合规 |
| `ai/AI与就业` | AI、就业与分配 | 就业影响 |

### 3.3 无关项（排除）

- `synapse/*`（5 篇）：花园系统文档，客户不需要。
- `ref/lprintf（gopublic）/*`（15 篇）：lprintf 项目特定材料（BP、竞品、成本），非客服场景。
- `about`、`icp`、`ref/iTechnolabs`、`ref/PostHog`、`ref/TopEdu` 等：不相关或引用材料。
- `_mermaid-test`：测试目录。

## 4. 差距分析

基于 12 个真实用例，对两类方案（旧 / 新花园）进行检索对比。
旧方案使用 `KnowledgeIndex`（SQLite 分块 + 逐字 keyword），
新方案使用外部花园编译产物（倒排索引 + 图谱）。

### 4.1 用例对比表

| 用例 | 旧方案首发命中 | 新方案首发命中 | 胜出 | 分析 |
|------|--------------|--------------|------|------|
| 产品介绍 | ✅ faq/product | ❌ 无 | 旧 | 新花园没有产品介绍类标签 |
| 获客引流 | ✅ capabilities | ❌ 无 | 旧 | 同上，缺少获客引流等 Bot 特有标签 |
| 电商售后 | ✅ scenarios | ❌ 无 | 旧 | 行业场景缺失 |
| 教育行业 | ✅ capabilities | ⚠️ 效率陷阱（1 条） | 旧 | 花园无教育标签 |
| FDE定义 | ❌ capabilities/pricing（噪声） | ✅ fde/定义/深度/AI落地难点 | 新 | 旧方案无 FDE 词条，逐字匹配不收敛 |
| AI落地难点 | ❌ scenarios/pricing（噪声） | ✅ fde/AI落地难点 | 新 | 旧方案缺乏深度方法论 |
| AI落地成本 | ✅ pricing | ⚠️ gopublic/cost-model（无关） | 旧 | 花园成本文章是 lprintf 项目特定的 |
| 人月神话 | ❌ pricing/scenarios（噪声） | ✅ software-engineering | 新 | 旧方案无工程经典文章 |
| AI对就业影响 | ❌ capabilities/faq（噪声） | ✅ ai/AI与就业 + 多篇引用 | 新 | 花园有完整就业研究 |
| 个人信息合规 | ❌ implementation/scenarios（噪声） | ✅ 个人信息保护法 | 新 | 花园有合规专题 |
| 知识库问答 | ⚠️ implementation/pricing（部分相关） | ⚠️ Synapse（花园系统） | — | 双方都不完全对 |
| 数字花园维护 | ❌ pricing（噪声） | ✅ Synapse 系列 | — | Bot 场景不需要花园维护知识 |

### 4.2 量化总结

- 旧方案胜出 4 例（产品/获客/电商/教育）：**Bot 特有知识**覆盖好，但**噪声严重**（大量不相关文章因逐字交叉命中进入结果）。
- 新方案胜出 5 例（FDE/难点/人月神话/就业/合规）：**专业深度**极强，精确命中，无噪声。
- 双方均不理想 3 例（成本/知识库问答/花园维护）：成本旧较好、知识库答非所问、花园维护不需要。

### 4.3 结构性差距

| 维度 | 旧方案 | 新方案（外部花园） |
|------|--------|-----------------|
| 产品介绍 / 能力说明 | ✅ 好 | ❌ 无 |
| 行业场景方案 | ✅ 好（电商/教育等） | ❌ 无 |
| 定价与成本 | ✅ 好（Bot 特定） | ❌ 无关（lprintf 商业计划） |
| 实施路径 | ✅ 好（PoC/试点/生产） | ❌ 无 |
| FAQ / 运维 | ✅ 好 | ❌ 无 |
| FDE 方法论 | ❌ 无 | ✅ 深度 |
| AI 落地痛点分析 | ❌ 无 | ✅ 深度 |
| 软件工程经典 | ❌ 无 | ✅ 有（人月神话/AI 影响） |
| 就业 / 合规 / 行业报告 | ❌ 无 | ✅ 丰富 |
| 检索精度 | ❌ 低（逐字交叉命中噪声多） | ✅ 高（标签精确匹配） |
| 检索覆盖度 | ❌ 窄（仅 6 篇 Bot 文档） | ⚠️ 宽但不全 |

## 5. 结论：必须合并，各取所长

- 外部花园**不能直接替代**旧知识库：产品介绍、场景、定价、实施、FAQ 全部缺失。
- 旧知识库**信息量太浅**：无法支撑“专业顾问”的 Agent 对话深度。

因此唯一合理的路径：**在花园结构内新增 Bot 专属分类，补全旧知识库内容**。
旧方案无保留价值 —— `KnowledgeIndex` 的逐字切分和 SQLite 分块在精度、维护性和
上下文控制上明显弱于花园的标签倒排 + 图谱方案。

## 6. 新花园目录设计

```
backend/garden/raw/zh/
├── product/                    # Bot 专属：产品与能力（从旧知识库迁移）
│   ├── overview/main.md        # 产品介绍（合并 product.md 核心内容）
│   ├── capabilities/main.md    # 能力详情（合并 capabilities.md）
│   ├── scenarios/main.md       # 行业场景（合并 scenarios.md）
│   ├── pricing/main.md         # 定价与成本（合并 pricing.md）
│   ├── implementation/main.md  # 实施路径（合并 implementation.md）
│   └── faq/main.md             # 常见问题（合并 faq.md）
│
├── fde/                        # 从花园迁移（高度相关）
│   ├── main.md                 # FDE 索引
│   ├── AI落地难点/main.md
│   ├── FDE的问题驱动交付/main.md
│   ├── 定义/main.md
│   ├── 价值衡量/main.md
│   ├── 深度与价值/main.md
│   ├── 企业落地方案/main.md
│   ├── 工作闭环/main.md
│   ├── 生态/main.md
│   ├── 职能边界/main.md
│   └── 能力模型/main.md
│
├── software-engineering/       # 从花园迁移（高度相关）
│   ├── main.md
│   └── 效率陷阱与全能幻觉/main.md
│
├── ref/                        # 从花园迁移（引用材料，部分相关）
│   ├── 江浙沪中小企业AI需求访谈整理/main.md
│   ├── 个人信息保护法/main.md
│   ├── AI与就业/main.md
│   ├── 人月神话/main.md
│   ├── NIST AI风险管理框架Playbook/main.md
│   ├── Palantir 2025年报AI部署与治理/main.md
│   ├── 企业黑客松项目延续研究/main.md
│   ├── 企业全员AI化讽刺短片/main.md
│   └── ... （其他引用文章酌情保留）
│
└── garden.json                 # 花园配置
```

## 7. 迁移步骤

1. **创建目录** `backend/garden/raw/zh/product/`。
2. **整理旧知识库内容**为 6 篇新文章（`product/{overview,capabilities,scenarios,pricing,implementation,faq}`），
   每篇补充 `tags.txt`（`human:` 和 `ai:` 标签）。
3. **从外部花园复制** `fde/`、`software-engineering/`、`ref/` 中标记为“高度相关”和“部分相关”的文章。
4. **排除** `synapse/`、`ref/lprintf（gopublic）/`、`about`、`icp` 等无关目录。
5. **实现** `GardenKnowledgeSource`（`backend/wechat_bot/garden.py`），
   扫描 `raw/zh` 编译为内存索引。
6. **替换** `search_knowledge` 工具为 `search_garden` + `read_garden_note`。
7. **添加** `trim_messages`。删除旧 `KnowledgeIndex` 及其依赖。
8. **运行对比脚本** `backend/scripts/knowledge_gap_report.py`，
   验证新索引在原用例上的覆盖率是否达标。

## 8. 附录：对比脚本

`backend/scripts/knowledge_gap_report.py` 和 `docs/specs/260921/gap-results.json`
包含完整对比数据和可重复运行的代码。迁移完成后重新运行，
预期所有 12 个用例在新花园索引下均有高质量首发命中。