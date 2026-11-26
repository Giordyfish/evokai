"""Command line: serve, migrate, bootstrap, generate keys, types of an agent's tools."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import quote

from evokai.core.config import Settings, get_settings


def _migrate(settings: Settings) -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["settings"] = settings
    command.upgrade(cfg, "head")


def _db_setup(settings: Settings) -> None:
    """Create the runtime role and the schema, migrate, grant. Idempotent.

    Run with an owner ``EVOKAI_DATABASE_URL``. On Postgres: role ``EVOKAI_APP_DB_ROLE`` (created
    with ``EVOKAI_APP_DB_PASSWORD``, password reset on every run), schema ``EVOKAI_DATABASE_SCHEMA``
    (default ``evokai``) owned by the owner, migrations into it, then USAGE on the schema and CRUD
    on its tables for the role. Elsewhere it only migrates.
    """
    from evokai.db.engine import async_url
    from evokai.db.setup import grant, prepare

    if not async_url(settings.database_url).startswith("postgresql"):
        _migrate(settings)
        return
    settings = settings.model_copy(update={"database_schema": settings.database_schema or "evokai"})
    asyncio.run(prepare(settings))
    _migrate(settings)
    asyncio.run(grant(settings))


def _prepare(settings: Settings, *, rotate: bool, migrate: bool) -> Settings:
    """Keys from the state directory, then the migrations when asked.

    The keys are generated on first use and the operator key is printed once.

    Returns:
        The settings with their keys.
    """
    from evokai.core.state import apply_state, banner

    state = apply_state(settings, rotate_operator=rotate)
    settings = state.settings
    if migrate:
        if settings.database_url.startswith("sqlite"):
            _migrate(settings)
        else:
            _db_setup_or_migrate(settings)
    if state.created or settings.state_dir:
        sys.stdout.write(banner(settings, state.operator_key) + "\n")
        sys.stdout.flush()
    return settings


def _db_setup_or_migrate(settings: Settings) -> None:
    """Migrate; on Postgres with a schema, create the schema first (the owner runs the service)."""
    from evokai.db.engine import async_url

    if settings.database_schema and async_url(settings.database_url).startswith("postgresql"):
        from evokai.db.setup import prepare

        asyncio.run(prepare(settings, role=False))
    _migrate(settings)


def _connectors(args: argparse.Namespace) -> int:
    """``evokai connectors list|check|serve``.

    Returns:
        Exit code.
    """
    import yaml

    from evokai.agents.bootstrap import expand_env
    from evokai.connectors import build, describe
    from evokai.core.errors import AppError

    if args.action == "list":
        for d in describe():
            keys = ", ".join(
                f"{k}{'*' if k in d['schema'].get('required', []) else ''}"
                for k in d["schema"].get("properties", {})
            )
            sys.stdout.write(f"{d['kind']:<16} {d['title']}\n{'':<16} {keys}\n")
        sys.stdout.write("(* required; keys ending in _env name environment variables)\n")
        return 0
    spec = yaml.safe_load(expand_env(Path(args.file).read_text(encoding="utf-8"))) or {}
    try:
        connector = build(args.name, str(spec.get("connector")), spec.get("config"))
    except AppError as e:
        sys.stderr.write(e.message + "\n")
        return 2
    if args.action == "check":
        tools = asyncio.run(connector.list_tools())
        for t in tools:
            sys.stdout.write(
                f"{t.name:<28} {'writes' if t.mutating else 'reads'}  {t.description}\n"
            )
        asyncio.run(connector.close())
        return 0
    import uvicorn

    from evokai.connectors.server import mcp_app

    token = os.environ.get(args.token_env, "") if args.token_env else None
    if args.token_env and not token:
        sys.stderr.write(f"{args.token_env} is empty\n")
        return 2
    uvicorn.run(mcp_app(connector, token), host=args.host, port=args.port, log_config=None)
    return 0


def _types(args: argparse.Namespace) -> int:
    """``evokai types``: the ``.d.ts`` of an agent's tools (``GET /v1/admin/agents/{name}/types``).

    Returns:
        Exit code.
    """
    import httpx

    key = os.environ.get(args.key_env, "")
    if not key:
        sys.stderr.write(f"{args.key_env} is empty: set it to the application's admin key\n")
        return 2
    url = f"{args.url.rstrip('/')}/v1/admin/agents/{quote(args.agent, safe='')}/types"
    headers = {"Authorization": f"Bearer {key}"}
    if args.credential_env:
        headers["X-Evokai-Credential"] = os.environ.get(args.credential_env, "")
    try:
        r = httpx.get(url, headers=headers, timeout=60)
    except httpx.HTTPError as e:
        sys.stderr.write(f"{url}: {type(e).__name__}\n")
        return 1
    if r.status_code != 200:
        sys.stderr.write(f"{url}: {r.status_code} {r.text[:500]}\n")
        return 1
    if args.out:
        Path(args.out).write_text(r.text, encoding="utf-8")
    else:
        sys.stdout.write(r.text)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Arguments (defaults to sys.argv).

    Returns:
        Exit code.
    """
    parser = argparse.ArgumentParser(prog="evokai")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the HTTP server")
    init = sub.add_parser(
        "init", help="generate the keys in EVOKAI_STATE_DIR (first start) and migrate the database"
    )
    init.add_argument(
        "--rotate-operator-key", action="store_true", help="replace the operator key (shown once)"
    )
    sub.add_parser("migrate", help="apply database migrations")
    sub.add_parser(
        "db-setup", help="owner URL: create the runtime role and schema, migrate, grant (Postgres)"
    )
    boot = sub.add_parser("bootstrap", help="create or update an application from a YAML file")
    boot.add_argument("file")
    keys = sub.add_parser("keygen", help="print a new key")
    keys.add_argument("kind", choices=["srv", "adm", "signing", "encryption"])
    sub.add_parser("selftest", help="import everything and exit")
    sub.add_parser("purge", help="delete conversations past their time to live (for cron)")
    conn = sub.add_parser("connectors", help="ready connectors: list, check a configuration, serve")
    conn_sub = conn.add_subparsers(dest="action", required=True)
    conn_sub.add_parser("list", help="the connector kinds and their configuration keys")
    for action in ("check", "serve"):
        c = conn_sub.add_parser(
            action,
            help="check a configuration and list its tools"
            if action == "check"
            else "run one connector as a standalone MCP server",
        )
        c.add_argument("file", help="a YAML file: {connector: <kind>, config: {...}}")
        c.add_argument("--name", default="connector")
        if action == "serve":
            c.add_argument("--port", type=int, default=8790)
            c.add_argument("--host", default="127.0.0.1")
            c.add_argument("--token-env", help="require this variable's value as a bearer token")
    types = sub.add_parser(
        "types", help="TypeScript types of an agent's tools, from a running Evokai (admin key)"
    )
    types.add_argument("--agent", required=True, help="the agent's name")
    types.add_argument("--out", help="write to this file instead of stdout")
    types.add_argument(
        "--url",
        default=os.environ.get("EVOKAI_URL", "http://localhost:8787/agent"),
        help="Evokai's base URL, with its path prefix (default $EVOKAI_URL or the local one)",
    )
    types.add_argument(
        "--credential-env",
        help="a variable holding a person's credential, for MCP servers that forward it",
    )
    types.add_argument(
        "--key-env",
        default="EVOKAI_ADMIN_KEY",
        help="the variable that holds the application's admin key (default EVOKAI_ADMIN_KEY)",
    )
    spec = sub.add_parser("openapi", help="print the OpenAPI schema (voice routes included)")
    spec.add_argument("--out", help="write to this file instead of stdout")
    args = parser.parse_args(argv)
    settings = get_settings()

    if args.cmd == "init":
        settings = _prepare(settings, rotate=args.rotate_operator_key, migrate=True)
        return 0
    if args.cmd == "serve":
        import uvicorn

        from evokai.main import create_app
        from evokai.observability.logging import setup_logging

        settings = _prepare(settings, rotate=False, migrate=settings.auto_migrate)
        setup_logging()
        uvicorn.run(
            create_app(settings),
            host="0.0.0.0",  # ruff: ignore[hardcoded-bind-all-interfaces] (inside the container network)
            port=settings.port,
            log_config=None,
            proxy_headers=True,
        )
        return 0
    if args.cmd == "migrate":
        _migrate(settings)
        return 0
    if args.cmd == "db-setup":
        _db_setup(settings)
        return 0
    if args.cmd == "bootstrap":
        from evokai.agents.bootstrap import bootstrap, bootstrap_apps
        from evokai.services import build_services

        async def run() -> str:
            svc = build_services(settings)
            try:
                keys = {"srv": settings.bootstrap_server_key, "adm": settings.bootstrap_admin_key}
                app_id = await bootstrap(svc.store, svc.keyring, args.file, keys)
                await bootstrap_apps(svc.store, settings.bootstrap_apps)
                return app_id
            finally:
                await svc.close()

        sys.stdout.write(asyncio.run(run()) + "\n")
        return 0
    if args.cmd == "keygen":
        if args.kind in {"srv", "adm"}:
            from evokai.core.security import generate_key

            sys.stdout.write(generate_key(args.kind).value + "\n")
        elif args.kind == "encryption":
            from cryptography.fernet import Fernet

            sys.stdout.write(Fernet.generate_key().decode() + "\n")
        else:
            import secrets

            sys.stdout.write(secrets.token_urlsafe(48) + "\n")
        return 0
    if args.cmd == "purge":
        from evokai.services import build_services

        async def purge() -> tuple[int, int, int, int, int, int]:
            svc = build_services(settings)
            try:
                conversations = await svc.store.sessions.purge_expired()
                traces = await svc.store.traces.purge()
                events = await svc.store.security_events.purge(settings.security_event_ttl_days)
                entries = await svc.store.audit.purge(settings.audit_ttl_days)
                documents = await svc.store.knowledge.purge()
                records = await svc.store.capabilities.purge()
                return conversations, traces, events, entries, documents, records
            finally:
                await svc.close()

        conversations, traces, events, entries, documents, records = asyncio.run(purge())
        sys.stdout.write(
            f"purged {conversations} conversations, {traces} traces, {events} security events, "
            f"{entries} audit entries, {documents} knowledge documents, "
            f"{records} capability records\n"
        )
        return 0
    if args.cmd == "connectors":
        return _connectors(args)
    if args.cmd == "types":
        return _types(args)
    if args.cmd == "openapi":
        import json

        from evokai.main import create_app

        app = create_app(settings.model_copy(update={"voice_enabled": True}))
        text = json.dumps(app.openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0
    if args.cmd == "selftest":
        import importlib.util

        from evokai.main import create_app

        probe = settings.model_copy(
            update={"production": False, "database_url": "sqlite+aiosqlite:///:memory:"}
        )
        create_app(probe)
        voice = importlib.util.find_spec("pipecat") is not None
        if voice:
            from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
            from pipecat.audio.vad.silero import SileroVADAnalyzer

            from evokai.audio.voice.pipeline import VoiceServer  # ruff: ignore[unused-import]

            SileroVADAnalyzer()  # the bundled models load offline, on a read-only root
            LocalSmartTurnAnalyzerV3()

        guard = "no"
        if settings.prompt_guard_model:
            # An image built with Prompt Guard must load and run it offline, on a read-only root.
            from evokai.guard.classifiers.prompt_guard import PromptGuard

            pg = PromptGuard.from_settings(settings)
            try:
                asyncio.run(pg.warm_up())
            except Exception as e:
                sys.stderr.write(f"prompt_guard: {pg.status().reason or e}\n")
                return 1
            finally:
                pg.close()
            guard = "yes"
        flavour = settings.image_flavour or "dev"
        sys.stdout.write(
            f"ok flavour={flavour} voice={'yes' if voice else 'no'} prompt_guard={guard}\n"
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
