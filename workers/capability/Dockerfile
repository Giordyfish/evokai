# syntax=docker/dockerfile:1.7
# Evokai capability worker: one pydantic-ai-harness capability served over MCP (evokai_worker.server),
# separate from Evokai's lite and full images.
#   --build-arg CAPABILITY=kubernetes  the Kubernetes sandbox with shell and file tools on its pod
#   --build-arg CAPABILITY=browser     browser-use and Playwright with Playwright's Chromium; the process
#                                      serves one of them (EVOKAI_WORKER_CAPABILITY=browser_use|playwright)
# The two images cannot be one: browser-use pins pydantic and protobuf below what the Kubernetes client
# needs. Runs as uid 1001 on a read-only root (/tmp a tmpfs), port 8790; EVOKAI_WORKER_SECRET is required,
# and browser_use calls models only through Evokai (EVOKAI_URL, with the turn's call token Evokai sends):
# no provider key and no Evokai key here.
ARG PYTHON_VERSION=3.14

FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
ARG CAPABILITY=kubernetes
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    test "$CAPABILITY" = kubernetes || test "$CAPABILITY" = browser \
 && uv sync --locked --no-dev --no-install-project --extra "$CAPABILITY"
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra "$CAPABILITY"

FROM python:${PYTHON_VERSION}-slim AS runtime
ARG CAPABILITY=kubernetes
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp XDG_CACHE_HOME=/tmp/.cache \
    PYDANTIC_AI_NO_BANNER=1 EVOKAI_WORKER_PORT=8790 PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
WORKDIR /app
COPY --from=build --chown=root:root /app /app
RUN groupadd --system --gid 1001 evokai \
 && useradd --system --uid 1001 --gid 1001 --home-dir /tmp --no-create-home evokai \
 && if [ "$CAPABILITY" = "browser" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libatomic1 \
      && playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/* \
      && chmod -R a+rX /opt/ms-playwright; \
    fi
# The image's own capability by default (browser: browser_use); the environment may choose playwright.
ENV EVOKAI_WORKER_CAPABILITY=${CAPABILITY}
LABEL org.opencontainers.image.title="evokai-capability-worker"
USER 1001:1001
EXPOSE 8790
ENTRYPOINT ["evokai-worker"]
