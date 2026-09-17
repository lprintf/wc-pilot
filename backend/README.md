# 企业微信客服 LLM PoC 后端

当前已实现完整的微信客服事件回调链路：验签解密、`sync_msg` 拉取、SQLite cursor/消息幂等、OpenAI 兼容 LLM 调用和 `send_msg` 回复。首次启动只建立 cursor 基线，不会回复历史消息。

容器运行后提供以下端点：

## LangGraph 对话编排

消息到达后不再直接调用 LLM，而是进入 LangGraph 有状态图：

```
START -> load_conversation -> detect_intent -> route_intent
  -> greeting     (welcome_customer)
  -> capabilities (describe_capabilities)
  -> knowledge_qa (retrieve_knowledge -> answer_with_kb)
  -> lead_gen / after_sales (start_discovery -> collect_business_facts)
  -> business_discovery (collect_business_facts -> estimate_cost_feasibility)
  -> human_handoff (escalate_to_human)
  -> profile (login link)
  -> other (finalize_reply)
  -> END
```

意图识别除 `个人中心/我的信息/我的消息/查看记录` 硬编码外，全部交给 LLM 分类。多轮业务信息采集（行业、渠道、日咨
询量、痛点、目标）通过 AsyncSqliteSaver checkpoint 跨轮持久化，Admin 后台可查看进度和估算结果。

知识库 MD 文件位于 `backend/knowledge/*.md`，启动时自动索引。
Admin 后台新增 `GET /api/admin/logs?lines=200&level=INFO` 日志查看接口。

- `GET /health/live`
- `GET /health/ready`
- `GET|POST /wecom/kf/callback`

启动时若企业微信接口暂时不可用，后端会按 5、10、20、40、60 秒的间隔重试客服账号和消息游标初始化，之后每 60 秒重试，成功后自动恢复，无需重启。`/health/ready` 在初始化完成前返回 503，并提供脱敏后的接口名和错误码；若错误码为 `60020`，请检查应用的可信 IP 是否包含后端当前公网出口 IP。已有消息游标会保留，首次建立游标仍不会回复历史消息。

独立 LLM 烟测脚本只调用 LLM，不读取或回复企业微信消息：

```powershell
uv sync
uv run python scripts/test_llm_api.py
```

也可以传入自定义问题：

```powershell
uv run python scripts/test_llm_api.py "请用一句话介绍你自己"
```

脚本从仓库根目录 `.env` 读取 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL`。`LLM_BASE_URL` 应包含兼容 API 的版本路径，例如 `https://example.com/v1`，也可以直接填写完整的 `/chat/completions` 地址。

## 用户中心登录链路

开发环境启动、源码挂载、热重载和容器脚本命令见 [Compose 开发说明](../docs/COMPOSE_DEV.md)。

客户发送以下任一文本指令时，机器人不会调用 LLM，而是返回一个 10 分钟内有效、只能使用一次的个人中心登录链接：

- `我的信息`
- `我的消息`
- `个人中心`
- `查看记录`

同一用户每分钟最多生成一个登录链接；短时间内重复发送上述任一指令时，应继续使用刚才收到的链接。

链接使用 `PUBLIC_BASE_URL` 生成，该配置必须是 HTTPS 地址。票据和网站 Session 都使用 256 bit 随机值，数据库只保存哈希；登录链接明文不会写入客服会话历史。

当前已提供：

- `GET /auth/t/{ticket}`：消费一次性票据，设置 `HttpOnly`、`Secure`、`SameSite=Lax` Session Cookie，然后跳转到 `/me`。
- `GET /me`：适配微信内置浏览器的移动端用户中心，展示客户资料、真实客服问答、消息时间和回复处理时间。
- `GET /api/me`：根据服务端 Session 返回当前用户的昵称、头像和性别，不接收或返回 `external_userid`。
- `GET /api/me/conversations`：列出当前用户在本系统保存的微信客服会话。
- `GET /api/me/conversations/{id}`：返回当前用户指定会话的客户消息、AI 回复及 UTC 时间戳。

第一版仍未包含登出、数据导出和删除接口。

## 客服后台 Demo

配置 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 后，访问 `/admin` 可打开三栏客服后台。后台使用 HTTPS 下的 HTTP Basic 认证，服务端只接受配置的单一客服账号；未配置管理员凭据时相关接口返回 `admin_not_configured`。

导航中的「客户管理」提供独立的分页列表、昵称/内部 ID 搜索、活跃时间/未读排序、资料详情和进入会话。详情支持编辑本系统显示昵称、性别、备注和标签；编辑记录单独保存，不会被微信资料同步覆盖，也不修改微信账号资料。标签最多 10 个，每个最多 20 字，备注最多 2000 字。

「查询企业微信 ID」单独调用 `GET /api/admin/users/{user_id}/identity`，仅管理员可以查询当前客服账号下的客户，响应禁止缓存；普通列表和会话详情不会返回外部身份 ID。编辑接口为 `PATCH /api/admin/users/{user_id}`，需要完整提交 `nickname`、`gender`、`notes`、`tags`。

删除接口为 `DELETE /api/admin/users/{user_id}`，请求体需包含匹配的 `confirm_user_id`。编辑和删除均校验 CSRF 请求头。删除会移除本系统客户资料、备注、标签、历史正文和登录会话；为防止重复回调再次发送、或删除后获得额外额度，保留无正文的消息去重及额度记录。客户发来新消息后会重新建立客户资料。存在 pending 消息或跨账号关联时拒绝删除；自动回复中的客户会等待当前处理结束。页面要求输入客户内部 ID 才能确认删除。

后台接口包括：

- `GET /api/admin/users?q=&page=&page_size=&sort=`：按昵称或内部 `user_id` 搜索、分页和排序，返回未读数及最近消息摘要。
- `GET /api/admin/users/{user_id}`：返回安全范围内的客户资料。
- `GET /api/admin/users/{user_id}/conversation?before_id=&limit=`：按稳定消息 ID 分页读取会话，并标记已读。
- `POST /api/admin/users/{user_id}/messages`：发送人工文本。请求必须带客户端 `request_id`；重复提交只返回原消息。发送失败会保留 `failed` 状态，可带 `retry_message_id` 明确重试。

发送接口同时要求 `X-Requested-With: XMLHttpRequest` 请求头作为 Demo 级 CSRF 防护；生产环境还应补充基于 Session/CSRF Token 的完整方案。

人工消息会先以 `pending` 写入统一 `conversation_message` 表，再调用企业微信 `kf/send_msg`，成功或失败后更新状态；操作员标识和时间一并保留。数据库启动时会将旧 `processed_message` 中的 AI 对话回填到统一消息表。

所有自动回复（包括登录链接、限流提示和兜底回复）及人工回复都会在同一条文本末尾附加提示，例如：

```text
---
客服剩余回复次数4，约48小时后清空，回复任意消息重置。
```

依据[企业微信发送消息规则](https://developer.work.weixin.qq.com/document/path/94677)，客户发送消息后可在 48 小时内回复最多 5 条。提示显示发送本条后的剩余次数，剩余时长从最新客户消息的发送时间计算；超时会清空额度，客户发送新消息才会重新获得额度。非文本客户消息也会刷新额度。额度按客服账号和客户隔离并持久化，AI 和人工回复共用；发送失败不扣次数，重复回调不重置额度。历史数据首次按已有收发记录估算，同秒事件可能保守多计；服务重启保留之后的计数。

发送时为提示预留空间，正文过长会按 UTF-8 字节截断并加省略号，总长不超过企业微信的 2048 字节限制。提示不写入 LLM 对话上下文，登录链接继续在历史记录中隐藏。后台发送遇到窗口过期或额度用尽（含企业微信错误码 `95002`、`95001`）返回 `409` 和明确原因，需要客户发来新消息后才能重试；其他发送故障仍返回 `502`，日志包含企业微信错误码供排查。自动回复遇到额度限制会保留客户文本并推进同步游标。

额度记录覆盖本服务的发送；在其他工具中发送消息、或请求超时但企业微信实际已接收时，本地计数可能与平台不同，以企业微信的限制结果为准。当前发送锁适用于仓库默认的单进程部署，多进程部署需要共享发送锁。

人工发送被本地额度/窗口检查拦截时，会先通过 `sync_msg` 补齐目标客户的消息，再重新检查。补齐消息保留正文但不触发历史 AI 补答，且不推进公共回调游标，其他客户的回调仍正常处理。已确认发送的企业微信消息 ID 持久化到 `processed_message`，历史回填复用该 ID；升级时仅清理能唯一对应真实发送的旧回填副本。

LLM 上下文由两部分组成：最近 12 条已保存的客户/AI/人工消息（附 UTC 时间戳及发送方），加上当前一次拉取内同一客户全部待回复文本（按时间排序、按 msgid 去重）。本轮问题整体合并为一个 user 输入，不受历史 12 条上限截断；提示词要求综合回答所有问题并识别后续更正。每批每客户只调用一次 LLM、发送一条综合回复、消耗一次额度；成功后在同一事务中逐条保存原消息及一条回复，失败重试不会丢失前面的问题。登录链接等专用命令仍走原流程。合并范围是一次 `sync_msg` 拉取，尚未增加跨回调等待窗口；LLM 生成期间新到的消息会在后续一轮处理。

## 前端构建

前端位于 `frontend/`，采用 React、TypeScript、Vite、TanStack Query 和 TanStack Virtual。生产构建包含三个独立入口：

- `/`：无 JavaScript 依赖的 SEO 项目介绍页。
- `/admin/`：客服后台，用户列表和消息列表均使用虚拟滚动及分页加载。
- `/me/`：微信客户个人中心。

Compose 中的 `frontend-builder` 是一次性构建服务，产物写入 `frontend_dist` 命名卷，再由只读 Nginx gateway 提供。正常运行时没有 Node 服务。

本地验证：

```powershell
cd frontend
corepack pnpm install
.\node_modules\.bin\tsc.cmd --noEmit
.\node_modules\.bin\vite.cmd build
```
