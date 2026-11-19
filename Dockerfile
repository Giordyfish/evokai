# syntax=docker/dockerfile:1.7
# Evokai service image. Runs as uid 1001 on a read-only root filesystem: only /tmp (a tmpfs) is
# written. Port 8787. Voice (Pipecat, WebRTC, Silero, Smart Turn) only with --build-arg VOICE=1.
# The local injection classifier (Llama Prompt Guard 2, ONNX) only with --build-arg PROMPT_GUARD=1 and
# --build-context prompt-guard-model=<folder from scripts/export-prompt-guard.py>: the model is baked
# into /opt/prompt-guard from your own folder, never downloaded during the build (docs/security.md).
# Two flavours, --build-arg IMAGE=lite (the default) or IMAGE=full: lite installs the harness core with
# Code Mode, compaction and subagents and every harness extra under 30 MB (the harness-lite extra);
# full installs every harness extra that resolves (the harness extra). Whether a capability is used is
# the deployment's, the grant's and the agent's choice (evokai.capabilities); one the lite image leaves
# out shows as not installed, «not in the lite image».
# The admin console (admin/) is built to static files and served at {EVOKAI_BASE_PATH}/admin/.
# /data (owned by uid 1001) is where EVOKAI_STATE_DIR keeps generated keys when a volume is mounted.
ARG PYTHON_VERSION=3.14

# The console: static files, built on the build machine's own platform (they are the same everywhere).
FROM --platform=$BUILDPLATFORM node:22-slim AS console
WORKDIR /src
COPY admin/package.json admin/package-lock.json admin/
RUN --mount=type=cache,target=/root/.npm cd admin && npm ci --no-audit --no-fund
COPY clients/typescript/src clients/typescript/src
COPY admin admin
RUN cd admin && npm run build

FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
ARG VOICE=0
ARG PROMPT_GUARD=""
ARG IMAGE=lite
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    test "$IMAGE" = lite || test "$IMAGE" = full \
 && uv sync --locked --no-dev --no-install-project --extra otel \
      --extra $([ "$IMAGE" = "full" ] && echo harness || echo harness-lite) \
      $([ "$VOICE" = "1" ] && echo --extra voice) $([ -n "$PROMPT_GUARD" ] && echo --extra prompt-guard)
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra otel \
      --extra $([ "$IMAGE" = "full" ] && echo harness || echo harness-lite) \
      $([ "$VOICE" = "1" ] && echo --extra voice) $([ -n "$PROMPT_GUARD" ] && echo --extra prompt-guard)

# Gates image: the same dependencies plus dev tools (used by scripts/ci.sh --in-docker).
FROM build AS test
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-extras --dev
COPY tests ./tests
COPY config ./config
RUN uv run --no-sync ruff check src tests migrations \
 && uv run --no-sync ruff format --check src tests migrations \
 && uv run --no-sync ty check \
 && uv run --no-sync pytest -q

# The Prompt Guard model: empty unless the build replaces this stage with
# --build-context prompt-guard-model=<folder> (model.onnx, tokenizer.json, config.json, LICENSE, NOTICE).
FROM scratch AS prompt-guard-model

FROM python:${PYTHON_VERSION}-slim AS runtime
ARG VOICE=0
ARG PROMPT_GUARD=""
ARG IMAGE=lite
ARG COMMIT=""
ENV PATH=/app/.venv/bin:$PATH \
    EVOKAI_BUILD_COMMIT=$COMMIT \
    EVOKAI_IMAGE_FLAVOUR=$IMAGE \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HOME=/tmp XDG_CACHE_HOME=/tmp/.cache HF_HOME=/tmp/.cache/huggingface \
    EVOKAI_PORT=8787 EVOKAI_BASE_PATH=/agent EVOKAI_PRODUCTION=true \
    EVOKAI_VOICE_ENABLED=false \
    EVOKAI_PROMPT_GUARD_MODEL=${PROMPT_GUARD:+/opt/prompt-guard}
RUN groupadd --system --gid 1001 evokai \
 && useradd --system --uid 1001 --gid 1001 --home-dir /tmp --no-create-home evokai \
 && mkdir -p /data && chown 1001:1001 /data && chmod 700 /data \
 && if [ "$VOICE" = "1" ] || [ -n "$PROMPT_GUARD" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*; \
    fi
WORKDIR /app
COPY --from=build --chown=root:root /app /app
COPY --chown=root:root config ./config
COPY --from=console --chown=root:root /src/admin/dist ./admin/dist
COPY --from=prompt-guard-model --chown=root:root / /opt/prompt-guard/
LABEL org.opencontainers.image.title="evokai" org.opencontainers.image.source="https://github.com/Giordyfish/evokai"
USER 1001:1001
EXPOSE 8787
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request as u;u.urlopen(f\"http://127.0.0.1:{os.environ.get('EVOKAI_PORT','8787')}{os.environ.get('EVOKAI_BASE_PATH','/agent').rstrip('/')}/health\",timeout=2)"]
ENTRYPOINT ["evokai"]
CMD ["serve"]
