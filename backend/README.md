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
