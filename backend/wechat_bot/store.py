"""Small SQLite store for cursors, idempotency, and recent conversation history."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from wechat_bot.llm import ChatMessage


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
                CREATE TABLE IF NOT EXISTS kf_cursor (
                    open_kfid TEXT PRIMARY KEY,
                    cursor TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processed_message (
                    msgid TEXT PRIMARY KEY,
                    open_kfid TEXT NOT NULL,
                    external_userid TEXT NOT NULL,
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

    def mark_sent(
        self,
        *,
        msgid: str,
        open_kfid: str,
        external_userid: str,
        send_time: int,
        customer_content: str,
        reply_content: str,
    ) -> None:
        self._record(
            msgid,
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
        open_kfid: str,
        external_userid: str,
        send_time: int,
    ) -> None:
        self._record(
            msgid, open_kfid, external_userid, send_time, "", "", "ignored"
        )

    def history(self, external_userid: str, *, turns: int = 6) -> list[ChatMessage]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT customer_content, reply_content
                FROM processed_message
                WHERE external_userid = ? AND status = 'sent'
                ORDER BY send_time DESC
                LIMIT ?
                """,
                (external_userid, turns),
            ).fetchall()
        history: list[ChatMessage] = []
        for row in reversed(rows):
            history.append(ChatMessage("user", str(row["customer_content"])))
            history.append(ChatMessage("assistant", str(row["reply_content"])))
        return history

    def _record(
        self,
        msgid: str,
        open_kfid: str,
        external_userid: str,
        send_time: int,
        customer_content: str,
        reply_content: str,
        status: str,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO processed_message(
                    msgid, open_kfid, external_userid, send_time,
                    customer_content, reply_content, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msgid,
                    open_kfid,
                    external_userid,
                    send_time,
                    customer_content,
                    reply_content,
                    status,
                    int(time.time()),
                ),
            )
