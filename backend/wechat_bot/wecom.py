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

    async def _api_request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        for attempt in range(2):
            token = await self._get_access_token(force_refresh=attempt == 1)
            url = f"{API_ROOT}/{path}?{urlencode({'access_token': token})}"
            try:
                response = await self._client.request(method, url, json=body)
                response.raise_for_status()
                result = response.json()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"WeCom {path} returned HTTP {exc.response.status_code}"
                ) from exc
            except httpx.RequestError as exc:
                raise RuntimeError(
                    f"WeCom {path} request failed: {type(exc).__name__}"
                ) from exc
            except ValueError as exc:
                raise RuntimeError(f"WeCom {path} returned invalid JSON") from exc
            if not isinstance(result, dict):
                raise RuntimeError(f"WeCom {path} returned non-object JSON")
            errcode = int(result.get("errcode", -1))
            if errcode == 0:
                return result
            if errcode in {40014, 42001} and attempt == 0:
                continue
            raise WeComAPIError(path, errcode, str(result.get("errmsg", "unknown")))
        raise RuntimeError(f"WeCom {path} token refresh failed")

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
