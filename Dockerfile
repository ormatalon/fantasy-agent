FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHONPATH=/app/src \
    PATH="/app/.venv/bin:$PATH" \
    FANTASY_DATA_DIR=/data FANTASY_UI_HOST=0.0.0.0 FANTASY_UI_PORT=8080 NICEGUI_STORAGE_PATH=/data/.nicegui

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src

RUN useradd --create-home app && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health')"
ENTRYPOINT []
CMD ["python", "-m", "ui"]
