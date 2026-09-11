"""FastAPI entrypoint for WeCom customer-service callbacks."""

from __future__ import annotations

import asyncio
import base64
import binascii
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
from typing import AsyncIterator

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from wechat_bot.auth import AuthManager, InvalidLoginTicket, SESSION_COOKIE_NAME
from wechat_bot.callback import (
    CallbackParseError,
    CustomerServiceEvent,
    parse_callback_event,
)
from wechat_bot.config import Settings
from wechat_bot.crypto import CallbackCryptoError, WeComCallbackCrypto
from wechat_bot.llm import OpenAICompatibleLLM
from wechat_bot.service import CustomerServiceProcessor
from wechat_bot.store import MessageStore
from wechat_bot.web import render_authentication_required
from wechat_bot.wecom import WeComAPIError, WeComClient
from wechat_bot.replies import ReplyUnavailable, send_reply


LOGGER = logging.getLogger(__name__)
INITIALIZATION_RETRY_SECONDS = 5.0
MAX_INITIALIZATION_RETRY_SECONDS = 60.0


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = MessageStore(settings.database_path)
        self.auth = AuthManager(self.store, settings.public_base_url)
        self.wecom = WeComClient(settings.corp_id, settings.app_agent_secret)
        self.llm = OpenAICompatibleLLM(settings.llm)
        self.processor = CustomerServiceProcessor(
            self.wecom,
            self.llm,
            self.store,
            settings.open_kfid,
            self.auth,
        )
        self.crypto: WeComCallbackCrypto | None = None
        self._config_errors = settings.callback_config_errors()
        self._initialization_error = ""
        self._initialization_task: asyncio.Task[None] | None = None
        if not self._config_errors:
            self.crypto = WeComCallbackCrypto(
                settings.callback_token,
                settings.encoding_aes_key,
                settings.corp_id,
            )
        self.tasks: set[asyncio.Task[None]] = set()

    @property
    def errors(self) -> list[str]:
        return self._config_errors + (
            [self._initialization_error] if self._initialization_error else []
        )

    @property
    def ready(self) -> bool:
        return not self.errors and bool(self.processor.managed_open_kfid)

    async def start(self) -> None:
        if not await self._initialize():
            self._initialization_task = asyncio.create_task(self._retry_initialization())

    async def _initialize(self) -> bool:
        try:
            await self.processor.bootstrap()
        except Exception as exc:
            # API/transport exception text may contain credentials or request URLs.
            detail = type(exc).__name__
            if isinstance(exc, WeComAPIError):
                detail += f" ({exc.operation}, errcode={exc.errcode})"
                if exc.errcode == 60020:
                    detail += "; add the service egress IP to the application's trusted IPs"
            self._initialization_error = f"cursor initialization failed: {detail}"
            LOGGER.warning("%s; initialization will retry", self._initialization_error)
            return False
        self._initialization_error = ""
        LOGGER.info("customer-service initialization completed")
        return True

    async def _retry_initialization(self) -> None:
        delay = INITIALIZATION_RETRY_SECONDS
        while True:
            await asyncio.sleep(delay)
            if await self._initialize():
                return
            delay = min(delay * 2, MAX_INITIALIZATION_RETRY_SECONDS)

    def schedule(self, event: CustomerServiceEvent) -> None:
        if event.open_kfid != self.processor.managed_open_kfid:
            raise CallbackParseError("callback open_kfid is not managed by this service")
        task = asyncio.create_task(self.processor.handle_event(event))
        self.tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            LOGGER.error(
                "customer-service callback processing failed",
                exc_info=task.exception(),
            )

    async def close(self) -> None:
        if self._initialization_task is not None:
            self._initialization_task.cancel()
            await asyncio.gather(self._initialization_task, return_exceptions=True)
        if self.tasks:
            _done, pending = await asyncio.wait(self.tasks, timeout=10)
            for task in pending:
                task.cancel()
        await self.llm.aclose()
        await self.wecom.aclose()
        self.store.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = Runtime(settings or Settings.load())
        app.state.runtime = runtime
        await runtime.start()
        try:
            yield
        finally:
            await runtime.close()

    app = FastAPI(title="WeChat Customer Service LLM PoC", lifespan=lifespan)

    @app.middleware("http")
    async def prevent_admin_caching(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/admin" or request.url.path.startswith("/api/admin/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> Response:
        if request.url.path.startswith("/api/admin/"):
            return admin_error("invalid_request", "请求参数无效", 422)
        return JSONResponse({"detail": exc.errors()}, status_code=422)

    def authenticated_user_id(request: Request) -> int | None:
        runtime: Runtime = request.app.state.runtime
        session_token = request.cookies.get(SESSION_COOKIE_NAME, "")
        return runtime.auth.authenticate_session(session_token)

    def timestamp_json(timestamp: int) -> str:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()

    def secure_html(content: str, *, status_code: int = 200) -> HTMLResponse:
        response = HTMLResponse(content, status_code=status_code)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src https: data:; "
            "style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    def admin_error(code: str, message: str, status_code: int, *, retryable: bool = False) -> JSONResponse:
        response = JSONResponse({"error": {"code": code, "message": message, "retryable": retryable}}, status_code=status_code)
        response.headers["Cache-Control"] = "no-store"
        return response

    def admin_authenticated(request: Request) -> bool:
        runtime: Runtime = request.app.state.runtime
        settings = getattr(runtime, "settings", None)
        if settings is None or not getattr(settings, "admin_configured", False):
            return False
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return False
        import hmac
        return hmac.compare_digest(username, settings.admin_username) and hmac.compare_digest(password, settings.admin_password)

    def require_admin(request: Request) -> Response | None:
        runtime: Runtime = request.app.state.runtime
        settings = getattr(runtime, "settings", None)
        if settings is None or not getattr(settings, "admin_configured", False):
            return admin_error("admin_not_configured", "客服后台尚未配置管理员账号", 503)
        if not admin_authenticated(request):
            response = admin_error("authentication_required", "需要客服管理员认证", 401)
            response.headers["WWW-Authenticate"] = 'Basic realm="customer-service-admin", charset="UTF-8"'
            return response
        return None

    def admin_message_json(message: object) -> dict[str, object]:
        return {
            "id": message.id, "sender_type": message.sender_type,
            "content": message.content, "occurred_at": timestamp_json(message.occurred_at),
            "message_type": message.message_type, "source": message.source,
            "send_status": message.send_status, "error_message": message.error_message,
            "client_request_id": message.client_request_id,
        }

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def health_ready(request: Request) -> Response:
        runtime: Runtime = request.app.state.runtime
        status_code = 200 if runtime.ready else 503
        return JSONResponse(
            {
                "status": "ready" if runtime.ready else "not_ready",
                "errors": runtime.errors,
                "application_agent_id": runtime.settings.app_agent_id,
            },
            status_code=status_code,
        )

    @app.get("/auth/t/{ticket}")
    async def consume_login_ticket(ticket: str, request: Request) -> Response:
        runtime: Runtime = request.app.state.runtime
        previous_session = request.cookies.get(SESSION_COOKIE_NAME, "")
        try:
            session = runtime.auth.consume_login_ticket(
                ticket, previous_session_token=previous_session
            )
        except InvalidLoginTicket:
            return secure_html(render_authentication_required(), status_code=400)
        response = RedirectResponse("/me", status_code=303)
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session.token,
            max_age=runtime.auth.session_ttl_seconds,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/me", response_class=HTMLResponse)
    async def user_center(request: Request) -> Response:
        runtime: Runtime = request.app.state.runtime
        user_id = authenticated_user_id(request)
        if user_id is None:
            return secure_html(render_authentication_required(), status_code=401)
        if runtime.store.get_customer_profile(user_id) is None:
            return secure_html(render_authentication_required(), status_code=404)
        response = RedirectResponse("/me/", status_code=303)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/me")
    async def get_current_user(request: Request) -> Response:
        runtime: Runtime = request.app.state.runtime
        user_id = authenticated_user_id(request)
        if user_id is None:
            raise HTTPException(401, "authentication required")
        profile = runtime.store.get_customer_profile(user_id)
        if profile is None:
            raise HTTPException(404, "customer profile is unavailable")
        response = JSONResponse(
            {
                "profile": {
                    "nickname": profile.nickname,
                    "avatar_url": profile.avatar_url,
                    "gender": profile.gender,
                },
                "data_scope": "仅展示本系统收到的微信客服资料和消息",
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/me/conversations")
    async def get_current_user_conversations(request: Request) -> Response:
        runtime: Runtime = request.app.state.runtime
        user_id = authenticated_user_id(request)
        if user_id is None:
            raise HTTPException(401, "authentication required")
        summaries = runtime.store.list_conversations(user_id)
        response = JSONResponse(
            {
                "conversations": [
                    {
                        "id": summary.id,
                        "source": summary.source,
                        "started_at": timestamp_json(summary.started_at),
                        "last_message_at": timestamp_json(summary.last_message_at),
                        "message_count": summary.message_count,
                        "preview": summary.preview,
                    }
                    for summary in summaries
                ]
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/me/conversations/{conversation_id}")
    async def get_current_user_conversation(
        conversation_id: int, request: Request
    ) -> Response:
        runtime: Runtime = request.app.state.runtime
        user_id = authenticated_user_id(request)
        if user_id is None:
            raise HTTPException(401, "authentication required")
        messages = runtime.store.conversation_messages(user_id, conversation_id)
        if messages is None:
            raise HTTPException(404, "conversation not found")
        response = JSONResponse(
            {
                "id": conversation_id,
                "messages": [
                    {
                        "sender_type": message.sender_type,
                        "content": message.content,
                        "occurred_at": timestamp_json(message.occurred_at),
                    }
                    for message in messages
                ],
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_console(request: Request) -> Response:
        denied = require_admin(request)
        if denied is not None:
            return denied
        return RedirectResponse("/admin/", status_code=303)

    @app.get("/api/admin/users")
    async def admin_users(request: Request, q: str = Query(""), page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100), sort: str = Query("recent")) -> Response:
        denied = require_admin(request)
        if denied is not None:
            return denied
        runtime: Runtime = request.app.state.runtime
        if sort not in {"recent", "oldest", "unread"}:
            return admin_error("invalid_sort", "不支持的排序方式", 400)
        if not runtime.processor.managed_open_kfid:
            return admin_error("service_not_ready", "客服账号尚未就绪", 503, retryable=True)
        users, total = runtime.store.list_admin_users(runtime.processor.managed_open_kfid, search=q, page=page, page_size=page_size, sort=sort)
        return JSONResponse({"users": [{"id": u.id, "nickname": u.nickname, "avatar_url": u.avatar_url if u.avatar_url.startswith("https://") else "", "last_message_preview": u.last_message_preview, "last_active_at": timestamp_json(u.last_active_at), "unread_count": u.unread_count} for u in users], "page": page, "page_size": page_size, "total": total})

    @app.get("/api/admin/session")
    async def admin_session(request: Request) -> Response:
        denied = require_admin(request)
        if denied is not None:
            return denied
        return Response(status_code=204)

    @app.get("/api/admin/users/{user_id}")
    async def admin_user(request: Request, user_id: int) -> Response:
        denied = require_admin(request)
        if denied is not None:
            return denied
        runtime: Runtime = request.app.state.runtime
        profile = runtime.store.get_admin_user(user_id, runtime.processor.managed_open_kfid)
        if profile is None:
            return admin_error("user_not_found", "用户不存在", 404)
        return JSONResponse({"user": {"id": profile.user_id, "nickname": profile.nickname, "avatar_url": profile.avatar_url if profile.avatar_url.startswith("https://") else "", "gender": profile.gender, "first_seen_at": timestamp_json(profile.first_seen_at), "last_seen_at": timestamp_json(profile.last_seen_at), **runtime.store.customer_management(user_id)}}, headers={"Cache-Control": "no-store"})

    @app.patch("/api/admin/users/{user_id}")
    async def admin_update_customer(request: Request, user_id: int, body: dict[str, object] = Body(...)) -> Response:
        denied = require_admin(request)
        if denied is not None:
            return denied
        if request.headers.get("x-requested-with") != "XMLHttpRequest":
            return admin_error("csrf_required", "修改客户需要 CSRF 请求头", 403)
        nickname, gender, notes, tags = (body.get(key) for key in ("nickname", "gender", "notes", "tags"))
        if not isinstance(nickname, str) or len(nickname.strip()) > 100:
            return admin_error("invalid_request", "昵称最多 100 个字符", 400)
        if type(gender) is not int or gender not in (0, 1, 2):
            return admin_error("invalid_request", "性别值无效", 400)
        if not isinstance(notes, str) or len(notes) > 2000:
            return admin_error("invalid_request", "备注最多 2000 个字符", 400)
        if not isinstance(tags, list) or len(tags) > 10 or any(not isinstance(tag, str) or not tag.strip() or len(tag.strip()) > 20 for tag in tags):
            return admin_error("invalid_request", "最多 10 个标签，每个标签 1–20 个字符", 400)
        runtime: Runtime = request.app.state.runtime
        try:
            runtime.store.update_managed_customer(user_id, runtime.processor.managed_open_kfid,
                nickname=nickname.strip(), gender=gender, notes=notes.strip(),
                tags=list(dict.fromkeys(tag.strip() for tag in tags)), operator_id=runtime.settings.admin_username)
        except LookupError:
            return admin_error("user_not_found", "用户不存在", 404)
        LOGGER.info("admin customer updated: user_id=%s", user_id)
        return await admin_user(request, user_id)

    @app.delete("/api/admin/users/{user_id}")
    async def admin_delete_customer(request: Request, user_id: int, body: dict[str, object] = Body(...)) -> Response:
        denied = require_admin(request)
        if denied is not None:
            return denied
        if request.headers.get("x-requested-with") != "XMLHttpRequest":
            return admin_error("csrf_required", "删除客户需要 CSRF 请求头", 403)
        if type(body.get("confirm_user_id")) is not int or body["confirm_user_id"] != user_id:
            return admin_error("confirmation_required", "请确认要删除的客户 ID", 400)
        runtime: Runtime = request.app.state.runtime
        open_kfid = runtime.processor.managed_open_kfid
        external_userid = runtime.store.get_customer_external_userid(user_id, open_kfid)
        if external_userid is None:
            return admin_error("user_not_found", "用户不存在", 404)
        async with runtime.store.customer_lock(open_kfid, external_userid):
            try:
                runtime.store.delete_managed_customer(user_id, open_kfid)
            except LookupError:
                return admin_error("user_not_found", "用户不存在", 404)
            except ValueError as exc:
                return admin_error("customer_busy", str(exc), 409)
        LOGGER.info("admin customer deleted: user_id=%s", user_id)
        return Response(status_code=204)

    @app.get("/api/admin/users/{user_id}/identity")
    async def admin_customer_identity(request: Request, user_id: int) -> Response:
        denied = require_admin(request)
        if denied is not None:
            return denied
        runtime: Runtime = request.app.state.runtime
        open_kfid = runtime.processor.managed_open_kfid
        if not open_kfid:
            return admin_error("service_not_ready", "客服账号尚未就绪", 503, retryable=True)
        external_userid = runtime.store.get_customer_external_userid(user_id, open_kfid)
        if external_userid is None:
            return admin_error("user_not_found", "用户不存在", 404)
        return JSONResponse(
            {"user_id": user_id, "open_kfid": open_kfid, "external_userid": external_userid},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/admin/users/{user_id}/conversation")
    async def admin_conversation(request: Request, user_id: int, before_id: int | None = Query(None, ge=1), limit: int = Query(50, ge=1, le=100)) -> Response:
        denied = require_admin(request)
        if denied is not None:
            return denied
        runtime: Runtime = request.app.state.runtime
        if runtime.store.get_admin_user(user_id, runtime.processor.managed_open_kfid) is None:
            return admin_error("user_not_found", "用户不存在", 404)
        messages, has_more = runtime.store.admin_conversation_messages(user_id, runtime.processor.managed_open_kfid, before_id=before_id, limit=limit)
        runtime.store.mark_admin_user_read(user_id, messages[-1].id if messages else before_id)
        return JSONResponse({"messages": [admin_message_json(m) for m in messages], "has_more": has_more, "next_before_id": messages[0].id if has_more and messages else None})

    @app.post("/api/admin/users/{user_id}/messages")
    async def admin_send_message(request: Request, user_id: int, body: dict[str, object] | None = Body(default=None)) -> Response:
        denied = require_admin(request)
        if denied is not None:
            return denied
        if request.headers.get("x-requested-with", "") != "XMLHttpRequest":
            return admin_error("csrf_required", "发送消息需要 CSRF 请求头", 403)
        runtime: Runtime = request.app.state.runtime
        open_kfid = runtime.processor.managed_open_kfid
        if not open_kfid:
            return admin_error("service_not_ready", "客服账号尚未就绪", 503, retryable=True)
        payload = body or {}
        retry_id = payload.get("retry_message_id")
        try:
            retry_message_id = int(retry_id) if retry_id is not None else None
        except (TypeError, ValueError):
            return admin_error("invalid_request", "重试消息编号无效", 400)
        content = str(payload.get("content", "")).strip()
        if retry_message_id is None and not content:
            return admin_error("empty_message", "消息内容不能为空", 400)
        if len(content) > 2000:
            return admin_error("message_too_long", "消息长度不能超过 2000 个字符", 400)
        request_id = str(payload.get("request_id", "")).strip()
        if not request_id or len(request_id) > 100:
            return admin_error("invalid_request", "缺少有效 request_id", 400)
        if retry_message_id is None:
            existing = runtime.store.get_admin_message_by_request(user_id, request_id)
            if existing is not None:
                if existing.send_status == "failed":
                    return admin_error("retry_required", "该消息发送失败，请明确点击重试", 409, retryable=True)
                return JSONResponse({"message": admin_message_json(existing), "deduplicated": True}, status_code=202 if existing.send_status == "pending" else 200)
        try:
            message, should_send = runtime.store.create_admin_message(user_id=user_id, open_kfid=open_kfid, content=content, operator_id=runtime.settings.admin_username, request_id=request_id, retry_message_id=retry_message_id)
        except LookupError:
            return admin_error("user_not_found", "用户不存在", 404)
        except ValueError as exc:
            return admin_error("invalid_request", str(exc), 409)
        if message.send_status == "sent":
            return JSONResponse({"message": admin_message_json(message), "deduplicated": True})
        if not should_send:
            return JSONResponse({"message": admin_message_json(message), "deduplicated": True}, status_code=202)
        external_userid = runtime.store.get_customer_external_userid(user_id, open_kfid)
        if not external_userid:
            runtime.store.complete_admin_message(message.id, status="failed", error_message="user_unavailable")
            return admin_error("user_unavailable", "用户当前不可发送", 409, retryable=True)
        try:
            try:
                runtime.store.reply_budget(open_kfid, external_userid).footer()
            except ReplyUnavailable:
                # Local state may lag behind WeChat when a callback was missed.
                await runtime.processor.refresh_for_admin(external_userid)
            source_message_id = await send_reply(runtime.wecom, runtime.store, open_kfid, external_userid, message.content)
        except ReplyUnavailable as exc:
            upstream = exc.__cause__ if isinstance(exc.__cause__, WeComAPIError) else None
            LOGGER.warning(
                "admin message delivery blocked: message_id=%s code=%s source=%s errcode=%s",
                message.id, exc.code, "wecom" if upstream else "local",
                upstream.errcode if upstream else None,
            )
            runtime.store.complete_admin_message(message.id, status="failed", error_message=exc.code)
            failed = runtime.store.get_admin_message_by_request(user_id, message.client_request_id or request_id)
            return JSONResponse({"error": {"code": exc.code, "message": str(exc), "retryable": False}, "message": admin_message_json(failed or message)}, status_code=409)
        except Exception as exc:
            LOGGER.warning("admin message delivery failed: message_id=%s error=%s errcode=%s", message.id, type(exc).__name__, exc.errcode if isinstance(exc, WeComAPIError) else None)
            runtime.store.complete_admin_message(message.id, status="failed", error_message="delivery_failed")
            failed = runtime.store.get_admin_message_by_request(user_id, message.client_request_id or request_id)
            return JSONResponse({"error": {"code": "delivery_failed", "message": "企业微信发送失败，可点击重试", "retryable": True}, "message": admin_message_json(failed or message)}, status_code=502)
        runtime.store.complete_admin_message(message.id, status="sent", source_message_id=source_message_id or None)
        sent = runtime.store.get_admin_message_by_request(user_id, message.client_request_id or request_id) or message
        return JSONResponse({"message": admin_message_json(sent)})

    @app.get("/wecom/kf/callback", response_class=PlainTextResponse)
    async def verify_callback(
        request: Request,
        msg_signature: str = Query(...),
        timestamp: str = Query(...),
        nonce: str = Query(...),
        echostr: str = Query(...),
    ) -> str:
        runtime: Runtime = request.app.state.runtime
        if runtime.crypto is None:
            raise HTTPException(503, "callback encryption is not configured")
        try:
            return runtime.crypto.verify_url(msg_signature, timestamp, nonce, echostr)
        except CallbackCryptoError as exc:
            raise HTTPException(403, "callback verification failed") from exc

    @app.post("/wecom/kf/callback", response_class=PlainTextResponse)
    async def receive_callback(
        request: Request,
        msg_signature: str = Query(...),
        timestamp: str = Query(...),
        nonce: str = Query(...),
    ) -> str:
        runtime: Runtime = request.app.state.runtime
        if not runtime.ready or runtime.crypto is None:
            raise HTTPException(503, "callback service is not ready")
        encrypted_xml = (await request.body()).decode("utf-8")
        try:
            event_xml = runtime.crypto.decrypt_callback(
                msg_signature, timestamp, nonce, encrypted_xml
            )
            event = parse_callback_event(event_xml, runtime.settings.corp_id)
            if event is None:
                LOGGER.info("ignored non-customer-service callback")
                return "success"
            runtime.schedule(event)
        except CallbackCryptoError as exc:
            raise HTTPException(403, "callback verification failed") from exc
        except CallbackParseError as exc:
            raise HTTPException(400, "unsupported callback") from exc
        return "success"

    return app


app = create_app()
