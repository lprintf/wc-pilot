"""FastAPI entrypoint for WeCom customer-service callbacks."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request, Response
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
from wechat_bot.web import render_authentication_required, render_user_center
from wechat_bot.wecom import WeComClient


LOGGER = logging.getLogger(__name__)


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
        self.errors = settings.callback_config_errors()
        if not self.errors:
            self.crypto = WeComCallbackCrypto(
                settings.callback_token,
                settings.encoding_aes_key,
                settings.corp_id,
            )
        self.tasks: set[asyncio.Task[None]] = set()

    @property
    def ready(self) -> bool:
        return not self.errors and bool(self.processor.managed_open_kfid)

    async def start(self) -> None:
        try:
            await self.processor.bootstrap()
        except Exception as exc:
            LOGGER.exception("customer-service cursor initialization failed")
            self.errors.append(f"cursor initialization failed: {type(exc).__name__}")

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
        profile = runtime.store.get_customer_profile(user_id)
        if profile is None:
            return secure_html(render_authentication_required(), status_code=404)
        conversations = []
        for summary in runtime.store.list_conversations(user_id):
            messages = runtime.store.conversation_messages(user_id, summary.id)
            if messages is not None:
                conversations.append((summary, messages))
        return secure_html(render_user_center(profile, conversations))

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
