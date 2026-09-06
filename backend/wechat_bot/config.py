"""Application configuration loaded from environment variables or the root .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from wechat_bot.llm import LLMConfig


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


def load_env(path: Path = DEFAULT_ENV_FILE) -> dict[str, str]:
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
    values.update(os.environ)
    return values


@dataclass(frozen=True, slots=True)
class Settings:
    corp_id: str
    app_agent_id: str
    app_agent_secret: str
    callback_token: str
    encoding_aes_key: str
    open_kfid: str
    public_base_url: str
    database_path: Path
    llm: LLMConfig
    admin_username: str = ""
    admin_password: str = ""

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> Settings:
        def required(key: str) -> str:
            value = values.get(key, "").strip()
            if not value:
                raise ValueError(f"missing required configuration: {key}")
            return value

        public_base_url = required("PUBLIC_BASE_URL").rstrip("/")
        if not public_base_url.startswith("https://"):
            raise ValueError("PUBLIC_BASE_URL must use HTTPS")

        return cls(
            corp_id=required("CorpID"),
            app_agent_id=required("APP_AGENT_ID"),
            app_agent_secret=required("APP_AGENT_SECRET"),
            callback_token=values.get("WECHAT_KF_CALLBACK_TOKEN", "").strip(),
            encoding_aes_key=values.get("WECHAT_KF_ENCODING_AES_KEY", "").strip(),
            open_kfid=values.get("WECHAT_KF_OPEN_KFID", "").strip(),
            public_base_url=public_base_url,
            database_path=Path(
                values.get("DATABASE_PATH", "data/wechat_bot.db").strip()
                or "data/wechat_bot.db"
            ),
            llm=LLMConfig.from_mapping(values),
            admin_username=values.get("ADMIN_USERNAME", "").strip(),
            admin_password=values.get("ADMIN_PASSWORD", ""),
        )

    @classmethod
    def load(cls) -> Settings:
        return cls.from_mapping(load_env())

    def callback_config_errors(self) -> list[str]:
        missing: list[str] = []
        if not self.callback_token:
            missing.append("WECHAT_KF_CALLBACK_TOKEN")
        if not self.encoding_aes_key:
            missing.append("WECHAT_KF_ENCODING_AES_KEY")
        elif len(self.encoding_aes_key) != 43:
            missing.append("WECHAT_KF_ENCODING_AES_KEY must be 43 characters")
        return missing

    @property
    def admin_configured(self) -> bool:
        return bool(self.admin_username and self.admin_password)
