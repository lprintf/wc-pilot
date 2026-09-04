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
