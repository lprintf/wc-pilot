# 专属花园基准测试报告
> 快照：`2026-09-21T152944Z`

## 用例覆盖

| 用例 | 旧方案首发 | 新花园首发（score） |
|------|-----------|-------------------|
| 产品介绍 | faq.md | product/overview (3) |
| 获客引流 | capabilities.md | product/lead-gen (4) |
| 电商售后 | capabilities.md | product/scenarios/ecommerce (4) |
| 教育行业 | capabilities.md | product/scenarios/education (2) |
| FDE定义 | capabilities.md | fde (3) |
| AI落地难点 | scenarios.md | fde/ai-landing-difficulties (4) |
| AI落地成本 | pricing.md | product/cost (2) |
| 人月神话 | pricing.md | software-engineering/human-month (2) |
| AI对就业影响 | capabilities.md | ai/employment (4) |
| 个人信息合规 | implementation.md | compliance/personal-information (3) |
| 知识库问答 | implementation.md | product/knowledge-qa (4) |
| 数字花园维护 | pricing.md | (none) |

## 详细结果

### 产品介绍
terms: `产品介绍, 系统能力, 能做什么`

**新花园**
- `product/overview` _产品介绍：微信客服智能体_ score=3 back=3
- `product/about` _lprintf 团队_ score=1 back=3
- `product/capabilities` _系统能力总览_ score=1 back=6

**旧方案**
- `faq.md` _这个系统能做什么？_
- `product.md` _产品介绍：微信客服智能体_
- `scenarios.md` _SaaS / 互联网行业_
- `capabilities.md` _获客引流客服_
- `capabilities.md` _售后客服_

### 获客引流
terms: `获客引流, 线索, 微信客服`

**新花园**
- `product/lead-gen` _获客引流客服_ score=4 back=6
- `product/capabilities` _系统能力总览_ score=1 back=6
- `product/scenarios/education` _教育培训行业_ score=1 back=0
- `product/overview` _产品介绍：微信客服智能体_ score=1 back=3

**旧方案**
- `capabilities.md` _获客引流客服_
- `product.md` _产品能做什么_
- `faq.md` _这个系统能做什么？_
- `capabilities.md` _售后客服_
- `product.md` _产品目标_

### 电商售后
terms: `电商, 售后, 退换货, 物流`

**新花园**
- `product/scenarios/ecommerce` _电商行业_ score=4 back=0
- `product/after-sales` _售后客服_ score=3 back=7
- `product/scenarios` _行业场景总览_ score=2 back=7
- `product/capabilities` _系统能力总览_ score=1 back=6
- `product/scenarios/logistics` _物流快递行业_ score=1 back=0

**旧方案**
- `capabilities.md` _售后客服_
- `product.md` _如何体验_
- `scenarios.md` _电商行业_
- `product.md` _产品能做什么_
- `product.md` _产品目标_

### 教育行业
terms: `教育, 培训, 获客`

**新花园**
- `product/scenarios/education` _教育培训行业_ score=2 back=0
- `product/scenarios` _行业场景总览_ score=1 back=7
- `product/capabilities` _系统能力总览_ score=1 back=6
- `product/lead-gen` _获客引流客服_ score=1 back=6

**旧方案**
- `capabilities.md` _获客引流客服_
- `product.md` _产品能做什么_
- `faq.md` _这个系统能做什么？_
- `product.md` _产品目标_
- `product.md` _如何体验_

### FDE定义
terms: `FDE, 定义, AI落地`

**新花园**
- `fde` _FDE 概述_ score=3 back=0
- `fde/definition` _FDE 定义_ score=2 back=2
- `fde/capability-model` _FDE 能力模型_ score=1 back=2
- `fde/ecosystem` _FDE 生态_ score=1 back=1
- `fde/enterprise-landing` _FDE 企业落地方案_ score=1 back=4

**旧方案**
- `capabilities.md` _数据分析与优化_
- `implementation.md` _阶段三：生产化_
- `pricing.md` _成本构成_
- `pricing.md` _量级分档（Demo 估算）_

### AI落地难点
terms: `AI落地, 难点, 流程, 数据`

**新花园**
- `fde/ai-landing-difficulties` _AI 落地难点_ score=4 back=5
- `ai/employment` _AI、就业与分配_ score=1 back=0
- `software-engineering/ai-impact` _AI 对软件工程的影响_ score=1 back=1
- `fde` _FDE 概述_ score=1 back=0
- `product/consulting` _业务咨询与落地评估_ score=1 back=15

**旧方案**
- `scenarios.md` _物流 / 快递行业_
- `capabilities.md` _售后客服_
- `pricing.md` _成本构成_
- `pricing.md` _量级分档（Demo 估算）_
- `product.md` _产品能做什么_

### AI落地成本
terms: `成本, LLM, 开发, 运维`

**新花园**
- `product/cost` _成本估算_ score=2 back=5
- `product/consulting` _业务咨询与落地评估_ score=1 back=15
- `software-engineering/human-month` _人月神话_ score=1 back=1
- `software-engineering/ai-impact` _AI 对软件工程的影响_ score=1 back=1

**旧方案**
- `pricing.md` _成本构成_
- `pricing.md` _量级分档（Demo 估算）_
- `capabilities.md` _获客引流客服_
- `implementation.md` _阶段二：小范围试点_
- `pricing.md` _成本估算模型_

### 人月神话
terms: `人月神话, 软件工程`

**新花园**
- `software-engineering/human-month` _人月神话_ score=2 back=1
- `software-engineering/ai-impact` _AI 对软件工程的影响_ score=1 back=1

**旧方案**
- `pricing.md` _量级分档（Demo 估算）_
- `scenarios.md` _电商行业_
- `capabilities.md` _获客引流客服_
- `capabilities.md` _售后客服_
- `capabilities.md` _人工协同_

### AI对就业影响
terms: `AI, 就业, 分配, 劳动市场`

**新花园**
- `ai/employment` _AI、就业与分配_ score=4 back=0
- `software-engineering/ai-impact` _AI 对软件工程的影响_ score=2 back=1
- `fde` _FDE 概述_ score=2 back=0
- `compliance/generative-ai-regulation` _生成式 AI 服务管理_ score=1 back=1
- `fde/ai-landing-difficulties` _AI 落地难点_ score=1 back=5

**旧方案**
- `capabilities.md` _人工协同_
- `capabilities.md` _售后客服_
- `capabilities.md` _知识库问答_
- `faq.md` _这个系统能做什么？_
- `product.md` _一句话定位_

### 个人信息合规
terms: `个人信息, 合规, 数据`

**新花园**
- `compliance/personal-information` _个人信息保护与数据合规_ score=3 back=2
- `compliance/generative-ai-regulation` _生成式 AI 服务管理_ score=2 back=1
- `fde/ai-landing-difficulties` _AI 落地难点_ score=2 back=5
- `product/after-sales` _售后客服_ score=1 back=7
- `product/lead-gen` _获客引流客服_ score=1 back=6

**旧方案**
- `implementation.md` _阶段一：PoC 验证_
- `capabilities.md` _获客引流客服_
- `product.md` _产品能做什么_
- `scenarios.md` _电商行业_
- `implementation.md` _阶段二：小范围试点_

### 知识库问答
terms: `知识库, 问答, 文档, 检索`

**新花园**
- `product/knowledge-qa` _知识库问答_ score=4 back=3
- `product/capabilities` _系统能力总览_ score=2 back=6

**旧方案**
- `implementation.md` _阶段一：PoC 验证_
- `pricing.md` _成本构成_
- `capabilities.md` _售后客服_
- `faq.md` _AI 客服会编造答案吗？_
- `pricing.md` _优化建议_

### 数字花园维护
terms: `Synapse, 数字花园, 内容维护`

**新花园**
- (none)

**旧方案**
- `pricing.md` _成本构成_
- `pricing.md` _量级分档（Demo 估算）_
- `capabilities.md` _售后客服_
- `capabilities.md` _获客引流客服_
- `faq.md` _数据安全吗？_
