"""Async client for the WeCom WeChat Customer Service API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Any
from urllib.parse import urlencode

import httpx


API_ROOT = "https://qyapi.weixin.qq.com/cgi-bin"


class WeComAPIError(RuntimeError):
    def __init__(self, operation: str, errcode: int, errmsg: str) -> None:
        super().__init__(f"{operation} failed: errcode={errcode}, errmsg={errmsg}")
        self.operation = operation
        self.errcode = errcode
        self.errmsg = errmsg


@dataclass(frozen=True, slots=True)
class SyncResult:
    messages: list[dict[str, Any]]
    next_cursor: str
    pages: int


@dataclass(frozen=True, slots=True)
class WeComCustomer:
    external_userid: str
    nickname: str
    avatar_url: str
    gender: int
    unionid: str | None
    scene: str
    scene_param: str


class WeComClient:
    def __init__(
        self,
        corp_id: str,
        app_secret: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._corp_id = corp_id
        self._app_secret = app_secret
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0)
        )
        self._access_token = ""
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_accounts(self) -> list[dict[str, Any]]:
        result = await self._api_request("GET", "kf/account/list")
        accounts = result.get("account_list", [])
        if not isinstance(accounts, list):
            raise RuntimeError("kf/account/list returned a non-list account_list")
        return [account for account in accounts if isinstance(account, dict)]

    async def sync_messages(
        self,
        open_kfid: str,
        cursor: str = "",
        callback_token: str = "",
        *,
        max_pages: int = 20,
    ) -> SyncResult:
        messages: list[dict[str, Any]] = []
        next_cursor = cursor
        for page in range(1, max_pages + 1):
            body: dict[str, Any] = {"open_kfid": open_kfid, "limit": 1000}
            if next_cursor:
                body["cursor"] = next_cursor
            if callback_token:
                body["token"] = callback_token
            result = await self._api_request("POST", "kf/sync_msg", body)
            page_messages = result.get("msg_list", [])
            if not isinstance(page_messages, list):
                raise RuntimeError("kf/sync_msg returned a non-list msg_list")
            messages.extend(item for item in page_messages if isinstance(item, dict))
            next_cursor = str(result.get("next_cursor", next_cursor))
            if int(result.get("has_more", 0)) != 1:
                return SyncResult(messages, next_cursor, page)
        raise RuntimeError(f"kf/sync_msg still has more data after {max_pages} pages")

    async def send_text(
        self, open_kfid: str, external_userid: str, content: str
    ) -> str:
        result = await self._api_request(
            "POST",
            "kf/send_msg",
            {
                "touser": external_userid,
                "open_kfid": open_kfid,
                "msgtype": "text",
                "text": {"content": content},
            },
        )
        return str(result.get("msgid", ""))

    async def batch_get_customers(
        self, external_userids: list[str]
    ) -> list[WeComCustomer]:
        if not 1 <= len(external_userids) <= 100:
            raise ValueError("customer batch size must be between 1 and 100")
        if any(
            not isinstance(identifier, str) or not identifier.strip()
            for identifier in external_userids
        ):
            raise ValueError("external_userid cannot be empty")
        identifiers = list(dict.fromkeys(external_userids))

        result = await self._api_request(
            "POST",
            "kf/customer/batchget",
            {
                "external_userid_list": identifiers,
                "need_enter_session_context": 1,
            },
            timeout=httpx.Timeout(10.0, connect=5.0),
            retry_transient=True,
        )
        customer_list = result.get("customer_list", [])
        if not isinstance(customer_list, list):
            raise RuntimeError(
                "kf/customer/batchget returned a non-list customer_list"
            )

        customers: list[WeComCustomer] = []
        for item in customer_list:
            if not isinstance(item, dict):
                continue
            external_value = item.get("external_userid", "")
            external_userid = (
                external_value.strip() if isinstance(external_value, str) else ""
            )
            if not external_userid:
                continue
            context = item.get("enter_session_context", {})
            if not isinstance(context, dict):
                context = {}
            try:
                gender = int(item.get("gender", 0))
            except (TypeError, ValueError):
                gender = 0
            nickname = item.get("nickname", "")
            avatar_url = item.get("avatar", "")
            unionid_value = item.get("unionid", "")
            scene = context.get("scene", "")
            scene_param = context.get("scene_param", "")
            customers.append(
                WeComCustomer(
                    external_userid=external_userid,
                    nickname=nickname if isinstance(nickname, str) else "",
                    avatar_url=avatar_url if isinstance(avatar_url, str) else "",
                    gender=gender,
                    unionid=(
                        unionid_value.strip()
                        if isinstance(unionid_value, str) and unionid_value.strip()
                        else None
                    ),
                    scene=scene if isinstance(scene, str) else "",
                    scene_param=scene_param if isinstance(scene_param, str) else "",
                )
            )
        return customers

    async def _api_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: httpx.Timeout | None = None,
        retry_transient: bool = False,
    ) -> dict[str, Any]:
        force_token_refresh = False
        token_retry_available = True
        transient_retry_available = retry_transient
        while True:
            token = await self._get_access_token(force_refresh=force_token_refresh)
            force_token_refresh = False
            url = f"{API_ROOT}/{path}?{urlencode({'access_token': token})}"
            try:
                request_kwargs: dict[str, Any] = {"json": body}
                if timeout is not None:
                    request_kwargs["timeout"] = timeout
                response = await self._client.request(method, url, **request_kwargs)
                response.raise_for_status()
                result = response.json()
            except httpx.HTTPStatusError as exc:
                if transient_retry_available and exc.response.status_code >= 500:
                    transient_retry_available = False
                    continue
                raise RuntimeError(
                    f"WeCom {path} returned HTTP {exc.response.status_code}"
                ) from exc
            except httpx.RequestError as exc:
                if transient_retry_available:
                    transient_retry_available = False
                    continue
                raise RuntimeError(
                    f"WeCom {path} request failed: {type(exc).__name__}"
                ) from exc
            except ValueError as exc:
                if transient_retry_available:
                    transient_retry_available = False
                    continue
                raise RuntimeError(f"WeCom {path} returned invalid JSON") from exc
            if not isinstance(result, dict):
                raise RuntimeError(f"WeCom {path} returned non-object JSON")
            errcode = int(result.get("errcode", -1))
            if errcode == 0:
                return result
            if errcode in {40014, 42001} and token_retry_available:
                token_retry_available = False
                force_token_refresh = True
                continue
            if errcode == -1 and transient_retry_available:
                transient_retry_available = False
                continue
            raise WeComAPIError(path, errcode, str(result.get("errmsg", "unknown")))

    async def _get_access_token(self, *, force_refresh: bool = False) -> str:
        now = time.monotonic()
        if not force_refresh and self._access_token and now < self._token_expires_at:
            return self._access_token
        async with self._token_lock:
            now = time.monotonic()
            if not force_refresh and self._access_token and now < self._token_expires_at:
                return self._access_token
            query = urlencode({"corpid": self._corp_id, "corpsecret": self._app_secret})
            try:
                response = await self._client.get(f"{API_ROOT}/gettoken?{query}")
                response.raise_for_status()
                result = response.json()
            except httpx.HTTPError as exc:
                raise RuntimeError("WeCom gettoken request failed") from exc
            except ValueError as exc:
                raise RuntimeError("WeCom gettoken returned invalid JSON") from exc
            errcode = int(result.get("errcode", -1))
            if errcode != 0:
                raise WeComAPIError(
                    "gettoken", errcode, str(result.get("errmsg", "unknown"))
                )
            token = str(result.get("access_token", ""))
            if not token:
                raise RuntimeError("WeCom gettoken returned no access_token")
            expires_in = max(int(result.get("expires_in", 7200)), 120)
            self._access_token = token
            self._token_expires_at = time.monotonic() + expires_in - 60
            return token
