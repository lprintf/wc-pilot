"""FastAPI entrypoint for WeCom customer-service callbacks."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

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
from wechat_bot.wecom import WeComClient


LOGGER = logging.getLogger(__name__)


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = MessageStore(settings.database_path)
        self.wecom = WeComClient(settings.corp_id, settings.app_agent_secret)
        self.llm = OpenAICompatibleLLM(settings.llm)
        self.processor = CustomerServiceProcessor(
            self.wecom, self.llm, self.store, settings.open_kfid
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
