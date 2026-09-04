"""Server-rendered mobile pages for the customer center."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlsplit

from wechat_bot.store import (
    ConversationMessage,
    ConversationSummary,
    CustomerProfile,
)


DISPLAY_TIMEZONE = timezone(timedelta(hours=8))


def _format_time(timestamp: int) -> tuple[str, str]:
    value = datetime.fromtimestamp(timestamp, timezone.utc)
    display = value.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    return value.isoformat(), f"{display} UTC+8"


def _safe_avatar(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return escape(url, quote=True)


def render_user_center(
    profile: CustomerProfile,
    conversations: list[tuple[ConversationSummary, list[ConversationMessage]]],
) -> str:
    nickname = escape(profile.nickname.strip() or "微信用户")
    avatar_url = _safe_avatar(profile.avatar_url)
    if avatar_url:
        avatar = f'<img class="avatar" src="{avatar_url}" alt="用户头像">'
    else:
        avatar = '<div class="avatar avatar-fallback" aria-hidden="true">微</div>'
    _first_seen_iso, first_seen_display = _format_time(profile.first_seen_at)
    _last_seen_iso, last_seen_display = _format_time(profile.last_seen_at)

    conversation_cards: list[str] = []
    for index, (summary, messages) in enumerate(conversations):
        _started_iso, started_display = _format_time(summary.started_at)
        last_iso, last_display = _format_time(summary.last_message_at)
        message_blocks: list[str] = []
        for message in messages:
            occurred_iso, occurred_display = _format_time(message.occurred_at)
            sender_label = "我" if message.sender_type == "customer" else "AI 客服"
            timestamp_label = (
                "发送时间" if message.sender_type == "customer" else "回复时间"
            )
            message_blocks.append(
                f"""
                <article class="message {escape(message.sender_type)}">
                  <div class="message-meta">
                    <span>{sender_label}</span>
                    <span>{timestamp_label}</span>
                    <time datetime="{occurred_iso}">{occurred_display}</time>
                  </div>
                  <div class="bubble">{escape(message.content)}</div>
                </article>
                """
            )
        messages_html = "".join(message_blocks) or (
            '<div class="empty-state">这段会话暂时没有可展示的文本消息。</div>'
        )
        open_attribute = " open" if index == 0 else ""
        conversation_cards.append(
            f"""
            <details class="conversation"{open_attribute}>
              <summary>
                <span class="conversation-title">微信客服 · AI 自动回复</span>
                <span class="conversation-count">{summary.message_count} 条消息</span>
                <span class="conversation-preview">{escape(summary.preview)}</span>
                <span class="conversation-time">
                  最近更新：<time datetime="{last_iso}">{last_display}</time>
                </span>
              </summary>
              <div class="conversation-detail">
                <div class="conversation-start">开始于 {started_display}</div>
                {messages_html}
              </div>
            </details>
            """
        )
    conversations_html = "".join(conversation_cards) or (
        """
        <section class="empty-state page-empty">
          <div class="empty-icon">💬</div>
          <h2>还没有客服记录</h2>
          <p>你与 AI 客服完成问答后，记录会显示在这里。</p>
        </section>
        """
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="color-scheme" content="light">
  <meta name="theme-color" content="#07c160">
  <title>我的客服记录</title>
  <style>
    :root {{
      color: #1f2329;
      background: #f5f6f7;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(180deg, #e9fbf0 0, #f5f6f7 240px);
    }}
    .page {{
      width: min(100%, 760px);
      margin: 0 auto;
      padding: max(22px, env(safe-area-inset-top)) 16px
        max(32px, env(safe-area-inset-bottom));
    }}
    .profile-card {{
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 20px;
      border: 1px solid rgba(7, 193, 96, .16);
      border-radius: 20px;
      background: rgba(255, 255, 255, .94);
      box-shadow: 0 12px 32px rgba(31, 35, 41, .08);
    }}
    .avatar {{
      width: 62px;
      height: 62px;
      flex: 0 0 62px;
      border-radius: 18px;
      object-fit: cover;
      background: #dff7e9;
    }}
    .avatar-fallback {{
      display: grid;
      place-items: center;
      color: #067a3f;
      font-size: 25px;
      font-weight: 700;
    }}
    h1 {{ margin: 0 0 5px; font-size: 22px; line-height: 1.25; }}
    .profile-subtitle {{ margin: 0; color: #697077; font-size: 14px; }}
    .profile-times {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-top: 14px;
    }}
    .profile-time {{
      padding: 9px 10px;
      border-radius: 11px;
      background: #f6faf7;
    }}
    .profile-time span {{ display: block; color: #858b91; font-size: 11px; }}
    .profile-time strong {{
      display: block;
      margin-top: 3px;
      color: #39423d;
      font-size: 12px;
      font-weight: 600;
    }}
    .scope-notice {{
      margin: 14px 0 22px;
      padding: 13px 15px;
      border-radius: 14px;
      color: #486052;
      background: rgba(255, 255, 255, .72);
      font-size: 13px;
      line-height: 1.55;
    }}
    .section-heading {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      margin: 0 2px 12px;
    }}
    .section-heading h2 {{ margin: 0; font-size: 18px; }}
    .section-heading span {{ color: #858b91; font-size: 12px; }}
    .conversation {{
      margin-bottom: 14px;
      overflow: hidden;
      border: 1px solid #e7e9eb;
      border-radius: 18px;
      background: #fff;
      box-shadow: 0 7px 22px rgba(31, 35, 41, .05);
    }}
    .conversation summary {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 6px 12px;
      padding: 17px 18px;
      cursor: pointer;
      list-style: none;
    }}
    .conversation summary::-webkit-details-marker {{ display: none; }}
    .conversation-title {{ font-weight: 650; }}
    .conversation-count {{ color: #07a952; font-size: 13px; }}
    .conversation-preview {{
      overflow: hidden;
      color: #697077;
      font-size: 13px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .conversation-time {{ color: #9a9fa5; font-size: 11px; text-align: right; }}
    .conversation-detail {{
      padding: 16px 14px 18px;
      border-top: 1px solid #eef0f2;
      background: #f7f8f9;
    }}
    .conversation-start {{
      margin: 0 auto 16px;
      color: #9a9fa5;
      font-size: 11px;
      text-align: center;
    }}
    .message {{ display: flex; flex-direction: column; margin: 0 0 15px; }}
    .message.customer {{ align-items: flex-end; }}
    .message.assistant {{ align-items: flex-start; }}
    .message-meta {{
      display: flex;
      gap: 8px;
      margin: 0 4px 5px;
      color: #858b91;
      font-size: 11px;
    }}
    .customer .message-meta {{ flex-direction: row-reverse; }}
    .bubble {{
      max-width: 86%;
      padding: 11px 13px;
      border-radius: 15px 15px 15px 5px;
      background: #fff;
      box-shadow: 0 2px 8px rgba(31, 35, 41, .06);
      font-size: 15px;
      line-height: 1.55;
      overflow-wrap: anywhere;
      white-space: pre-wrap;
    }}
    .customer .bubble {{
      border-radius: 15px 15px 5px 15px;
      background: #95ec69;
    }}
    .empty-state {{
      padding: 22px;
      border-radius: 14px;
      color: #858b91;
      background: #fff;
      text-align: center;
    }}
    .page-empty {{ padding: 42px 20px; border: 1px solid #e7e9eb; }}
    .empty-icon {{ font-size: 34px; }}
    .empty-state h2 {{ margin: 10px 0 6px; color: #3f454b; font-size: 17px; }}
    .empty-state p {{ margin: 0; font-size: 13px; }}
    footer {{
      padding: 18px 8px 0;
      color: #9a9fa5;
      font-size: 11px;
      line-height: 1.6;
      text-align: center;
    }}
    @media (max-width: 420px) {{
      .page {{ padding-left: 12px; padding-right: 12px; }}
      .profile-card {{ border-radius: 17px; }}
      .conversation {{ border-radius: 16px; }}
      .conversation-time {{ grid-column: 1 / -1; text-align: left; }}
      .bubble {{ max-width: 91%; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="profile-card">
      {avatar}
      <div>
        <h1>{nickname}</h1>
        <p class="profile-subtitle">我的 AI 客服记录</p>
        <div class="profile-times">
          <div class="profile-time">
            <span>首次咨询</span><strong>{first_seen_display}</strong>
          </div>
          <div class="profile-time">
            <span>最近咨询</span><strong>{last_seen_display}</strong>
          </div>
        </div>
      </div>
    </section>
    <aside class="scope-notice">
      当前仅展示本系统通过微信客服 API 收到并保存的消息，
      不代表完整的企业微信“会话内容存档”。
    </aside>
    <div class="section-heading">
      <h2>历史对话</h2>
      <span>时间显示为 UTC+8</span>
    </div>
    {conversations_html}
    <footer>
      登录链接只能使用一次。请勿转发链接或向他人展示本页面。
    </footer>
  </main>
</body>
</html>"""


def render_authentication_required() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>登录已失效</title>
  <style>
    body { margin: 0; background: #f5f6f7; color: #1f2329;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; }
    main { width: min(88%, 420px); margin: 18vh auto 0; padding: 32px 22px;
      border-radius: 18px; background: #fff; text-align: center;
      box-shadow: 0 12px 32px rgba(31, 35, 41, .08); }
    .icon { font-size: 42px; } h1 { margin: 14px 0 8px; font-size: 21px; }
    p { margin: 0; color: #697077; font-size: 14px; line-height: 1.65; }
  </style>
</head>
<body><main><div class="icon">🔒</div><h1>登录已失效</h1>
<p>请返回微信客服，发送“我的信息”或“查看记录”获取新的登录链接。</p>
</main></body></html>"""
