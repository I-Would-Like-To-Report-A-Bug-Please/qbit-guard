FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /bin/


FROM base AS test

ARG BUILD_VERSION
ENV APP_VERSION=${BUILD_VERSION:-0.0.0-dev}

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY tests /app/tests

RUN uv sync --extra dev
RUN uv run python -m unittest discover -s tests -v


FROM base AS build

ARG BUILD_VERSION
ENV APP_VERSION=${BUILD_VERSION:-0.0.0-dev}

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN uv sync --no-dev --no-editable


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=build /app/.venv /app/.venv

ENTRYPOINT ["qbit-guard-watcher"]
