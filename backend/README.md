# 企业微信客服 LLM PoC 后端

当前已实现完整的微信客服事件回调链路：验签解密、`sync_msg` 拉取、SQLite cursor/消息幂等、OpenAI 兼容 LLM 调用和 `send_msg` 回复。首次启动只建立 cursor 基线，不会回复历史消息。

容器运行后提供以下端点：

- `GET /health/live`
- `GET /health/ready`
- `GET|POST /wecom/kf/callback`

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

后台接口包括：

- `GET /api/admin/users?q=&page=&page_size=&sort=`：按昵称或内部 `user_id` 搜索、分页和排序，返回未读数及最近消息摘要。
- `GET /api/admin/users/{user_id}`：返回安全范围内的客户资料。
- `GET /api/admin/users/{user_id}/conversation?before_id=&limit=`：按稳定消息 ID 分页读取会话，并标记已读。
- `POST /api/admin/users/{user_id}/messages`：发送人工文本。请求必须带客户端 `request_id`；重复提交只返回原消息。发送失败会保留 `failed` 状态，可带 `retry_message_id` 明确重试。

发送接口同时要求 `X-Requested-With: XMLHttpRequest` 请求头作为 Demo 级 CSRF 防护；生产环境还应补充基于 Session/CSRF Token 的完整方案。

人工消息会先以 `pending` 写入统一 `conversation_message` 表，再调用企业微信 `kf/send_msg`，成功或失败后更新状态；操作员标识和时间一并保留。数据库启动时会将旧 `processed_message` 中的 AI 对话回填到统一消息表。
