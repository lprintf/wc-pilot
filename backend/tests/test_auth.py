from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import tempfile
import unittest
from pathlib import Path

from wechat_bot.auth import (
    AuthManager,
    InvalidLoginTicket,
    LoginTicketRateLimited,
)
from wechat_bot.store import MessageStore


class AuthManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "test.db"
        self.store = MessageStore(self.path)
        self.user_id = self.store.get_or_create_customer(
            "wk-account", "external-user", seen_at=100
        )
        self.auth = AuthManager(self.store, "https://customer.example.test")

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def test_ticket_and_session_secrets_are_only_stored_as_hashes(self) -> None:
        ticket = self.auth.issue_login_ticket(self.user_id, now=100)

        self.assertGreaterEqual(len(ticket.token), 43)
        self.assertEqual(ticket.expires_at, 700)
        self.assertNotIn("external-user", ticket.url)
        session = self.auth.consume_login_ticket(ticket.token, now=200)
        self.assertEqual(session.user_id, self.user_id)
        self.assertEqual(
            self.auth.authenticate_session(session.token, now=201), self.user_id
        )

        connection = sqlite3.connect(self.path)
        try:
            ticket_hash = connection.execute(
                "SELECT token_hash FROM login_ticket"
            ).fetchone()[0]
            session_hash = connection.execute(
                "SELECT session_hash FROM web_session"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertNotEqual(ticket_hash, ticket.token)
        self.assertNotEqual(session_hash, session.token)
        self.assertEqual(len(ticket_hash), 64)
        self.assertEqual(len(session_hash), 64)

    def test_ticket_is_single_use_even_with_concurrent_consumers(self) -> None:
        ticket = self.auth.issue_login_ticket(self.user_id, now=100)

        def consume() -> bool:
            try:
                self.auth.consume_login_ticket(ticket.token, now=101)
            except InvalidLoginTicket:
                return False
            return True

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: consume(), range(2)))

        self.assertEqual(sorted(results), [False, True])

    def test_expired_ticket_is_rejected(self) -> None:
        ticket = self.auth.issue_login_ticket(self.user_id, now=100)
        with self.assertRaises(InvalidLoginTicket):
            self.auth.consume_login_ticket(ticket.token, now=700)

    def test_ticket_requests_are_rate_limited_and_new_ticket_invalidates_old(self) -> None:
        first = self.auth.issue_login_ticket(self.user_id, now=100)
        with self.assertRaises(LoginTicketRateLimited):
            self.auth.issue_login_ticket(self.user_id, now=159)

        second = self.auth.issue_login_ticket(self.user_id, now=160)
        with self.assertRaises(InvalidLoginTicket):
            self.auth.consume_login_ticket(first.token, now=161)
        self.assertEqual(
            self.auth.consume_login_ticket(second.token, now=161).user_id,
            self.user_id,
        )

    def test_logout_revokes_session(self) -> None:
        ticket = self.auth.issue_login_ticket(self.user_id, now=100)
        session = self.auth.consume_login_ticket(ticket.token, now=101)

        self.auth.logout_session(session.token)

        self.assertIsNone(self.auth.authenticate_session(session.token, now=102))

    def test_expired_session_is_rejected(self) -> None:
        ticket = self.auth.issue_login_ticket(self.user_id, now=100)
        session = self.auth.consume_login_ticket(ticket.token, now=101)

        self.assertIsNone(
            self.auth.authenticate_session(session.token, now=session.expires_at)
        )

    def test_public_base_url_must_use_https(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            AuthManager(self.store, "http://customer.example.test")
