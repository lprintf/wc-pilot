"""One-time login tickets and server-side web sessions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import secrets
import time

from wechat_bot.store import MessageStore


SESSION_COOKIE_NAME = "wechat_bot_session"
DEFAULT_TICKET_TTL_SECONDS = 10 * 60
DEFAULT_TICKET_MIN_INTERVAL_SECONDS = 60
DEFAULT_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
SECRET_BYTES = 32


class LoginTicketError(RuntimeError):
    """Base error for one-time login tickets."""


class LoginTicketRateLimited(LoginTicketError):
    """Raised when a user requests login links too frequently."""


class InvalidLoginTicket(LoginTicketError):
    """Raised for missing, expired, or already-used login tickets."""


@dataclass(frozen=True, slots=True)
class IssuedLoginTicket:
    token: str
    url: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class IssuedWebSession:
    token: str
    user_id: int
    expires_at: int


def _hash_secret(value: str) -> str:
    return hashlib.sha256(f"wechat-bot-auth-v1\0{value}".encode()).hexdigest()


class AuthManager:
    def __init__(
        self,
        store: MessageStore,
        public_base_url: str,
        *,
        ticket_ttl_seconds: int = DEFAULT_TICKET_TTL_SECONDS,
        ticket_min_interval_seconds: int = DEFAULT_TICKET_MIN_INTERVAL_SECONDS,
        session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    ) -> None:
        base_url = public_base_url.rstrip("/")
        if not base_url.startswith("https://"):
            raise ValueError("PUBLIC_BASE_URL must use HTTPS")
        if not 300 <= ticket_ttl_seconds <= 600:
            raise ValueError("login ticket TTL must be between 5 and 10 minutes")
        if ticket_min_interval_seconds < 1:
            raise ValueError("login ticket minimum interval must be positive")
        if session_ttl_seconds < 1:
            raise ValueError("web session TTL must be positive")
        self._store = store
        self._public_base_url = base_url
        self._ticket_ttl_seconds = ticket_ttl_seconds
        self._ticket_min_interval_seconds = ticket_min_interval_seconds
        self._session_ttl_seconds = session_ttl_seconds

    @property
    def session_ttl_seconds(self) -> int:
        return self._session_ttl_seconds

    def issue_login_ticket(
        self, user_id: int, *, now: int | None = None
    ) -> IssuedLoginTicket:
        current_time = int(time.time()) if now is None else now
        token = secrets.token_urlsafe(SECRET_BYTES)
        expires_at = current_time + self._ticket_ttl_seconds
        created = self._store.create_login_ticket(
            user_id=user_id,
            token_hash=_hash_secret(token),
            created_at=current_time,
            expires_at=expires_at,
            min_interval_seconds=self._ticket_min_interval_seconds,
        )
        if not created:
            raise LoginTicketRateLimited("login ticket requested too frequently")
        return IssuedLoginTicket(
            token=token,
            url=f"{self._public_base_url}/auth/t/{token}",
            expires_at=expires_at,
        )

    def cancel_login_ticket(self, token: str) -> None:
        self._store.delete_unused_login_ticket(_hash_secret(token))

    def consume_login_ticket(
        self,
        token: str,
        *,
        previous_session_token: str = "",
        now: int | None = None,
    ) -> IssuedWebSession:
        if not token or len(token) > 128:
            raise InvalidLoginTicket("login ticket is invalid or expired")
        current_time = int(time.time()) if now is None else now
        session_token = secrets.token_urlsafe(SECRET_BYTES)
        expires_at = current_time + self._session_ttl_seconds
        user_id = self._store.consume_login_ticket(
            token_hash=_hash_secret(token),
            used_at=current_time,
            session_hash=_hash_secret(session_token),
            session_expires_at=expires_at,
            previous_session_hash=(
                _hash_secret(previous_session_token)
                if previous_session_token
                else None
            ),
        )
        if user_id is None:
            raise InvalidLoginTicket("login ticket is invalid or expired")
        return IssuedWebSession(session_token, user_id, expires_at)

    def authenticate_session(self, token: str, *, now: int | None = None) -> int | None:
        if not token:
            return None
        current_time = int(time.time()) if now is None else now
        return self._store.get_session_user(
            _hash_secret(token), accessed_at=current_time
        )

    def logout_session(self, token: str) -> None:
        if token:
            self._store.revoke_session(_hash_secret(token))
