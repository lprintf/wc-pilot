# 企业微信客服接入 LLM PoC 实施计划

## 1. 目标与范围

目标：普通微信用户通过“微信客服”链接或二维码发起咨询，服务端拉取文本消息、调用 LLM，并以同一客服账号回复用户。

PoC 只验证以下闭环：

```text
微信用户 -> 微信客服 -> 加密事件回调 -> sync_msg
        -> LLM -> send_msg -> 微信用户
```

本阶段不实现：完整 SCRM 前端、RAG/知识库、Milvus、会话存档、排班、数据分析、多租户和复杂人工坐席分配。

参考实现位于 `ref/iYqueCode`，仅用于核对企微协议和接口调用，不作为运行时依赖。

配置操作手册见 [WECHAT_KF_SETUP.md](./WECHAT_KF_SETUP.md)，两条消息获取路线及当前架构决策见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

当前进度（2026-08-25）：企业微信授权、主动 API 烟测、公网回调、LLM、`send_msg`、cursor 持久化和消息幂等均已通过真实消息验证。服务端已对一条客户问题生成并发送一条回复，当前只差确认微信客户端实际显示一次回复，并继续执行多轮和重启验收。

## 2. 暂定技术方案

- Python 3.13
- FastAPI：HTTP 回调和健康检查
- httpx：企业微信及 LLM API 调用
- SQLite：cursor、消息幂等和少量会话历史
- 后台工作协程：回调快速确认后异步拉取和处理消息
- unittest：加解密、回调、LLM 和消息处理测试

如后续确定使用 Java，可保留相同模块边界，将 FastAPI/httpx 替换为 Spring Boot/WxJava。

## 3. 企业微信准备

- [x] 在企业微信管理后台启用“微信客服”，创建 PoC 客服账号并获取客服链接或二维码。
- [x] 创建或选择一个企业微信自建应用，获取其 `AgentID` 和 `Secret`，分别配置为 `APP_AGENT_ID`、`APP_AGENT_SECRET`。
- [x] 完成自建应用的可信域名认证。
- [x] 在自建应用“接收消息 -> 设置 API 接收”中保存回调 URL，并启用“微信客服消息和事件”；系统强制启用的普通应用消息由后端验签后忽略。
- [x] 将当前开发环境的公网出口 IP 加入该自建应用的企业可信 IP。
- [x] 在“应用管理 -> 微信客服 -> API -> 可调用接口的应用”中添加该自建应用，并通过 `kf/account/list` 验证可以看到客服列表。
- [x] 在“通过 API 管理会话消息 -> 企业内部开发”中为该应用指定 PoC 客服账号，并通过 `kf/sync_msg`、`kf/send_msg` 验证会话消息权限。
- [x] 确保客服账号对应的接待人员位于该自建应用的可见范围内；这不是核心授权步骤，仅用于排查 `60030` 等相关错误。
- [x] 准备公网 HTTPS 地址 `https://wc-dev.lprintf.com`，配置 `PUBLIC_BASE_URL`，并验证健康检查和域名认证文件均返回 HTTP 200。
- [x] 生成回调 Token 和 43 位 EncodingAESKey，写入 `.env`。
- [x] 使用 `${PUBLIC_BASE_URL}/wecom/kf/callback` 和本地相同的 Token/AESKey 通过 URL 验证。
- [x] 验证真实 `kf_msg_or_event` 能触发 `sync_msg -> LLM -> send_msg`，且 SQLite 只产生一个唯一消息记录。
- [x] 通过 `kf/account/list` 获取测试客服账号的 `open_kfid`。
- [x] 将客服链接写入 `CUSTOM_SERVICE_URL`；当前只有一个客服账号，`WECHAT_KF_OPEN_KFID` 可暂时留空。
- [x] 使用普通微信确认链接或二维码能够进入客服会话。

说明：本 PoC 使用企业微信接管微信客服的“企业内部开发”模式。自建应用是 API 调用主体，其 Secret 与 `CorpID` 一起换取 `access_token`；`BOT_ID`/`BOT_SECRET` 是群机器人配置，本 PoC 不使用。独立微信客服管理后台的 Secret 和帮助其他企业管理微信客服的第三方授权模式均不在本 PoC 范围内。

## 4. 里程碑 A：项目骨架与配置

- [x] 初始化应用、依赖管理和测试目录。
- [x] 增加类型化配置加载，启动时检查所有必需环境变量。
- [x] 确保日志只记录非敏感状态，不输出 Secret、Token、AESKey 或 LLM API Key。
- [x] 增加 `GET /health/live` 和 `GET /health/ready`，返回应用及消息处理器就绪状态。
- [x] 增加 `.gitignore`，禁止提交 `.env`、数据库文件、日志和本地证书。

验收：缺少必需配置时启动失败并指出变量名；配置完整时 `/healthz` 返回成功。

## 5. 里程碑 B：企微回调

- [x] 实现 `GET /wecom/kf/callback`，校验 `msg_signature` 并解密 `echostr`。
- [x] 实现 `POST /wecom/kf/callback`，验签、AES 解密并解析 XML。
- [x] 解密后校验消息尾部 CorpID 必须等于配置的 `CorpID`。
- [x] 只接受 `kf_msg_or_event`，提取 `Token` 和 `OpenKfId`。
- [x] 回调立即返回企微要求的成功响应，耗时工作交给后台任务。
- [x] 为无效签名、错误 CorpID、无效事件和重复消息处理增加测试。

验收：企业微信后台能够保存回调配置；无效请求不会进入消息处理流程。

## 6. 里程碑 C：企业微信 API 客户端

- [x] 使用烟测脚本验证 `CorpID + APP_AGENT_SECRET` 可以获取 `access_token`。
- [x] 使用烟测脚本验证 `kf/account/list` 可以返回被授权客服账号。
- [x] 使用烟测脚本验证 `sync_msg` 可以读取普通微信客户消息。
- [x] 使用烟测脚本验证 `send_msg` 可以向普通微信客户回复文本。
- [x] 在正式后端客户端中实现 `CorpID + APP_AGENT_SECRET` 获取 `access_token`，不得使用系统应用 Secret。
- [ ] 启动时记录 `APP_AGENT_ID` 以标识被授权应用，但不在日志中输出应用 Secret。
- [x] 缓存 token，并在过期或企微返回 token 失效错误时刷新一次。
- [x] 实现 `sync_msg`，传入回调 Token、cursor 和 `open_kfid`。
- [x] 当 `has_more=1` 时持续分页，直到本轮消息拉取完成。
- [x] 实现 `send_msg` 文本回复。
- [x] 统一处理企微 HTTP、网络、JSON 和 `errcode` 错误；PoC 仅对 access token 失效执行一次刷新重试。

验收：使用模拟响应验证 token 缓存、分页和错误处理；未授权应用或未指定为 API 管理的客服账号会产生明确错误；授权完成后能够拉取真实测试客服消息。

## 7. 里程碑 D：持久化与幂等

- [x] 建立 `kf_cursor` 表，按 `open_kfid` 保存最新 cursor。
- [x] 建立 `processed_message` 表，以企微消息 ID 做唯一约束。
- [x] cursor 在本轮消息处理完成后推进；首次启动只建立基线，不回复历史消息。
- [x] 对同一 `open_kfid` 串行拉取，防止并发回调争用 cursor。
- [x] 保存最少量的用户消息、回复、状态和时间；不保存原始加密请求。

验收：重复回调不会重复回复；服务重启后能够从已保存 cursor 继续处理。

## 8. 里程碑 E：LLM 问答

- [x] 实现 OpenAI 兼容的非流式 Chat Completions 客户端。
- [x] 配置 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY`。
- [x] 使用真实凭证运行 `backend/scripts/test_llm_api.py`，确认模型成功返回文本。
- [x] 只处理客户发来的文本消息，忽略机器人自身消息和非文本消息。
- [x] 设置基础系统提示词，明确身份、回答边界和禁止泄露内部配置；正式上线前仍需按业务范围细化。
- [x] 按外部联系人保存最近 6 轮对话。
- [x] 设置连接和响应超时；LLM 失败时发送固定兜底文案。
- [x] 按 UTF-8 字节数限制回复长度，适配微信客服 2048 字节文本限制。

验收：模拟 LLM 成功、超时、限流和异常响应；所有分支均产生明确、可控的处理结果。

## 9. 里程碑 F：端到端验收

- [x] 部署到公网 HTTPS 测试地址，验证 `/health/ready` 和域名认证文件返回 HTTP 200；生产化前仍需确认固定出口 IP。
- [x] 确认自建应用已获微信客服接口权限，且测试客服账号已切换为通过该应用 API 管理。
- [x] 确认测试接待人员位于自建应用可见范围内。
- [x] 在自建应用中完成 PoC 接收消息服务器 URL 验证。
- [x] 普通微信用户通过 `CUSTOM_SERVICE_URL` 发起会话，并完成 API 文本回复烟测。
- [ ] 发送文本问题，确认只收到一次 LLM 回复。
- [ ] 连续发送多条消息，验证顺序、上下文和分页处理。
- [ ] 重启服务后再次发送消息，验证 cursor 恢复。
- [ ] 模拟 LLM 不可用，验证兜底回复。
- [ ] 检查日志，确认不包含密钥、完整 access token 或原始客户敏感信息。

PoC 完成标准：在不人工操作后台的情况下，普通微信用户能够稳定完成至少 20 轮问答；重复回调不重复回复；服务重启不丢失新消息。

## 10. PoC 后再评估

- [ ] RAG 与企业知识库，并评估是否需要向量数据库。
- [ ] 转人工和 AI/人工服务状态切换。
- [ ] 图片、语音和文件消息处理。
- [ ] 队列、失败重试、死信和多实例部署。
- [ ] 内容安全、敏感信息脱敏、审计和数据保留策略。
- [ ] 指标监控：回调数、拉取延迟、LLM 延迟、发送成功率和 token 成本。
