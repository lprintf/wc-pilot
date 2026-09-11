from __future__ import annotations

import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from wechat_bot.llm import ChatMessage
from wechat_bot.store import MessageStore


class MessageStoreTests(unittest.TestCase):
    def test_batch_record_rolls_back_all_messages_on_failure(self) -> None:
        user = self.store.get_or_create_customer("kf", "customer", seen_at=100)
        record = self.store._record_locked
        count = 0
        def fail_on_second(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise sqlite3.OperationalError("simulated write failure")
            return record(*args)
        with patch.object(self.store, "_record_locked", side_effect=fail_on_second):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.mark_batch_sent(messages=[("first", 100, "question 1"), ("last", 101, "question 2")],
                    user_id=user, open_kfid="kf", external_userid="customer", reply_content="answer", reply_source_message_id="out")
        self.assertFalse(self.store.is_processed("first"))
        self.assertFalse(self.store.is_processed("last"))
        self.assertEqual(self.store.history(user), [])

    def test_platform_reply_is_not_duplicated_on_backfill_or_restart(self) -> None:
        user = self.store.get_or_create_customer("kf", "customer", seen_at=100)
        args = dict(msgid="in", user_id=user, open_kfid="kf", external_userid="customer",
                    send_time=100, customer_content="hi", reply_content="reply", reply_source_message_id="out")
        self.store.mark_sent(**args)
        self.store.get_or_create_customer("kf", "customer", seen_at=101)
        self.store.mark_sent(**{**args, "reply_source_message_id": "must-not-insert"})
        self.store.close()
        self.store = MessageStore(self.path)
        self.assertEqual(len(self.store.history(user)), 2)
        self.assertEqual(self.store._connection.execute("SELECT source_message_id FROM conversation_message WHERE sender_type='ai'").fetchone()[0], "out")

    def test_migration_removes_only_confirmed_synthetic_duplicate(self) -> None:
        user = self.store.get_or_create_customer("kf", "customer", seen_at=100)
        self.store.mark_sent(msgid="in", user_id=user, open_kfid="kf", external_userid="customer", send_time=100,
                             customer_content="hi", reply_content="same", reply_source_message_id="out")
        with self.store._connection:
            self.store._connection.execute("UPDATE processed_message SET reply_source_message_id=NULL")
            self.store._connection.execute("""INSERT INTO conversation_message(conversation_id,user_id,sender_type,message_type,content,source,source_message_id,send_status,occurred_at,created_at,updated_at)
                SELECT conversation_id,user_id,sender_type,message_type,content,source,'legacy-reply:in',send_status,occurred_at,created_at,updated_at
                FROM conversation_message WHERE source_message_id='out'""")
        self.store.close()
        self.store = MessageStore(self.path)
        self.assertEqual(len(self.store.history(user)), 2)
        self.assertEqual(self.store._connection.execute("SELECT reply_source_message_id FROM processed_message").fetchone()[0], "out")
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "test.db"
        self.store = MessageStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def test_customer_identity_is_stable_and_scoped_to_account(self) -> None:
        first = self.store.get_or_create_customer(
            "wk-account", "external-user", seen_at=100
        )
        repeated = self.store.get_or_create_customer(
            "wk-account", "external-user", seen_at=200
        )
        self.store.get_or_create_customer(
            "wk-account", "external-user", seen_at=50
        )
        other_account = self.store.get_or_create_customer(
            "wk-other", "external-user", seen_at=200
        )

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, other_account)
        profile = self.store.get_customer_profile(first)
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.first_seen_at, 50)
        self.assertEqual(profile.last_seen_at, 200)

    def test_updates_and_caches_customer_profile(self) -> None:
        user_id = self.store.get_or_create_customer(
            "wk-account", "external-user", seen_at=100
        )
        self.assertTrue(
            self.store.profile_needs_refresh(
                user_id, max_age_seconds=3600, now=200
            )
        )

        self.store.update_customer_profile(
            user_id=user_id,
            external_userid="external-user",
            nickname="昵称",
            avatar_url="https://example.test/avatar.png",
            gender=2,
            unionid="union-id",
            scene="scene",
            scene_param="parameter",
            fetched_at=150,
        )

        profile = self.store.get_customer_profile(user_id)
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.nickname, "昵称")
        self.assertEqual(profile.avatar_url, "https://example.test/avatar.png")
        self.assertEqual(profile.gender, 2)
        self.assertEqual(profile.unionid, "union-id")
        self.assertEqual(profile.scene, "scene")
        self.assertEqual(profile.scene_param, "parameter")
        self.assertFalse(
            self.store.profile_needs_refresh(
                user_id, max_age_seconds=3600, now=200
            )
        )

    def test_conversation_history_is_isolated_by_internal_user(self) -> None:
        first_user = self.store.get_or_create_customer(
            "wk-first", "same-external-id", seen_at=100
        )
        second_user = self.store.get_or_create_customer(
            "wk-second", "same-external-id", seen_at=100
        )
        self.store.mark_sent(
            msgid="first-message",
            user_id=first_user,
            open_kfid="wk-first",
            external_userid="same-external-id",
            send_time=101,
            customer_content="第一个用户的问题",
            reply_content="第一个回答",
        )
        self.store.mark_sent(
            msgid="second-message",
            user_id=second_user,
            open_kfid="wk-second",
            external_userid="same-external-id",
            send_time=102,
            customer_content="第二个用户的问题",
            reply_content="第二个回答",
        )

        self.assertEqual(
            self.store.history(first_user),
            [
                ChatMessage("user", "第一个用户的问题"),
                ChatMessage("assistant", "第一个回答"),
            ],
        )
        summaries = self.store.list_conversations(first_user)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].message_count, 2)
        self.assertEqual(summaries[0].preview, "第一个用户的问题")
        messages = self.store.conversation_messages(
            first_user, summaries[0].id
        )
        self.assertIsNotNone(messages)
        assert messages is not None
        self.assertEqual(
            [(message.sender_type, message.content) for message in messages],
            [
                ("customer", "第一个用户的问题"),
                ("assistant", "第一个回答"),
            ],
        )
        self.assertIsNone(
            self.store.conversation_messages(second_user, summaries[0].id)
        )

    def test_migrates_existing_processed_message_table(self) -> None:
        self.store.close()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("DROP TABLE processed_message")
            connection.execute(
                """
                CREATE TABLE processed_message (
                    msgid TEXT PRIMARY KEY,
                    open_kfid TEXT NOT NULL,
                    external_userid TEXT NOT NULL,
                    send_time INTEGER NOT NULL,
                    customer_content TEXT NOT NULL,
                    reply_content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO processed_message(
                    msgid, open_kfid, external_userid, send_time,
                    customer_content, reply_content, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "old-message",
                    "wk-account",
                    "external-user",
                    100,
                    "旧问题",
                    "旧回答",
                    "sent",
                    100,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        self.store = MessageStore(self.path)

        connection = sqlite3.connect(self.path)
        try:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(processed_message)"
                ).fetchall()
            }
        finally:
            connection.close()
        self.assertIn("user_id", columns)
        self.assertIn("conversation_id", columns)
        user_id = self.store.get_or_create_customer(
            "wk-account", "external-user", seen_at=200
        )
        self.assertEqual(
            self.store.history(user_id),
            [ChatMessage("user", "旧问题"), ChatMessage("assistant", "旧回答")],
        )
        summaries = self.store.list_conversations(user_id)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].message_count, 2)
