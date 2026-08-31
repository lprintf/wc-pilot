"""Smoke-test the configured OpenAI-compatible LLM without using WeCom."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow `python scripts/test_llm_api.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wechat_bot.config import load_env
from wechat_bot.llm import LLMConfig, LLMError, OpenAICompatibleLLM


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send one question to the configured OpenAI-compatible LLM."
    )
    parser.add_argument(
        "question",
        nargs="?",
        default="请仅回复：LLM API 连接成功",
        help="question sent to the model",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"dotenv file (default: {DEFAULT_ENV_FILE})",
    )
    return parser.parse_args()


async def run() -> int:
    args = parse_args()
    try:
        config = LLMConfig.from_mapping(load_env(args.env_file.resolve()))
        print(f"config: ready (model={config.model})", flush=True)
        async with OpenAICompatibleLLM(config) as llm:
            answer = await llm.answer(args.question)
        print("chat/completions: ok")
        print(answer)
        return 0
    except (LLMError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
