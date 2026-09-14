FROM python:3.13-slim AS dependencies
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-editable

FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    TRADER_STATE_DIR=/app/state
WORKDIR /app
RUN groupadd --gid 10001 trader && useradd --uid 10001 --gid trader --no-create-home trader \
    && mkdir /app/state && chown trader:trader /app/state
COPY --from=dependencies /app/.venv /app/.venv
COPY src/ ./src/
USER trader
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "src/main.py", "--healthcheck"]
CMD ["python", "src/main.py"]
