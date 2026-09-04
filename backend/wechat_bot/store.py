"""SQLite store for customer identities, profiles, and conversation state."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from wechat_bot.llm import ChatMessage


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


class MessageStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
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
                    """
                    SELECT customer_content, reply_content, send_time, created_at
                    FROM processed_message
                    WHERE conversation_id = ? AND status = 'sent'
                    ORDER BY send_time DESC, created_at DESC
                    LIMIT 1
                    """,
                    (int(conversation["id"]),),
                ).fetchone()
                count_row = self._connection.execute(
                    """
                    SELECT COUNT(1) AS turn_count
                    FROM processed_message
                    WHERE conversation_id = ? AND status = 'sent'
                    """,
                    (int(conversation["id"]),),
                ).fetchone()
                if message_row is None or count_row is None:
                    continue
                preview = str(message_row["customer_content"]).strip()
                if not preview:
                    preview = str(message_row["reply_content"]).strip()
                summaries.append(
                    ConversationSummary(
                        id=int(conversation["id"]),
                        source=str(conversation["source"]),
                        started_at=int(conversation["created_at"]),
                        last_message_at=max(
                            int(message_row["send_time"]),
                            int(message_row["created_at"]),
                        ),
                        message_count=int(count_row["turn_count"]) * 2,
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
                """
                SELECT customer_content, reply_content, send_time, created_at
                FROM processed_message
                WHERE conversation_id = ? AND status = 'sent'
                ORDER BY send_time DESC, created_at DESC
                LIMIT ?
                """,
                (conversation_id, turns),
            ).fetchall()

        messages: list[ConversationMessage] = []
        for row in reversed(rows):
            customer_content = str(row["customer_content"]).strip()
            reply_content = str(row["reply_content"]).strip()
            if customer_content:
                messages.append(
                    ConversationMessage(
                        "customer", customer_content, int(row["send_time"])
                    )
                )
            if reply_content:
                messages.append(
                    ConversationMessage(
                        "assistant", reply_content, int(row["created_at"])
                    )
                )
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

    def history(self, user_id: int, *, turns: int = 6) -> list[ChatMessage]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT customer_content, reply_content
                FROM processed_message
                WHERE user_id = ? AND status = 'sent'
                ORDER BY send_time DESC
                LIMIT ?
                """,
                (user_id, turns),
            ).fetchall()
        history: list[ChatMessage] = []
        for row in reversed(rows):
            history.append(ChatMessage("user", str(row["customer_content"])))
            history.append(ChatMessage("assistant", str(row["reply_content"])))
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
    ) -> None:
        with self._lock, self._connection:
            recorded_at = int(time.time())
            conversation_id = self._get_or_create_conversation_locked(
                user_id=user_id,
                open_kfid=open_kfid,
                occurred_at=send_time if send_time > 0 else recorded_at,
            )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO processed_message(
                    msgid, user_id, conversation_id, open_kfid,
                    external_userid, send_time,
                    customer_content, reply_content, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            self._connection.execute(
                """
                UPDATE conversation
                SET updated_at = MAX(updated_at, ?)
                WHERE id = ?
                """,
                (recorded_at, conversation_id),
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
