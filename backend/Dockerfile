FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY main.py ./
COPY wechat_bot ./wechat_bot

RUN mkdir -p /app/data && chown -R nobody:nogroup /app

USER nobody

CMD ["/app/.venv/bin/uvicorn", "wechat_bot.app:app", "--host", "0.0.0.0", "--port", "8000"]
