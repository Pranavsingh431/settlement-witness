# Backend image for Settlement Witness.
#
# The build installs only the locked runtime dependencies, then runs the
# service as an unprivileged user.

FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/srv/backend/.venv/bin:${PATH}" \
    SW_APP_ENV=production \
    SW_LOG_LEVEL=INFO \
    SW_API_HOST=0.0.0.0 \
    SW_API_PORT=8000 \
    SW_DATABASE_PATH=/srv/data/settlement.sqlite

WORKDIR /srv/backend

RUN groupadd --system app && useradd --system --gid app --home-dir /srv app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# The migrations travel with the image. The entry point brings the database to
# head before the server binds, so an image without them starts and then fails
# on its first request, which is the harder failure to diagnose.
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY app ./app

# Runtime state lives here. Created and owned before the user is dropped,
# because the container runs unprivileged and cannot create it later.
RUN mkdir -p /srv/data

RUN chown -R app:app /srv
USER app

VOLUME ["/srv/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()"]

# The same entry point the developer commands use, so the container and a
# local run read their address from the same settings.
CMD ["python", "-m", "app"]
