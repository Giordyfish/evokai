"""The FastAPI application."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Route

from evokai import policies
from evokai.agents.bootstrap import bootstrap, bootstrap_apps
from evokai.api import (
    a2a_routes,
    admin_routes,
    agui_routes,
    ask_routes,
    assistant_routes,
    capability_routes,
    channel_routes,
    compliance_routes,
    connection_routes,
    console_routes,
    cost_routes,
    eval_routes,
    grants_routes,
    knowledge_routes,
    openai_routes,
    policy_routes,
    preferences_routes,
    privacy_routes,
    provider_routes,
    redteam_routes,
    run_routes,
    security_routes,
    server_routes,
    session_routes,
    sso_routes,
    trace_routes,
    worker_routes,
    workflow_routes,
)
from evokai.api.console_static import mount_console
from evokai.api.hardening import Hardening, origin_cache
from evokai.assistant.bootstrap import ensure_assistant
from evokai.core.config import Settings, get_settings
from evokai.core.errors import AppError
from evokai.core.util import utcnow
from evokai.guard import identifiers
from evokai.mcp_admin.server import AdminMcp
from evokai.observability.logging import logger
from evokai.observability.middleware import CorrelationMiddleware
from evokai.observability.telemetry import setup_telemetry
from evokai.privacy import purge_revocations
from evokai.services import Services, build_services
from evokai.version import __version__

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "too_large",
    422: "bad_request",
    429: "rate_limited",
}


PURGE_EVERY = 3600


async def _housekeeping(svc: Services) -> None:
    """Purge expired credentials and actions every few minutes, expired conversations hourly."""
    last_purge = 0.0
    while True:
        with contextlib.suppress(Exception):
            await svc.store.credentials.purge()
            await svc.store.pending.purge()
            # A revocation matters only while a token minted before it could still be alive.
            horizon = svc.settings.session_token_max_ttl + 300
            await purge_revocations(svc.store, utcnow() - timedelta(seconds=horizon))
        # Episodes of finished conversations, for agents that keep them (evokai.memory).
        with contextlib.suppress(Exception):
            if n := await svc.memory.summarize_finished():
                logger.info("memory_episodes_written", extra={"count": n})
        if time.monotonic() - last_purge >= PURGE_EVERY:
            last_purge = time.monotonic()
            state: dict[str, object] = {"lastRunAt": time.time(), "deleted": 0, "error": None}
            try:
                if n := await svc.store.sessions.purge_expired():
                    state["deleted"] = n
                    logger.info("conversations_purged", extra={"count": n})
                if n := await svc.store.traces.purge():
                    state["tracesDeleted"] = n
                    logger.info("traces_purged", extra={"count": n})
                ttl = svc.settings.security_event_ttl_days
                if n := await svc.store.security_events.purge(ttl):
                    state["securityEventsDeleted"] = n
                    logger.info("security_events_purged", extra={"count": n})
                if n := await svc.store.audit.purge(svc.settings.audit_ttl_days):
                    state["auditDeleted"] = n
                    logger.info("audit_purged", extra={"count": n})
                if n := await svc.store.knowledge.purge():
                    state["knowledgeDeleted"] = n
                    logger.info("knowledge_purged", extra={"count": n})
                if n := await svc.store.memories.purge():
                    state["memoriesDeleted"] = n
                    logger.info("memories_purged", extra={"count": n})
                if n := await svc.store.capabilities.purge():
                    state["capabilityRecordsDeleted"] = n
                    logger.info("capability_records_purged", extra={"count": n})
                # Month-end forecasts against the caps: alerts fire once per threshold.
                if svc.costs is not None and (n := await svc.costs.check_alerts()):
                    state["costAlerts"] = n
                    logger.info("cost_alerts_fired", extra={"count": n})
                if n := await svc.store.workflows.purge():
                    state["workflowRunsDeleted"] = n
                    logger.info("workflow_runs_purged", extra={"count": n})
            except Exception as e:
                state["error"] = type(e).__name__
                logger.warning("purge_failed", extra={"error_type": type(e).__name__})
            state["nextRunAt"] = time.time() + PURGE_EVERY
            svc.housekeeping = state
        await asyncio.sleep(300)


async def _refresh_system_policy(svc: Services) -> None:
    """Reload the system policy now and then (a save on another instance)."""
    while True:
        await asyncio.sleep(policies.REFRESH_SECONDS)
        try:
            await policies.load_system(svc.store)
        except Exception as e:
            logger.warning("system_policy_not_loaded", extra={"error_type": type(e).__name__})


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    """Build the application.

    Args:
        settings: Deployment settings (from the environment by default).
        services: Pre-built services (tests).

    Returns:
        The FastAPI app.
    """
    settings = settings or get_settings()
    svc = services or build_services(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.services = svc
        if settings.bootstrap_file:
            app_id = await bootstrap(
                svc.store,
                svc.keyring,
                settings.bootstrap_file,
                {"srv": settings.bootstrap_server_key, "adm": settings.bootstrap_admin_key},
            )
            logger.info("bootstrapped", extra={"application_id": app_id})
        if settings.bootstrap_apps:
            ids = await bootstrap_apps(svc.store, settings.bootstrap_apps)
            logger.info("bootstrapped_from_env", extra={"applications": len(ids)})
        # The built-in console assistant: a locked system application, inactive until activated.
        try:
            await ensure_assistant(svc.store, svc.keyring, settings)
        except Exception as e:
            logger.warning("assistant_not_ensured", extra={"error_type": type(e).__name__})
        # Every application's own identifiers are masked in logs and traces from the start.
        with contextlib.suppress(Exception):
            for application in await svc.store.applications.all():
                identifiers.of_app(application)
        # Runs left going by a process that stopped will never end: they are marked interrupted.
        with contextlib.suppress(Exception):
            await svc.store.evals.interrupt_stale(utcnow())
        # Documents a stopped process left queued or half-ingested are picked up again.
        with contextlib.suppress(Exception):
            await svc.knowledge.start()
        # Workflow steps a stopped process left running are resumed once their lease expires.
        with contextlib.suppress(Exception):
            await svc.workflows.start()
        # Durable agent steps (pgtask) are taken by this instance's worker too.
        with contextlib.suppress(Exception):
            await svc.durable.start()
        # Mailboxes read over IMAP are polled from now on.
        with contextlib.suppress(Exception):
            await svc.channels.start()
        # The system policy applies from the first turn, and another instance's save reaches
        # this one within policies.REFRESH_SECONDS.
        with contextlib.suppress(Exception):
            await policies.load_system(svc.store)
        task = asyncio.create_task(_housekeeping(svc))
        refresh = asyncio.create_task(_refresh_system_policy(svc))
        # A local classifier model loads now, not on the first turn that needs it.
        warm = asyncio.create_task(svc.guard.classifiers.warm_up())
        try:
            async with app.state.admin_mcp.lifespan():
                yield
        finally:
            task.cancel()
            refresh.cancel()
            warm.cancel()
            await svc.close()

    base = settings.base_path.rstrip("/")
    app = FastAPI(
        title="Evokai",
        version=__version__,
        summary="Agents runtime: any model provider, MCP tools, skills and voice sessions.",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=f"{base}/openapi.json",
    )
    app.state.services = svc
    app.add_middleware(
        Hardening,
        base_path=base,
        origins=origin_cache(svc.store),
        attachments_mb=settings.attachments_request_mb,
        takes_files=lambda authorization: session_routes.takes_attachments(svc, authorization),
        transcribe_bytes=settings.transcribe_max_bytes,
    )
    app.add_middleware(CorrelationMiddleware)

    openai_prefix = f"{settings.base_path.rstrip('/')}/v1/openai/"

    def openai_error(request: Request, status: int, code: str, msg: str) -> JSONResponse | None:
        """OpenAI's error shape on the OpenAI-compatible routes, so OpenAI clients read it.

        Returns:
            The response, or None on any other route.
        """
        if not request.url.path.startswith(openai_prefix):
            return None
        return JSONResponse(openai_routes.error_body(status, code, msg), status_code=status)

    @app.exception_handler(AppError)
    def app_error(request: Request, e: AppError) -> JSONResponse:
        if (r := openai_error(request, e.status, e.code, e.message)) is not None:
            return r
        extra = getattr(e, "extra", None) or {}
        return JSONResponse({**extra, "error": e.code, "message": e.message}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    def validation_error(request: Request, e: RequestValidationError) -> JSONResponse:
        fields = [".".join(str(p) for p in err.get("loc", [])) for err in e.errors()][:10]
        if any("ask_rules" in f for f in fields):
            # Ask rules say why they are refused (an effect other than ask or default, say).
            from evokai.guard.ask import explain_errors

            return JSONResponse(
                {
                    "error": "ask_rules",
                    "message": explain_errors([
                        x for x in e.errors() if "ask_rules" in [str(p) for p in x.get("loc", [])]
                    ]),
                    "fields": fields,
                },
                status_code=400,
            )
        if (
            r := openai_error(request, 400, "bad_request", f"Invalid request: {fields}")
        ) is not None:
            return r
        return JSONResponse(
            {"error": "bad_request", "message": "Invalid request.", "fields": fields},
            status_code=400,
        )

    @app.exception_handler(StarletteHTTPException)
    def http_error(request: Request, e: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(e.status_code, "error")
        if (r := openai_error(request, e.status_code, code, code.replace("_", " "))) is not None:
            return r
        return JSONResponse(
            {"error": code, "message": code.replace("_", " ")}, status_code=e.status_code
        )

    @app.exception_handler(Exception)
    def unexpected(_: Request, e: Exception) -> JSONResponse:
        logger.exception("unhandled", extra={"error_type": type(e).__name__})
        return JSONResponse(
            {"error": "internal", "message": "Something went wrong."}, status_code=500
        )

    root = APIRouter(prefix=settings.base_path.rstrip("/"))

    @root.get("/health")
    async def health() -> dict[str, bool]:
        """Liveness.

        Returns:
            ok.
        """
        return {"ok": True}

    root.include_router(session_routes.router)
    root.include_router(agui_routes.router)
    root.include_router(openai_routes.router)
    root.include_router(a2a_routes.router)
    root.include_router(server_routes.router)
    root.include_router(admin_routes.router)
    root.include_router(assistant_routes.router)
    root.include_router(assistant_routes.console)
    root.include_router(grants_routes.router)
    root.include_router(ask_routes.router)
    root.include_router(capability_routes.router)
    root.include_router(worker_routes.router)
    root.include_router(console_routes.router)
    root.include_router(run_routes.router)
    root.include_router(eval_routes.router)
    root.include_router(redteam_routes.router)
    root.include_router(cost_routes.router)
    root.include_router(provider_routes.router)
    root.include_router(compliance_routes.router)
    root.include_router(security_routes.router)
    root.include_router(policy_routes.router)
    root.include_router(privacy_routes.router)
    root.include_router(sso_routes.router)
    root.include_router(preferences_routes.router)
    root.include_router(trace_routes.router)
    root.include_router(knowledge_routes.router)
    root.include_router(knowledge_routes.files)
    root.include_router(workflow_routes.admin)
    root.include_router(workflow_routes.server)
    root.include_router(workflow_routes.hooks)
    root.include_router(connection_routes.router)
    root.include_router(channel_routes.public)
    root.include_router(channel_routes.admin)
    if settings.voice_enabled:
        from evokai.api import speech_routes, voice_routes

        root.include_router(voice_routes.router)
        root.include_router(speech_routes.router)

    app.include_router(root)
    # The admin API as an MCP server, for AI agents acting with their caller's rights.
    app.state.admin_mcp = AdminMcp(app, settings)
    app.router.routes.append(Route(app.state.admin_mcp.path, app.state.admin_mcp))
    if settings.admin_ui_enabled:
        mount_console(app, settings)
    setup_telemetry(
        app,
        enabled=settings.otel_enabled,
        service_name=settings.service_name,
        exporter=settings.otel_exporter,
    )
    logging.getLogger("evokai").debug("app_created")
    return app
