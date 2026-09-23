FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG MIRROR=official
ARG CHINA_PYPI_URL=""
ARG CUSTOM_PYPI_URL=""

WORKDIR /app

# uv binary from official image; avoids pip mirror availability issues
COPY --from=ghcr.io/astral-sh/uv:0.7.11 /uv /usr/local/bin/uv
COPY --from=ghcr.io/astral-sh/uv:0.7.11 /uvx /usr/local/bin/uvx

COPY pyproject.toml uv.lock ./
RUN case "$MIRROR" in \
      official) pypi_url="https://pypi.org/simple" ;; \
      tencent)  pypi_url="https://mirrors.cloud.tencent.com/pypi/simple" ;; \
      aliyun)   pypi_url="https://mirrors.aliyun.com/pypi/simple" ;; \
      china)    pypi_url="${CHINA_PYPI_URL:?CHINA_PYPI_URL is required}" ;; \
      custom)   pypi_url="${CUSTOM_PYPI_URL:?CUSTOM_PYPI_URL is required}" ;; \
      *) echo "Unsupported MIRROR: $MIRROR" >&2; exit 64 ;; \
    esac \
    && uv sync --frozen --no-dev --index-url "$pypi_url"

COPY main.py ./
COPY wechat_bot ./wechat_bot
COPY garden ./garden
COPY prompts ./prompts
COPY skills ./skills

RUN mkdir -p /app/data && chown -R nobody:nogroup /app

USER nobody

CMD ["/app/.venv/bin/uvicorn", "wechat_bot.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
