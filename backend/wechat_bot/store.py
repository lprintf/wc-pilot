"""SQLite store for customer identities, profiles, and conversation state."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from wechat_bot.llm import ChatMessage
from wechat_bot.replies import ReplyBudget


@dataclass(frozen=True, slots=True)
class CustomerProfile:
    user_id: int
    external_userid: str
    nickname: str
    avatar_url: str
    gender: int
    unionid: str | None
    scene: str
    scene_param: str
    first_seen_at: int
    last_seen_at: int
    profile_fetched_at: int | None


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    id: int
    source: str
    started_at: int
    last_message_at: int
    message_count: int
    preview: str


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    sender_type: str
    content: str
    occurred_at: int
    id: int = 0
    message_type: str = "text"
    source: str = "wechat_kf"
    send_status: str = "sent"
    error_message: str | None = None
    client_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class AdminUserSummary:
    id: int
    nickname: str
    avatar_url: str
    last_message_preview: str
    last_active_at: int
    unread_count: int


@dataclass(frozen=True, slots=True)
class AdminMessage:
    id: int
    user_id: int
    sender_type: str
    content: str
    occurred_at: int
    message_type: str
    source: str
    send_status: str
    error_message: str | None
    client_request_id: str | None


class MessageStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._reply_locks: dict[str, asyncio.Lock] = {}
        self._customer_locks: dict[tuple[str, str], asyncio.Lock] = {}
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_user (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_identity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    unionid TEXT,
                    created_at INTEGER NOT NULL,
                    UNIQUE(provider, subject_id, external_id)
                );

                CREATE INDEX IF NOT EXISTS idx_user_identity_user
                ON user_identity(user_id);

                CREATE INDEX IF NOT EXISTS idx_user_identity_unionid
                ON user_identity(unionid)
                WHERE unionid IS NOT NULL;

                CREATE TABLE IF NOT EXISTS customer_profile (
                    user_id INTEGER PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
                    external_userid TEXT NOT NULL,
                    nickname TEXT NOT NULL DEFAULT '',
                    avatar_url TEXT NOT NULL DEFAULT '',
                    gender INTEGER NOT NULL DEFAULT 0,
                    unionid TEXT,
                    scene TEXT NOT NULL DEFAULT '',
                    scene_param TEXT NOT NULL DEFAULT '',
                    first_seen_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    profile_fetched_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS login_ticket (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_hash TEXT NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL,
                    used_at INTEGER,
                    created_at INTEGER NOT NULL,
                    request_ip TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_login_ticket_user_created
                ON login_ticket(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS web_session (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_hash TEXT NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL,
                    revoked_at INTEGER,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_web_session_user
                ON web_session(user_id);

                CREATE TABLE IF NOT EXISTS conversation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
                    source TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(user_id, source, subject_id)
                );

                CREATE INDEX IF NOT EXISTS idx_conversation_user_updated
                ON conversation(user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS kf_cursor (
                    open_kfid TEXT PRIMARY KEY,
                    cursor TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processed_message (
                    msgid TEXT PRIMARY KEY,
                    open_kfid TEXT NOT NULL,
                    external_userid TEXT NOT NULL,
                    user_id INTEGER REFERENCES app_user(id),
                    conversation_id INTEGER REFERENCES conversation(id),
                    send_time INTEGER NOT NULL,
                    customer_content TEXT NOT NULL,
                    reply_content TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('sent', 'ignored')),
                    created_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_processed_customer_time
                ON processed_message(external_userid, send_time DESC);

                CREATE TABLE IF NOT EXISTS conversation_message (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
                    sender_type TEXT NOT NULL CHECK (sender_type IN ('user', 'ai', 'human_agent', 'system')),
                    message_type TEXT NOT NULL DEFAULT 'text',
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_message_id TEXT,
                    send_status TEXT NOT NULL CHECK (send_status IN ('pending', 'sent', 'failed')),
                    error_message TEXT,
                    operator_id TEXT,
                    client_request_id TEXT UNIQUE,
                    occurred_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_message_source
                ON conversation_message(source, source_message_id)
                WHERE source_message_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_conversation_message_conversation
                ON conversation_message(conversation_id, occurred_at, id);
                CREATE INDEX IF NOT EXISTS idx_conversation_message_user
                ON conversation_message(user_id, occurred_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS admin_user_state (
                    user_id INTEGER PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
                    last_read_message_id INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kf_reply_budget (
                    open_kfid TEXT NOT NULL,
                    external_userid TEXT NOT NULL,
                    received_at INTEGER NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0,
                    blocked_code TEXT,
                    PRIMARY KEY(open_kfid, external_userid)
                );
                CREATE TABLE IF NOT EXISTS kf_received_message (
                    msgid TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS customer_management (
                    user_id INTEGER PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
                    nickname TEXT NOT NULL,
                    gender INTEGER NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '[]',
                    updated_at INTEGER NOT NULL,
                    operator_id TEXT NOT NULL
                );
                """
            )
            columns = {
                str(row["name"])
                for row in self._connection.execute(
                    "PRAGMA table_info(processed_message)"
                ).fetchall()
            }
            if "user_id" not in columns:
                self._connection.execute(
                    "ALTER TABLE processed_message "
                    "ADD COLUMN user_id INTEGER REFERENCES app_user(id)"
                )
            if "conversation_id" not in columns:
                self._connection.execute(
                    "ALTER TABLE processed_message "
                    "ADD COLUMN conversation_id INTEGER REFERENCES conversation(id)"
                )
            if "reply_source_message_id" not in columns:
                self._connection.execute("ALTER TABLE processed_message ADD COLUMN reply_source_message_id TEXT")
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_processed_user_time
                ON processed_message(user_id, send_time DESC)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_processed_conversation_time
                ON processed_message(conversation_id, send_time DESC)
                """
            )
            self._backfill_conversations_locked()
            self._backfill_conversation_messages_locked()
            # Seed existing installations once, including ignored non-text messages.
            self._connection.execute("INSERT OR IGNORE INTO kf_received_message SELECT msgid FROM processed_message")
            self._connection.execute(
                """WITH latest AS (
                    SELECT open_kfid, external_userid, MAX(user_id) AS user_id,
                           MAX(send_time) AS received_at
                    FROM processed_message GROUP BY open_kfid, external_userid
                )
                INSERT OR IGNORE INTO kf_reply_budget(open_kfid, external_userid, received_at, used)
                SELECT l.open_kfid, l.external_userid, l.received_at,
                    (SELECT COUNT(*) FROM processed_message p
                     WHERE p.open_kfid=l.open_kfid AND p.external_userid=l.external_userid
                       AND p.status='sent' AND p.created_at >= l.received_at)
                    + (SELECT COUNT(*) FROM conversation_message m
                       JOIN conversation c ON c.id=m.conversation_id
                       WHERE c.subject_id=l.open_kfid AND m.user_id=l.user_id
                         AND m.sender_type='human_agent' AND m.send_status='sent'
                         AND m.updated_at >= l.received_at)
                FROM latest l"""
            )

    def reply_lock(self, open_kfid: str) -> asyncio.Lock:
        return self._reply_locks.setdefault(open_kfid, asyncio.Lock())

    def customer_lock(self, open_kfid: str, external_userid: str) -> asyncio.Lock:
        return self._customer_locks.setdefault((open_kfid, external_userid), asyncio.Lock())

    def observe_customer_message(self, open_kfid: str, external_userid: str, msgid: str, send_time: int) -> None:
        if not msgid or not external_userid or send_time <= 0:
            return
        with self._lock, self._connection:
            inserted = self._connection.execute(
                "INSERT OR IGNORE INTO kf_received_message(msgid) VALUES (?)", (msgid,)
            ).rowcount
            if inserted:
                self._connection.execute(
                    """INSERT INTO kf_reply_budget(open_kfid, external_userid, received_at, used)
                    VALUES (?, ?, ?, 0) ON CONFLICT(open_kfid, external_userid) DO UPDATE SET
                    received_at=excluded.received_at, used=0, blocked_code=NULL
                    WHERE excluded.received_at >= kf_reply_budget.received_at""",
                    (open_kfid, external_userid, send_time),
                )

    def reply_budget(self, open_kfid: str, external_userid: str) -> ReplyBudget:
        with self._lock:
            row = self._connection.execute(
                "SELECT received_at, used, blocked_code FROM kf_reply_budget WHERE open_kfid=? AND external_userid=?",
                (open_kfid, external_userid),
            ).fetchone()
        return ReplyBudget(int(row["received_at"]), int(row["used"]), row["blocked_code"]) if row else ReplyBudget(0, 0)

    def consume_reply(self, open_kfid: str, external_userid: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE kf_reply_budget SET used=used+1 WHERE open_kfid=? AND external_userid=?",
                (open_kfid, external_userid),
            )

    def reconcile_reply_usage(self, open_kfid: str, external_userid: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE kf_reply_budget SET used=MAX(used,
                    (SELECT COUNT(*) FROM processed_message p WHERE p.open_kfid=kf_reply_budget.open_kfid
                     AND p.external_userid=kf_reply_budget.external_userid AND p.status='sent'
                     AND p.created_at>=kf_reply_budget.received_at)
                    + (SELECT COUNT(*) FROM conversation_message m JOIN conversation c ON c.id=m.conversation_id
                       JOIN user_identity i ON i.user_id=m.user_id AND i.subject_id=c.subject_id AND i.provider='wecom_kf'
                       WHERE i.subject_id=kf_reply_budget.open_kfid AND i.external_id=kf_reply_budget.external_userid
                         AND m.sender_type='human_agent' AND m.send_status='sent' AND m.updated_at>=kf_reply_budget.received_at))
                    WHERE open_kfid=? AND external_userid=?""", (open_kfid, external_userid),
            )

    def exhaust_reply_budget(self, open_kfid: str, external_userid: str, *, expired: bool) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE kf_reply_budget SET used=5, blocked_code=? WHERE open_kfid=? AND external_userid=?",
                ("reply_window_expired" if expired else "reply_limit_reached", open_kfid, external_userid),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def get_cursor(self, open_kfid: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT cursor FROM kf_cursor WHERE open_kfid = ?", (open_kfid,)
            ).fetchone()
        return str(row["cursor"]) if row else None

    def set_cursor(self, open_kfid: str, cursor: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO kf_cursor(open_kfid, cursor, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(open_kfid) DO UPDATE SET
                    cursor = excluded.cursor,
                    updated_at = excluded.updated_at
                """,
                (open_kfid, cursor, int(time.time())),
            )

    def is_processed(self, msgid: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM processed_message WHERE msgid = ?", (msgid,)
            ).fetchone()
        return row is not None

    def get_or_create_customer(
        self, open_kfid: str, external_userid: str, *, seen_at: int
    ) -> int:
        if not open_kfid or not external_userid:
            raise ValueError("open_kfid and external_userid are required")
        observed_at = seen_at if seen_at > 0 else int(time.time())
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT user_id
                FROM user_identity
                WHERE provider = 'wecom_kf'
                  AND subject_id = ?
                  AND external_id = ?
                """,
                (open_kfid, external_userid),
            ).fetchone()
            if row is None:
                cursor = self._connection.execute(
                    "INSERT INTO app_user(created_at, updated_at) VALUES (?, ?)",
                    (observed_at, observed_at),
                )
                user_id = int(cursor.lastrowid)
                self._connection.execute(
                    """
                    INSERT INTO user_identity(
                        user_id, provider, subject_id, external_id, created_at
                    ) VALUES (?, 'wecom_kf', ?, ?, ?)
                    """,
                    (user_id, open_kfid, external_userid, observed_at),
                )
            else:
                user_id = int(row["user_id"])
                self._connection.execute(
                    """
                    UPDATE app_user
                    SET updated_at = MAX(updated_at, ?)
                    WHERE id = ?
                    """,
                    (observed_at, user_id),
                )

            conversation_id = self._get_or_create_conversation_locked(
                user_id=user_id,
                open_kfid=open_kfid,
                occurred_at=observed_at,
            )

            self._connection.execute(
                """
                INSERT INTO customer_profile(
                    user_id, external_userid, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_seen_at = MIN(
                        customer_profile.first_seen_at, excluded.first_seen_at
                    ),
                    last_seen_at = MAX(customer_profile.last_seen_at, excluded.last_seen_at)
                """,
                (user_id, external_userid, observed_at, observed_at),
            )
            self._connection.execute(
                """
                UPDATE processed_message
                SET user_id = ?, conversation_id = ?
                WHERE user_id IS NULL
                  AND open_kfid = ?
                  AND external_userid = ?
                """,
                (user_id, conversation_id, open_kfid, external_userid),
            )
            self._connection.execute(
                """
                UPDATE processed_message
                SET conversation_id = ?
                WHERE conversation_id IS NULL
                  AND user_id = ?
                  AND open_kfid = ?
                """,
                (conversation_id, user_id, open_kfid),
            )
            self._backfill_conversation_messages_locked()
        return user_id

    def profile_needs_refresh(
        self, user_id: int, *, max_age_seconds: int, now: int | None = None
    ) -> bool:
        current_time = int(time.time()) if now is None else now
        with self._lock:
            row = self._connection.execute(
                """
                SELECT profile_fetched_at
                FROM customer_profile
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if row is None or row["profile_fetched_at"] is None:
            return True
        return int(row["profile_fetched_at"]) <= current_time - max_age_seconds

    def update_customer_profile(
        self,
        *,
        user_id: int,
        external_userid: str,
        nickname: str,
        avatar_url: str,
        gender: int,
        unionid: str | None,
        scene: str,
        scene_param: str,
        fetched_at: int | None = None,
    ) -> None:
        timestamp = int(time.time()) if fetched_at is None else fetched_at
        with self._lock, self._connection:
            updated = self._connection.execute(
                """
                UPDATE customer_profile
                SET nickname = ?, avatar_url = ?, gender = ?, unionid = ?,
                    scene = ?, scene_param = ?, profile_fetched_at = ?
                WHERE user_id = ? AND external_userid = ?
                """,
                (
                    nickname,
                    avatar_url,
                    gender,
                    unionid,
                    scene,
                    scene_param,
                    timestamp,
                    user_id,
                    external_userid,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("customer profile identity does not match")
            self._connection.execute(
                """
                UPDATE user_identity
                SET unionid = ?
                WHERE user_id = ? AND provider = 'wecom_kf'
                """,
                (unionid, user_id),
            )

    def mark_profiles_fetched(
        self, user_ids: list[int], *, fetched_at: int | None = None
    ) -> None:
        if not user_ids:
            return
        timestamp = int(time.time()) if fetched_at is None else fetched_at
        with self._lock, self._connection:
            self._connection.executemany(
                """
                UPDATE customer_profile
                SET profile_fetched_at = ?
                WHERE user_id = ?
                """,
                [(timestamp, user_id) for user_id in user_ids],
            )

    def get_customer_profile(self, user_id: int) -> CustomerProfile | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM customer_profile WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return None
        return CustomerProfile(
            user_id=int(row["user_id"]),
            external_userid=str(row["external_userid"]),
            nickname=str(row["nickname"]),
            avatar_url=str(row["avatar_url"]),
            gender=int(row["gender"]),
            unionid=str(row["unionid"]) if row["unionid"] is not None else None,
            scene=str(row["scene"]),
            scene_param=str(row["scene_param"]),
            first_seen_at=int(row["first_seen_at"]),
            last_seen_at=int(row["last_seen_at"]),
            profile_fetched_at=(
                int(row["profile_fetched_at"])
                if row["profile_fetched_at"] is not None
                else None
            ),
        )

    def create_login_ticket(
        self,
        *,
        user_id: int,
        token_hash: str,
        created_at: int,
        expires_at: int,
        min_interval_seconds: int,
    ) -> bool:
        with self._lock, self._connection:
            recent = self._connection.execute(
                """
                SELECT 1
                FROM login_ticket
                WHERE user_id = ? AND created_at > ?
                LIMIT 1
                """,
                (user_id, created_at - min_interval_seconds),
            ).fetchone()
            if recent is not None:
                return False
            self._connection.execute(
                """
                UPDATE login_ticket
                SET used_at = ?
                WHERE user_id = ? AND used_at IS NULL AND expires_at > ?
                """,
                (created_at, user_id, created_at),
            )
            self._connection.execute(
                """
                INSERT INTO login_ticket(
                    token_hash, user_id, expires_at, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (token_hash, user_id, expires_at, created_at),
            )
        return True

    def delete_unused_login_ticket(self, token_hash: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM login_ticket WHERE token_hash = ? AND used_at IS NULL",
                (token_hash,),
            )

    def consume_login_ticket(
        self,
        *,
        token_hash: str,
        used_at: int,
        session_hash: str,
        session_expires_at: int,
        previous_session_hash: str | None,
    ) -> int | None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT user_id FROM login_ticket WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            updated = self._connection.execute(
                """
                UPDATE login_ticket
                SET used_at = ?
                WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?
                """,
                (used_at, token_hash, used_at),
            )
            if updated.rowcount != 1:
                return None
            user_id = int(row["user_id"])
            self._connection.execute(
                """
                INSERT INTO web_session(
                    session_hash, user_id, expires_at, created_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_hash,
                    user_id,
                    session_expires_at,
                    used_at,
                    used_at,
                ),
            )
            if previous_session_hash is not None:
                self._connection.execute(
                    """
                    UPDATE web_session
                    SET revoked_at = ?
                    WHERE session_hash = ? AND revoked_at IS NULL
                    """,
                    (used_at, previous_session_hash),
                )
        return user_id

    def get_session_user(self, session_hash: str, *, accessed_at: int) -> int | None:
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT user_id
                FROM web_session
                WHERE session_hash = ?
                  AND revoked_at IS NULL
                  AND expires_at > ?
                """,
                (session_hash, accessed_at),
            ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                """
                UPDATE web_session
                SET last_seen_at = ?
                WHERE session_hash = ?
                """,
                (accessed_at, session_hash),
            )
        return int(row["user_id"])

    def revoke_session(self, session_hash: str, *, revoked_at: int | None = None) -> None:
        timestamp = int(time.time()) if revoked_at is None else revoked_at
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE web_session
                SET revoked_at = ?
                WHERE session_hash = ? AND revoked_at IS NULL
                """,
                (timestamp, session_hash),
            )

    def list_conversations(self, user_id: int) -> list[ConversationSummary]:
        with self._lock:
            conversation_rows = self._connection.execute(
                """
                SELECT id, source, created_at, updated_at
                FROM conversation
                WHERE user_id = ?
                ORDER BY updated_at DESC, id DESC
                """,
                (user_id,),
            ).fetchall()
            summaries: list[ConversationSummary] = []
            for conversation in conversation_rows:
                message_row = self._connection.execute(
                    """SELECT content, occurred_at, created_at
                       FROM conversation_message
                       WHERE conversation_id = ? AND sender_type = 'user' AND send_status = 'sent'
                       ORDER BY occurred_at DESC, id DESC LIMIT 1""",
                    (int(conversation["id"]),),
                ).fetchone()
                count_row = self._connection.execute(
                    """SELECT COUNT(1) AS message_count
                       FROM conversation_message
                       WHERE conversation_id = ? AND send_status = 'sent'""",
                    (int(conversation["id"]),),
                ).fetchone()
                if message_row is None or count_row is None:
                    continue
                preview = str(message_row["content"]).strip()
                summaries.append(
                    ConversationSummary(
                        id=int(conversation["id"]),
                        source=str(conversation["source"]),
                        started_at=int(conversation["created_at"]),
                        last_message_at=max(
                            int(message_row["occurred_at"]),
                            int(message_row["created_at"]),
                        ),
                        message_count=int(count_row["message_count"]),
                        preview=preview,
                    )
                )
        return summaries

    def conversation_messages(
        self, user_id: int, conversation_id: int, *, turns: int = 100
    ) -> list[ConversationMessage] | None:
        if turns < 1:
            raise ValueError("turns must be positive")
        with self._lock:
            conversation = self._connection.execute(
                """
                SELECT 1
                FROM conversation
                WHERE id = ? AND user_id = ?
                """,
                (conversation_id, user_id),
            ).fetchone()
            if conversation is None:
                return None
            rows = self._connection.execute(
                """SELECT id, sender_type, content, occurred_at, message_type,
                          source, send_status, error_message, client_request_id
                   FROM conversation_message
                   WHERE conversation_id = ? AND send_status = 'sent'
                   ORDER BY occurred_at DESC, id DESC LIMIT ?""",
                (conversation_id, turns),
            ).fetchall()

        messages: list[ConversationMessage] = []
        for row in reversed(rows):
            messages.append(ConversationMessage(
                sender_type={"user": "customer", "ai": "assistant"}.get(str(row["sender_type"]), str(row["sender_type"])),
                content=str(row["content"]),
                occurred_at=int(row["occurred_at"]),
                id=int(row["id"]),
                message_type=str(row["message_type"]),
                source=str(row["source"]),
                send_status=str(row["send_status"]),
                error_message=(str(row["error_message"]) if row["error_message"] is not None else None),
                client_request_id=(str(row["client_request_id"]) if row["client_request_id"] is not None else None),
            ))
        return messages

    def mark_sent(
        self,
        *,
        msgid: str,
        user_id: int,
        open_kfid: str,
        external_userid: str,
        send_time: int,
        customer_content: str,
        reply_content: str,
        reply_source_message_id: str | None = None,
    ) -> None:
        self._record(
            msgid,
            user_id,
            open_kfid,
            external_userid,
            send_time,
            customer_content,
            reply_content,
            "sent",
            reply_source_message_id,
        )

    def mark_batch_sent(
        self, *, messages: list[tuple[str, int, str]], user_id: int,
        open_kfid: str, external_userid: str, reply_content: str,
        reply_source_message_id: str,
    ) -> None:
        """Save all inbound messages and the single reply in one transaction."""
        with self._lock, self._connection:
            for index, (msgid, send_time, content) in enumerate(messages):
                is_last = index == len(messages) - 1
                self._record_locked(
                    msgid, user_id, open_kfid, external_userid, send_time, content,
                    reply_content if is_last else "", "sent" if is_last else "ignored",
                    reply_source_message_id if is_last else None,
                )

    def mark_ignored(
        self,
        *,
        msgid: str,
        user_id: int,
        open_kfid: str,
        external_userid: str,
        send_time: int,
    ) -> None:
        self._record(
            msgid,
            user_id,
            open_kfid,
            external_userid,
            send_time,
            "",
            "",
            "ignored",
        )

    def mark_unanswered(
        self, *, msgid: str, user_id: int, open_kfid: str,
        external_userid: str, send_time: int, customer_content: str,
    ) -> None:
        self._record(msgid, user_id, open_kfid, external_userid, send_time,
                     customer_content, "", "ignored")

    def history(self, user_id: int, *, turns: int = 6, timestamped: bool = False) -> list[ChatMessage]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT sender_type, content, occurred_at
                FROM conversation_message
                WHERE user_id = ? AND send_status = 'sent'
                ORDER BY occurred_at DESC, id DESC
                LIMIT ?
                """,
                (user_id, turns * 2),
            ).fetchall()
        history: list[ChatMessage] = []
        for row in reversed(rows):
            role = "user" if str(row["sender_type"]) == "user" else "assistant"
            history.append(ChatMessage(
                role, str(row["content"]),
                int(row["occurred_at"]) if timestamped else None,
                {"user": "客户", "human_agent": "人工客服", "ai": "AI"}.get(str(row["sender_type"]), "系统") if timestamped else "",
            ))
        return history

    def _record(
        self,
        msgid: str,
        user_id: int,
        open_kfid: str,
        external_userid: str,
        send_time: int,
        customer_content: str,
        reply_content: str,
        status: str,
        reply_source_message_id: str | None = None,
    ) -> None:
        with self._lock, self._connection:
            self._record_locked(msgid, user_id, open_kfid, external_userid, send_time,
                                customer_content, reply_content, status, reply_source_message_id)

    def _record_locked(
        self,
        msgid: str,
        user_id: int,
        open_kfid: str,
        external_userid: str,
        send_time: int,
        customer_content: str,
        reply_content: str,
        status: str,
        reply_source_message_id: str | None = None,
    ) -> None:
        recorded_at = int(time.time())
        conversation_id = self._get_or_create_conversation_locked(
            user_id=user_id,
            open_kfid=open_kfid,
            occurred_at=send_time if send_time > 0 else recorded_at,
        )
        inserted = self._connection.execute(
            """
            INSERT OR IGNORE INTO processed_message(
                msgid, user_id, conversation_id, open_kfid,
                external_userid, send_time,
                customer_content, reply_content, status, created_at, reply_source_message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                msgid,
                user_id,
                conversation_id,
                open_kfid,
                external_userid,
                send_time,
                customer_content,
                reply_content,
                status,
                recorded_at,
                reply_source_message_id,
            ),
        ).rowcount
        if not inserted:
            return
        self._connection.execute(
            """
            UPDATE conversation
            SET updated_at = MAX(updated_at, ?)
            WHERE id = ?
            """,
            (recorded_at, conversation_id),
        )
        if customer_content:
            self._connection.execute(
                """INSERT OR IGNORE INTO conversation_message(
                   conversation_id, user_id, sender_type, message_type, content,
                   source, source_message_id, send_status, occurred_at, created_at, updated_at
                ) VALUES (?, ?, 'user', 'text', ?, 'wechat_kf', ?, 'sent', ?, ?, ?)""",
                (conversation_id, user_id, customer_content, msgid,
                 send_time if send_time > 0 else recorded_at, recorded_at, recorded_at),
            )
        if status == "sent":
            self._connection.execute(
                """INSERT OR IGNORE INTO conversation_message(
                   conversation_id, user_id, sender_type, message_type, content,
                   source, source_message_id, send_status, occurred_at, created_at, updated_at
                ) VALUES (?, ?, 'ai', 'text', ?, 'llm', ?, 'sent', ?, ?, ?)""",
                (conversation_id, user_id, reply_content, reply_source_message_id or f"legacy-reply:{msgid}",
                 recorded_at, recorded_at, recorded_at),
            )

    def _get_or_create_conversation_locked(
        self, *, user_id: int, open_kfid: str, occurred_at: int
    ) -> int:
        self._connection.execute(
            """
            INSERT INTO conversation(
                user_id, source, subject_id, created_at, updated_at
            ) VALUES (?, 'wechat_kf', ?, ?, ?)
            ON CONFLICT(user_id, source, subject_id) DO UPDATE SET
                created_at = MIN(conversation.created_at, excluded.created_at),
                updated_at = MAX(conversation.updated_at, excluded.updated_at)
            """,
            (user_id, open_kfid, occurred_at, occurred_at),
        )
        row = self._connection.execute(
            """
            SELECT id
            FROM conversation
            WHERE user_id = ? AND source = 'wechat_kf' AND subject_id = ?
            """,
            (user_id, open_kfid),
        ).fetchone()
        if row is None:
            raise RuntimeError("conversation could not be created")
        return int(row["id"])

    def _backfill_conversations_locked(self) -> None:
        rows = self._connection.execute(
            """
            SELECT user_id, open_kfid, MIN(send_time) AS first_time,
                   MAX(MAX(send_time, created_at)) AS last_time
            FROM processed_message
            WHERE user_id IS NOT NULL
            GROUP BY user_id, open_kfid
            """
        ).fetchall()
        for row in rows:
            user_id = int(row["user_id"])
            open_kfid = str(row["open_kfid"])
            conversation_id = self._get_or_create_conversation_locked(
                user_id=user_id,
                open_kfid=open_kfid,
                occurred_at=int(row["first_time"]),
            )
            self._connection.execute(
                """
                UPDATE conversation
                SET updated_at = MAX(updated_at, ?)
                WHERE id = ?
                """,
                (int(row["last_time"]), conversation_id),
            )
            self._connection.execute(
                """
                UPDATE processed_message
                SET conversation_id = ?
                WHERE conversation_id IS NULL
                  AND user_id = ?
                  AND open_kfid = ?
                """,
                (conversation_id, user_id, open_kfid),
            )

    def _backfill_conversation_messages_locked(self) -> None:
        rows = self._connection.execute(
            """SELECT msgid, user_id, conversation_id, customer_content,
                      reply_content, send_time, created_at, reply_source_message_id
               FROM processed_message
               WHERE status = 'sent' AND user_id IS NOT NULL
                 AND conversation_id IS NOT NULL"""
        ).fetchall()
        for row in rows:
            msgid = str(row["msgid"])
            conversation_id = int(row["conversation_id"])
            user_id = int(row["user_id"])
            send_time = int(row["send_time"])
            created_at = int(row["created_at"])
            reply_id = row["reply_source_message_id"]
            if not reply_id:
                # Old versions stored the platform ID only in conversation_message.
                # Recover it only when timestamp, body and conversation identify a
                # single real delivery; never collapse distinct actual sends.
                matches = self._connection.execute(
                    """SELECT source_message_id FROM conversation_message
                       WHERE conversation_id=? AND user_id=? AND sender_type='ai'
                         AND source='llm' AND content=? AND occurred_at=?
                         AND source_message_id NOT LIKE 'legacy-reply:%'""",
                    (conversation_id, user_id, row["reply_content"], created_at),
                ).fetchall()
                if len(matches) == 1:
                    reply_id = str(matches[0]["source_message_id"])
                    self._connection.execute("UPDATE processed_message SET reply_source_message_id=? WHERE msgid=?", (reply_id, msgid))
            if reply_id:
                self._connection.execute(
                    """DELETE FROM conversation_message WHERE source='llm'
                       AND source_message_id=? AND conversation_id=? AND user_id=?
                       AND content=? AND occurred_at=?""",
                    (f"legacy-reply:{msgid}", conversation_id, user_id, row["reply_content"], created_at),
                )
            self._connection.execute(
                """INSERT OR IGNORE INTO conversation_message(
                   conversation_id, user_id, sender_type, message_type, content,
                   source, source_message_id, send_status, occurred_at, created_at, updated_at
                ) VALUES (?, ?, 'user', 'text', ?, 'wechat_kf', ?, 'sent', ?, ?, ?)""",
                (conversation_id, user_id, str(row["customer_content"]), msgid,
                 send_time, created_at, created_at),
            )
            self._connection.execute(
                """INSERT OR IGNORE INTO conversation_message(
                   conversation_id, user_id, sender_type, message_type, content,
                   source, source_message_id, send_status, occurred_at, created_at, updated_at
                ) VALUES (?, ?, 'ai', 'text', ?, 'llm', ?, 'sent', ?, ?, ?)""",
                (conversation_id, user_id, str(row["reply_content"]), reply_id or f"legacy-reply:{msgid}",
                 created_at, created_at, created_at),
            )

    def list_admin_users(
        self, open_kfid: str, *, search: str = "", page: int = 1,
        page_size: int = 50, sort: str = "recent"
    ) -> tuple[list[AdminUserSummary], int]:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        pattern = f"%{search.strip()}%"
        order = "last_active_at DESC, u.id DESC" if sort != "oldest" else "last_active_at ASC, u.id ASC"
        if sort == "unread":
            order = "unread_count DESC, last_active_at DESC, u.id DESC"
        with self._lock:
            rows = self._connection.execute(
                f"""SELECT u.id, COALESCE(cmgt.nickname, p.nickname) AS nickname, p.avatar_url,
                    COALESCE((SELECT cm.content FROM conversation_message cm
                      WHERE cm.user_id=u.id AND cm.send_status='sent'
                      ORDER BY cm.occurred_at DESC, cm.id DESC LIMIT 1), '') AS preview,
                    COALESCE((SELECT MAX(cm.occurred_at) FROM conversation_message cm
                      WHERE cm.user_id=u.id AND cm.send_status='sent'), p.last_seen_at) AS last_active_at,
                    COALESCE((SELECT COUNT(1) FROM conversation_message cm
                      WHERE cm.user_id=u.id AND cm.sender_type='user' AND cm.send_status='sent'
                        AND cm.id > COALESCE((SELECT last_read_message_id FROM admin_user_state s WHERE s.user_id=u.id),0)),0) AS unread_count
                    FROM app_user u JOIN customer_profile p ON p.user_id=u.id
                    LEFT JOIN customer_management cmgt ON cmgt.user_id=u.id
                    JOIN user_identity i ON i.user_id=u.id AND i.provider='wecom_kf' AND i.subject_id=?
                    WHERE (?='' OR COALESCE(cmgt.nickname, p.nickname) LIKE ? OR CAST(u.id AS TEXT) LIKE ?)
                    ORDER BY {order} LIMIT ? OFFSET ?""",
                (open_kfid, search.strip(), pattern, pattern, page_size, (page - 1) * page_size),
            ).fetchall()
            total = int(self._connection.execute(
                """SELECT COUNT(1) FROM app_user u JOIN customer_profile p ON p.user_id=u.id
                   LEFT JOIN customer_management cmgt ON cmgt.user_id=u.id
                   JOIN user_identity i ON i.user_id=u.id AND i.provider='wecom_kf' AND i.subject_id=?
                   WHERE (?='' OR COALESCE(cmgt.nickname, p.nickname) LIKE ? OR CAST(u.id AS TEXT) LIKE ?)""",
                (open_kfid, search.strip(), pattern, pattern),
            ).fetchone()[0])
        return [AdminUserSummary(int(r["id"]), str(r["nickname"]), str(r["avatar_url"]),
                                 str(r["preview"]), int(r["last_active_at"]), int(r["unread_count"])) for r in rows], total

    def get_admin_user(self, user_id: int, open_kfid: str) -> CustomerProfile | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT 1 FROM user_identity WHERE user_id=? AND provider='wecom_kf' AND subject_id=?""",
                (user_id, open_kfid),
            ).fetchone()
        profile = self.get_customer_profile(user_id) if row else None
        if profile is not None:
            with self._lock:
                override = self._connection.execute("SELECT nickname, gender FROM customer_management WHERE user_id=?", (user_id,)).fetchone()
            if override:
                profile = replace(profile, nickname=str(override["nickname"]), gender=int(override["gender"]))
        return profile

    def customer_management(self, user_id: int) -> dict[str, object]:
        with self._lock:
            row = self._connection.execute("SELECT notes, tags FROM customer_management WHERE user_id=?", (user_id,)).fetchone()
        return {"notes": str(row["notes"]), "tags": json.loads(row["tags"])} if row else {"notes": "", "tags": []}

    def update_managed_customer(self, user_id: int, open_kfid: str, *, nickname: str, gender: int, notes: str, tags: list[str], operator_id: str) -> None:
        with self._lock, self._connection:
            if not self._connection.execute("SELECT 1 FROM user_identity WHERE user_id=? AND subject_id=? AND provider='wecom_kf'", (user_id, open_kfid)).fetchone():
                raise LookupError("user not found")
            self._connection.execute(
                """INSERT INTO customer_management(user_id,nickname,gender,notes,tags,updated_at,operator_id)
                   VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                   nickname=excluded.nickname,gender=excluded.gender,notes=excluded.notes,tags=excluded.tags,
                   updated_at=excluded.updated_at,operator_id=excluded.operator_id""",
                (user_id, nickname, gender, notes, json.dumps(tags, ensure_ascii=False), int(time.time()), operator_id),
            )

    def delete_managed_customer(self, user_id: int, open_kfid: str) -> None:
        with self._lock, self._connection:
            identities = self._connection.execute("SELECT provider,subject_id FROM user_identity WHERE user_id=?", (user_id,)).fetchall()
            if not any(row["provider"] == "wecom_kf" and row["subject_id"] == open_kfid for row in identities):
                raise LookupError("user not found")
            if any(row["provider"] != "wecom_kf" or row["subject_id"] != open_kfid for row in identities):
                raise ValueError("客户关联其他账号，暂不支持删除")
            if self._connection.execute("SELECT 1 FROM conversation_message WHERE user_id=? AND send_status='pending'", (user_id,)).fetchone():
                raise ValueError("客户有正在发送的消息，请稍后再删除")
            # Keep content-free deduplication and quota records: deletion must not
            # make old callbacks resend or create extra WeChat reply capacity.
            self._connection.execute("INSERT OR IGNORE INTO kf_received_message SELECT msgid FROM processed_message WHERE user_id=?", (user_id,))
            self._connection.execute("UPDATE processed_message SET user_id=NULL, conversation_id=NULL, customer_content='', reply_content='', status='ignored' WHERE user_id=?", (user_id,))
            self._connection.execute("DELETE FROM app_user WHERE id=?", (user_id,))

    def get_customer_external_userid(self, user_id: int, open_kfid: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT external_id FROM user_identity
                   WHERE user_id=? AND provider='wecom_kf' AND subject_id=?""",
                (user_id, open_kfid),
            ).fetchone()
        return str(row["external_id"]) if row else None

    def admin_conversation_messages(self, user_id: int, open_kfid: str, *, before_id: int | None = None, limit: int = 50) -> tuple[list[AdminMessage], bool]:
        if self.get_admin_user(user_id, open_kfid) is None:
            return [], False
        limit = min(max(limit, 1), 100)
        with self._lock:
            params: list[object] = [user_id, limit + 1]
            where = "user_id=? AND send_status IN ('pending','sent','failed')"
            if before_id is not None:
                where += " AND id < ?"
                params = [user_id, before_id, limit + 1]
            rows = self._connection.execute(
                f"""SELECT id,user_id,sender_type,content,occurred_at,message_type,source,send_status,error_message,client_request_id
                    FROM conversation_message WHERE {where} ORDER BY occurred_at DESC, id DESC LIMIT ?""", params
            ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()
        return [AdminMessage(int(r["id"]), int(r["user_id"]), str(r["sender_type"]), str(r["content"]), int(r["occurred_at"]), str(r["message_type"]), str(r["source"]), str(r["send_status"]), str(r["error_message"]) if r["error_message"] is not None else None, str(r["client_request_id"]) if r["client_request_id"] is not None else None) for r in rows], has_more

    def mark_admin_user_read(self, user_id: int, message_id: int | None = None) -> None:
        with self._lock, self._connection:
            if message_id is None:
                row = self._connection.execute("SELECT COALESCE(MAX(id),0) FROM conversation_message WHERE user_id=?", (user_id,)).fetchone()
                message_id = int(row[0]) if row else 0
            self._connection.execute("""INSERT INTO admin_user_state(user_id,last_read_message_id,updated_at) VALUES (?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET last_read_message_id=MAX(admin_user_state.last_read_message_id,excluded.last_read_message_id),updated_at=excluded.updated_at""", (user_id, message_id, int(time.time())))

    def get_admin_message_by_request(self, user_id: int, request_id: str) -> AdminMessage | None:
        with self._lock:
            row = self._connection.execute("""SELECT id,user_id,sender_type,content,occurred_at,message_type,source,send_status,error_message,client_request_id FROM conversation_message WHERE user_id=? AND client_request_id=?""", (user_id, request_id)).fetchone()
        return self._admin_message_from_row(row) if row else None

    def create_admin_message(self, *, user_id: int, open_kfid: str, content: str, operator_id: str, request_id: str, retry_message_id: int | None = None) -> tuple[AdminMessage, bool]:
        now = int(time.time())
        with self._lock, self._connection:
            profile = self._connection.execute("""SELECT external_userid FROM customer_profile p JOIN user_identity i ON i.user_id=p.user_id WHERE p.user_id=? AND i.provider='wecom_kf' AND i.subject_id=?""", (user_id, open_kfid)).fetchone()
            if profile is None:
                raise LookupError("user not found")
            if retry_message_id is not None:
                row = self._connection.execute("SELECT * FROM conversation_message WHERE id=? AND user_id=? AND sender_type='human_agent'", (retry_message_id,user_id)).fetchone()
                if row is None or str(row["send_status"]) != "failed":
                    raise ValueError("message cannot be retried")
                self._connection.execute("UPDATE conversation_message SET send_status='pending',error_message=NULL,updated_at=? WHERE id=?", (now,retry_message_id))
                row = self._connection.execute("SELECT * FROM conversation_message WHERE id=?", (retry_message_id,)).fetchone()
                return self._admin_message_from_row(row), True
            existing = self._connection.execute("SELECT * FROM conversation_message WHERE client_request_id=?", (request_id,)).fetchone()
            if existing is not None:
                if int(existing["user_id"]) != user_id or str(existing["content"]) != content:
                    raise ValueError("request id already used")
                return self._admin_message_from_row(existing), False
            conversation_id = self._get_or_create_conversation_locked(user_id=user_id, open_kfid=open_kfid, occurred_at=now)
            cur = self._connection.execute("""INSERT INTO conversation_message(conversation_id,user_id,sender_type,message_type,content,source,send_status,operator_id,client_request_id,occurred_at,created_at,updated_at) VALUES (?,?, 'human_agent','text',?,'admin','pending',?,?,?,?,?)""", (conversation_id,user_id,content,operator_id,request_id,now,now,now))
            row = self._connection.execute("SELECT * FROM conversation_message WHERE id=?", (cur.lastrowid,)).fetchone()
            return self._admin_message_from_row(row), True

    def complete_admin_message(self, message_id: int, *, status: str, source_message_id: str | None = None, error_message: str | None = None) -> None:
        if status not in {"sent", "failed"}:
            raise ValueError("invalid send status")
        with self._lock, self._connection:
            self._connection.execute("UPDATE conversation_message SET send_status=?,source_message_id=COALESCE(?,source_message_id),error_message=?,updated_at=? WHERE id=? AND sender_type='human_agent'", (status,source_message_id,error_message,int(time.time()),message_id))

    @staticmethod
    def _admin_message_from_row(row: sqlite3.Row | None) -> AdminMessage:
        if row is None:
            raise LookupError("message not found")
        return AdminMessage(int(row["id"]),int(row["user_id"]),str(row["sender_type"]),str(row["content"]),int(row["occurred_at"]),str(row["message_type"]),str(row["source"]),str(row["send_status"]),str(row["error_message"]) if row["error_message"] is not None else None,str(row["client_request_id"]) if row["client_request_id"] is not None else None)
