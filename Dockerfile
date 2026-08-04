FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
RUN useradd --system --uid 1000 --create-home \
      --home-dir /var/lib/minecraft-admin-mcp minecraft-mcp \
    && mkdir -p /app /backups /var/lib/minecraft-admin-mcp \
    && chown -R minecraft-mcp:minecraft-mcp /backups /var/lib/minecraft-admin-mcp
WORKDIR /app
COPY --from=builder --chown=minecraft-mcp:minecraft-mcp /app/.venv /app/.venv
USER 1000:1000
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8101
CMD ["python", "-m", "minecraft_admin_mcp"]
