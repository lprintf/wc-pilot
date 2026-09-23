# AI 客服展示助手

基于企业微信客服 + LangGraph ReAct 智能体打造的 AI 落地能力展示系统。

面向普通微信用户，通过企业微信客服对话体验知识库问答、获客引流、售后客服、业务咨询与落地评估等 AI 能力。同时作为 [lprintf 团队](https://lprintf.com) 的技术展示和获客入口。

## 架构

```
微信用户 -> 企业微信回调 -> FastAPI (验签解密)
                              |
                    CustomerServiceProcessor
                              |
                    LangGraph ReAct 循环
                    (Agent -> Tools -> Agent -> ...)
                              |
                    +----------+----------+
                    |                     |
            ChatOpenAI (LLM)      GardenKnowledgeSource
            (synscale.onesyn.ai)  (标签倒排索引 + 知识图谱)
                              |
                    企业微信 kf/send_msg -> 微信用户
```

- **Frontend**: React + Vite + TanStack Query，三入口（落地页 / Admin 后台 / 用户中心）
- **Backend**: FastAPI + LangGraph + SQLite
- **Gateway**: Nginx (反向代理 + 静态文件 + 缓存控制)
- **部署**: Docker Compose

## 快速启动

```bash
# 1. 复制环境变量
cp .env.example .env   # 编辑填入 LLM_API_KEY、企微配置等

# 2. 启动全栈（含热重载）
docker compose -f docker-compose.yml -f compose.dev.yaml up -d --build
```

服务就绪后：
- 企业微信回调：`POST /wecom/kf/callback`
- Admin 后台：`/admin/`（Basic Auth，配置 `ADMIN_USERNAME` / `ADMIN_PASSWORD`）
- 用户个人中心：通过微信客服发送"个人中心"获取一次性登录链接

## 功能

| 功能 | 说明 |
|------|------|
| 知识库问答 | 基于 Markdown 数字花园的标签倒排索引 + 知识图谱检索 |
| 获客引流客服 | 自动接待、线索识别、留资引导 |
| 售后客服 | 退换货咨询、物流查询、工单路由 |
| 业务咨询 | FDE 方法论驱动的启发式需求挖掘 |
| 人机协同 | 复杂问题自动转人工 |
| Admin 后台 | 三栏客服工作台：用户列表、对话、信息、日志 |
| 用户中心 | 一次性登录链接，查看客服记录 |

## 目录

```
wechat-bot/
  backend/               # FastAPI + LangGraph
    wechat_bot/
      graph/             # LangGraph ReAct 编排
        graph.py         # 图定义 (_agent, _prepare, _finalize_reply)
        state.py         # CustomerServiceState
        intents.py       # 意图枚举
      garden.py          # GardenKnowledgeSource (标签倒排 + 知识图谱)
      prompts/           # 系统提示词
        customer_service.md
      service.py         # 客服处理编排
      store.py           # SQLite 仓储
      app.py             # FastAPI 应用入口
    garden/
      raw/zh/            # 数字花园 Markdown 节点 (main.md + tags.txt)
        product/         # 产品介绍、能力、行业场景等
        fde/             # FDE 方法论
        compliance/      # 合规
        ai/              # AI 与就业
        software-engineering/  # 软件工程
    skills/              # 子 Agent 技能定义
    tests/
    scripts/             # 基准测试脚本

  frontend/              # React SPA
    src/admin/           # Admin 客服后台
    src/portal/          # 用户个人中心
    src/landing/         # 项目落地页
    src/shared/          # 共享 API 客户端和类型

  nginx/                 # Nginx 配置与 Dockerfile
    nginx.conf

  docs/specs/            # 设计文档
    260916/              # 整体架构与页面设计
    260917/              # Graph 重设计
    260919/              # 花园集成与上下文隔离
    260921/              # 知识迁移差距分析
```

## LLM 容错

- 瞬时故障识别：自动检测 429 / 502 / 503 / 504 及上游超时
- 自动重试：最多额外 2 次，退避 0.5s / 1.5s
- 区分兜底：上游不可用提示"AI 服务暂时繁忙"；其他异常提示通用兜底

## 测试

```bash
cd backend

# 全量测试
uv run python -m unittest discover -s tests -v

# 花园检索测试
uv run python -m unittest tests.test_garden tests.test_garden_retrieval -v

# Graph 测试
uv run python -m unittest tests.test_graph -v

# 基准测试
uv run python scripts/garden_benchmark.py
```

## 欢迎语配置

在企微后台配置以下欢迎语（客户进入客服对话框时自动发送）：

```
你好，我是 AI 客服展示助手。

我可以演示：
1. 知识库问答
2. 获客引流客服
3. 售后客服
4. 业务咨询与落地评估

发送"个人中心"或"我的信息"获取一次性登录链接，查看你的客服记录。

直接说出你的业务场景，例如"我们做电商售后"，我会继续追问，并给出成本与技术可行性建议。
```

关键词路由：`个人中心` / `我的信息` / `我的消息` → 返回一次性登录链接（硬编码，不走 LLM）。
