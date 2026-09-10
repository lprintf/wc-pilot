"""Smoke-test the WeCom WeChat Customer Service API.

The default mode is read-only: acquire an access token, list customer-service
accounts, and pull recent messages. Add --send-test to send a reply explicitly.

Required configuration (read from the repository-root .env by default):

    CorpID=wwxxxxxxxxxxxxxxxx
    APP_AGENT_ID=1000002
    APP_AGENT_SECRET=xxxxxxxxxxxxxxxx

If the application can see more than one customer-service account, also set:

    WECHAT_KF_OPEN_KFID=wkxxxxxxxxxxxxxxxx

Examples (PowerShell, run from the repository root):

    # 1. Read-only smoke test using the default .env file.
    uv run --project backend python backend/scripts/test_wechat_kf_api.py

    # 2. Select a customer-service account on the command line.
    uv run --project backend python backend/scripts/test_wechat_kf_api.py \
        --open-kfid "wkxxxxxxxxxxxxxxxx"

    # 3. Use the temporary token received in a kf_msg_or_event callback.
    #    This is not WECHAT_KF_CALLBACK_TOKEN from the callback configuration.
    uv run --project backend python backend/scripts/test_wechat_kf_api.py \
        --sync-token "callback-temporary-token"

    # 4. Continue pulling from a cursor returned by an earlier run.
    uv run --project backend python backend/scripts/test_wechat_kf_api.py \
        --cursor "previous-next-cursor" --max-pages 50

    # 5. Explicitly reply to the most recent customer message. This sends a
    #    real message, so first run the read-only test and verify the account.
    uv run --project backend python backend/scripts/test_wechat_kf_api.py \
        --send-test --content "这是一条接口联调测试消息"

    # 6. Load configuration from another dotenv file.
    uv run --project backend python backend/scripts/test_wechat_kf_api.py \
        --env-file ".env.test"

When already in the backend directory, the shorter equivalent is:

    uv run python scripts/test_wechat_kf_api.py

Run with --help to see all options. Environment variables override values in
the dotenv file. The script masks identifiers in its normal output.
Use --list-customers to print recent customers' profiles and full external IDs.
Add --database "data/wechat_bot.db" to list all locally saved customers offline.
Use --send-test --external-userid "wm..." to send to a specific customer.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


API_ROOT = "https://qyapi.weixin.qq.com/cgi-bin"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


class ApiError(RuntimeError):
    def __init__(self, operation: str, errcode: int, errmsg: str) -> None:
        hints = {
            60020: "add the current public egress IP to the application's trusted IPs",
            48002: (
                "authorize the application under WeChat Customer Service API and "
                "assign this account to API management"
            ),
            60030: "include the customer-service agent in the application's visibility scope",
            95001: "reply quota exhausted; wait for a new customer message",
            95002: "48-hour reply window expired; wait for a new customer message",
        }
        hint = f"; action: {hints[errcode]}" if errcode in hints else ""
        super().__init__(
            f"{operation} failed: errcode={errcode}, errmsg={errmsg}{hint}"
        )
        self.operation = operation
        self.errcode = errcode
        self.errmsg = errmsg


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test WeCom customer-service API access and message flow."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"dotenv file (default: {DEFAULT_ENV_FILE})",
    )
    parser.add_argument(
        "--open-kfid",
        help="customer-service account ID; defaults to WECHAT_KF_OPEN_KFID",
    )
    parser.add_argument(
        "--sync-token",
        help="temporary Token from a kf_msg_or_event callback (not callback config Token)",
    )
    parser.add_argument(
        "--cursor",
        default="",
        help="sync_msg cursor; empty starts at the earliest message in the last 3 days",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=20,
        help="maximum sync_msg pages to read (default: 20)",
    )
    parser.add_argument(
        "--list-customers",
        action="store_true",
        help="list recent customer profiles and full external_userid values (read-only unless --send-test)",
    )
    parser.add_argument(
        "--database",
        type=Path,
        help="read all saved customers from this SQLite database; requires --list-customers, no API calls",
    )
    parser.add_argument(
        "--external-userid",
        type=str.strip,
        help="target WeChat external_userid, not the admin user ID; with --send-test, skips sync unless --list-customers",
    )
    parser.add_argument(
        "--send-test",
        action="store_true",
        help="send to --external-userid or the most recent customer; omitted is read-only",
    )
    parser.add_argument(
        "--content",
        default="test",
        help="reply text used with --send-test (default: test)",
    )
    args = parser.parse_args()
    if args.external_userid == "":
        parser.error("--external-userid cannot be empty")
    if args.database is not None and (not args.list_customers or args.send_test):
        parser.error("--database requires --list-customers and cannot be combined with --send-test")
    return args


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value

    # Process environment variables take precedence over the dotenv file.
    for key in ("CorpID", "APP_AGENT_ID", "APP_AGENT_SECRET", "WECHAT_KF_OPEN_KFID"):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def require_config(config: dict[str, str], key: str) -> str:
    value = config.get(key, "").strip()
    if not value:
        raise ValueError(f"missing required configuration: {key}")
    return value


def request_json(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    timeout: float = 20,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from WeCom: {payload[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request to WeCom failed: {exc.reason}") from exc

    result = json.loads(payload)
    if not isinstance(result, dict):
        raise RuntimeError("WeCom returned a non-object JSON response")
    return result


def check_api_result(operation: str, result: dict[str, Any]) -> dict[str, Any]:
    errcode = int(result.get("errcode", -1))
    if errcode != 0:
        raise ApiError(operation, errcode, str(result.get("errmsg", "unknown error")))
    return result


def get_access_token(corp_id: str, app_secret: str) -> str:
    query = urllib.parse.urlencode({"corpid": corp_id, "corpsecret": app_secret})
    result = check_api_result(
        "gettoken", request_json("GET", f"{API_ROOT}/gettoken?{query}")
    )
    token = str(result.get("access_token", ""))
    if not token:
        raise RuntimeError("gettoken succeeded without access_token")
    return token


def api_url(path: str, access_token: str) -> str:
    query = urllib.parse.urlencode({"access_token": access_token})
    return f"{API_ROOT}/{path}?{query}"


def list_accounts(access_token: str) -> list[dict[str, Any]]:
    result = check_api_result(
        "kf/account/list", request_json("GET", api_url("kf/account/list", access_token))
    )
    accounts = result.get("account_list", [])
    if not isinstance(accounts, list):
        raise RuntimeError("account_list is not a list")
    return accounts


def mask_identifier(value: str) -> str:
    if len(value) <= 8:
        return "<present>" if value else "<empty>"
    return f"{value[:4]}...{value[-4:]}"


def select_account(
    accounts: list[dict[str, Any]], requested_open_kfid: str
) -> dict[str, Any]:
    if requested_open_kfid:
        for account in accounts:
            if str(account.get("open_kfid", "")) == requested_open_kfid:
                return account
        raise ValueError("the requested open_kfid is not visible to this application")

    if len(accounts) == 1:
        return accounts[0]
    if not accounts:
        raise ValueError("no customer-service accounts are visible to this application")
    raise ValueError(
        "multiple customer-service accounts found; set WECHAT_KF_OPEN_KFID "
        "or pass --open-kfid"
    )


def sync_messages(
    access_token: str,
    open_kfid: str,
    cursor: str,
    sync_token: str | None,
    max_pages: int,
) -> tuple[list[dict[str, Any]], str, int]:
    if max_pages < 1:
        raise ValueError("--max-pages must be at least 1")

    messages: list[dict[str, Any]] = []
    pages = 0
    has_more = 1
    next_cursor = cursor
    while has_more == 1 and pages < max_pages:
        body: dict[str, Any] = {"open_kfid": open_kfid, "limit": 1000}
        if next_cursor:
            body["cursor"] = next_cursor
        if sync_token:
            body["token"] = sync_token

        result = check_api_result(
            "kf/sync_msg",
            request_json("POST", api_url("kf/sync_msg", access_token), body),
        )
        page_messages = result.get("msg_list", [])
        if not isinstance(page_messages, list):
            raise RuntimeError("msg_list is not a list")
        messages.extend(item for item in page_messages if isinstance(item, dict))
        next_cursor = str(result.get("next_cursor", next_cursor))
        has_more = int(result.get("has_more", 0))
        pages += 1

    if has_more == 1:
        raise RuntimeError(
            f"sync_msg still has more data after {max_pages} pages; increase --max-pages"
        )
    return messages, next_cursor, pages


def latest_customer_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        message
        for message in messages
        if int(message.get("origin", 0)) == 3
        and str(message.get("external_userid", ""))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda message: int(message.get("send_time", 0)))


def print_customers(access_token: str, messages: list[dict[str, Any]]) -> None:
    latest: dict[str, int] = {}
    for message in messages:
        if int(message.get("origin", 0)) != 3:
            continue
        external_userid = str(message.get("external_userid", "")).strip()
        if external_userid:
            latest[external_userid] = max(latest.get(external_userid, 0), int(message.get("send_time", 0)))
    identifiers = sorted(latest, key=lambda identifier: (-latest[identifier], identifier))
    print(f"customers: {len(identifiers)} (from pulled messages only; timestamps in UTC)")
    print("external_userid\tnickname\tgender\tlast_message_at")
    for offset in range(0, len(identifiers), 100):
        batch = identifiers[offset:offset + 100]
        profiles: dict[str, dict[str, Any]] = {}
        try:
            result = check_api_result("kf/customer/batchget", request_json(
                "POST", api_url("kf/customer/batchget", access_token),
                {"external_userid_list": batch},
            ))
            customers = result.get("customer_list", [])
            if not isinstance(customers, list):
                raise RuntimeError("customer_list is not a list")
            profiles = {str(customer.get("external_userid", "")): customer
                        for customer in customers if isinstance(customer, dict)}
        except (RuntimeError, ValueError) as exc:
            print(f"warning: profiles unavailable; showing IDs and message times: {exc}", file=sys.stderr)
        for identifier in batch:
            profile = profiles.get(identifier, {})
            # JSON quoting keeps arbitrary nicknames on one terminal line.
            nickname = json.dumps(profile.get("nickname", ""), ensure_ascii=False)
            gender = {"1": "男", "2": "女"}.get(str(profile.get("gender", 0)), "未知")
            timestamp = datetime.fromtimestamp(latest[identifier], timezone.utc).isoformat() if latest[identifier] else "unknown"
            print(f"{identifier}\t{nickname}\t{gender}\t{timestamp}")


def print_database_customers(path: Path, open_kfid: str | None = None) -> None:
    # Read-only URI avoids creating a new empty database for a mistyped path.
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """SELECT i.user_id, i.subject_id, i.external_id,
                      COALESCE(p.nickname, ''), COALESCE(p.gender, 0),
                      COALESCE(p.last_seen_at, i.created_at)
               FROM user_identity i LEFT JOIN customer_profile p ON p.user_id=i.user_id
               WHERE i.provider='wecom_kf' AND (? IS NULL OR i.subject_id=?)
               ORDER BY 6 DESC, i.user_id DESC, i.subject_id""",
            (open_kfid, open_kfid),
        ).fetchall()
    finally:
        connection.close()
    print(f"customers: {len(rows)} (all saved customer identities; cached profiles; timestamps in UTC)")
    print("user_id\topen_kfid\texternal_userid\tnickname\tgender\tlast_seen_at")
    for user_id, account, identifier, nickname, gender, seen_at in rows:
        name = json.dumps(nickname, ensure_ascii=False)
        gender_label = {1: "男", 2: "女"}.get(gender, "未知")
        timestamp = datetime.fromtimestamp(seen_at, timezone.utc).isoformat() if seen_at else "unknown"
        print(f"{user_id}\t{account}\t{identifier}\t{name}\t{gender_label}\t{timestamp}")


def send_text(
    access_token: str, open_kfid: str, external_userid: str, content: str
) -> str:
    if not content:
        raise ValueError("--content cannot be empty")
    result = check_api_result(
        "kf/send_msg",
        request_json(
            "POST",
            api_url("kf/send_msg", access_token),
            {
                "touser": external_userid,
                "open_kfid": open_kfid,
                "msgtype": "text",
                "text": {"content": content},
            },
        ),
    )
    return str(result.get("msgid", ""))


def main() -> int:
    args = parse_args()
    try:
        if args.max_pages < 1:
            raise ValueError("--max-pages must be at least 1")
        if args.database is not None:
            print_database_customers(args.database, args.open_kfid)
            print("send: skipped (read-only database mode; no API calls)")
            return 0
        config = load_env(args.env_file.resolve())
        corp_id = require_config(config, "CorpID")
        app_agent_id = require_config(config, "APP_AGENT_ID")
        app_secret = require_config(config, "APP_AGENT_SECRET")

        print(f"config: ready (application AgentID={app_agent_id})", flush=True)
        access_token = get_access_token(corp_id, app_secret)
        print("gettoken: ok", flush=True)

        accounts = list_accounts(access_token)
        print(f"kf/account/list: ok ({len(accounts)} account(s))")
        for index, account in enumerate(accounts, start=1):
            print(
                f"  account {index}: open_kfid="
                f"{mask_identifier(str(account.get('open_kfid', '')))}"
            )

        requested_open_kfid = (
            args.open_kfid or config.get("WECHAT_KF_OPEN_KFID", "")
        ).strip()
        account = select_account(accounts, requested_open_kfid)
        open_kfid = require_config(
            {"open_kfid": str(account.get("open_kfid", ""))}, "open_kfid"
        )

        messages: list[dict[str, Any]] = []
        if not (args.send_test and args.external_userid) or args.list_customers:
            messages, next_cursor, pages = sync_messages(
                access_token, open_kfid, args.cursor, args.sync_token, args.max_pages,
            )
            customer_messages = [
                message for message in messages if int(message.get("origin", 0)) == 3
            ]
            print(
                f"kf/sync_msg: ok ({pages} page(s), {len(messages)} message(s), "
                f"{len(customer_messages)} customer message(s))"
            )
            print(f"next_cursor: {mask_identifier(next_cursor)}")
        if args.list_customers:
            print_customers(access_token, messages)

        if not args.send_test:
            print("send: skipped (read-only mode; pass --send-test to reply)")
            return 0

        external_userid = args.external_userid
        if not external_userid:
            target = latest_customer_message(messages)
            if target is None:
                raise RuntimeError("no recent customer message is available; pass --external-userid to target a known customer")
            external_userid = str(target["external_userid"])
        print(f"send: target={mask_identifier(external_userid)}", flush=True)
        msgid = send_text(
            access_token,
            open_kfid,
            external_userid,
            args.content,
        )
        print(f"kf/send_msg: ok (msgid returned={bool(msgid)})")
        return 0
    except (ApiError, RuntimeError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
