"""``evokai proxy`` and ``evokai proxy init-ca``.

``evokai proxy`` refuses to start, with the reason, unless ``EVOKAI_PROXY=on``, the gateway
module is on and a constrained CA is configured (``EVOKAI_PROXY_CA_KEY``,
``EVOKAI_PROXY_CA_CERT``). It does not migrate the database: the API does.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("evokai.gateway.proxy")


class Refused(RuntimeError):
    """``evokai proxy`` must not start; the message says why."""


async def gateway_module_on(svc: Any) -> bool:
    """Whether the gateway module is on (``evokai.core.modules``).

    Returns:
        True when it is on.
    """
    from evokai.core import modules

    return await modules.is_enabled(svc, modules.GATEWAY)


def preflight(settings: Any) -> Any:
    """The checks that need no database: the switch and the CA.

    Returns:
        The CA (``ca.Authority``).

    Raises:
        Refused: Why the proxy must not start.
    """
    from evokai.gateway.proxy import ca, catalogue

    if settings.proxy != "on":
        raise Refused("EVOKAI_PROXY is not on: the gateway proxy starts only with EVOKAI_PROXY=on")
    try:
        authority = ca.load(settings.proxy_ca_key, settings.proxy_ca_cert)
    except ca.CaError as e:
        raise Refused(f"no usable proxy CA: {e}. Create one with `evokai proxy init-ca`.") from e
    missing = [
        h.name
        for hosts in catalogue.INTERCEPTED.values()
        for h in hosts
        if not ca.covers(authority.permitted, h.name)
    ]
    if not ca.covers(authority.permitted, catalogue.CHECK_HOST):
        missing.append(catalogue.CHECK_HOST)
    if missing:
        log.warning("proxy_ca_does_not_cover", extra={"hosts": missing})
    return authority


async def serve(settings: Any) -> None:
    """Run the proxy until a signal stops it.

    Raises:
        Refused: Why the proxy must not start.
    """
    from evokai import policies
    from evokai.gateway.proxy.engine import Engine
    from evokai.gateway.proxy.service import ProxyService
    from evokai.gateway.proxy.status import run_heartbeat
    from evokai.services import build_services

    authority = preflight(settings)
    svc = build_services(settings)
    try:
        if not await gateway_module_on(svc):
            raise Refused("the gateway module is off: turn it on before starting the proxy")
        proxy = ProxyService(svc)
        try:
            await proxy.refresh()
            await proxy.book.purge()
        except Exception as e:
            raise Refused(
                f"the database is not ready for the proxy ({type(e).__name__}): "
                "run `evokai migrate`"
            ) from e
        with contextlib.suppress(Exception):
            await policies.load_system(svc.store)
        engine = Engine(proxy, authority, host=settings.proxy_listen_host, port=settings.proxy_port)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, engine.stop)
        refresh = asyncio.create_task(proxy.run_refresh())
        run = asyncio.create_task(engine.run())
        await engine.started.wait()
        heartbeat = asyncio.create_task(run_heartbeat(svc, proxy, engine.port))
        log.info(
            "proxy_listening",
            extra={"port": engine.port, "providers": proxy.providers},
        )
        sys.stdout.write(
            f"evokai proxy listening on {settings.proxy_listen_host}:{engine.port} "
            f"(providers: {', '.join(proxy.providers) or 'none enabled on a gateway'})\n"
        )
        sys.stdout.flush()
        try:
            await run
        finally:
            refresh.cancel()
            heartbeat.cancel()
            await svc.gateway.idle(10)
    finally:
        await svc.close()


def main(args: argparse.Namespace, settings: Any) -> int:
    """``evokai proxy [init-ca]``.

    Returns:
        Exit code.
    """
    if getattr(args, "action", None) == "init-ca":
        return init_ca(Path(args.out), args.org)
    from evokai.observability.logging import setup_logging

    setup_logging()
    logging.getLogger("mitmproxy").setLevel(logging.WARNING)
    try:
        asyncio.run(serve(settings))
    except Refused as e:
        sys.stderr.write(f"evokai proxy: {e}\n")
        return 2
    return 0


def init_ca(out: Path, org: str) -> int:
    """Write a new root and constrained intermediate, and say where each goes.

    Returns:
        Exit code.
    """
    from evokai.gateway.proxy import ca, catalogue

    try:
        files = ca.init(out, org)
    except ca.CaError as e:
        sys.stderr.write(f"evokai proxy init-ca: {e}\n")
        return 2
    sys.stdout.write(
        "Gateway proxy CA created.\n\n"
        f"  {files['root_cert']}\n"
        "      the root: distribute it to the PCs (Intune trusted certificate profile, macOS\n"
        "      configuration profile, NODE_EXTRA_CA_CERTS). The API serves it at /proxy/ca.crt.\n"
        f"  {files['cert']}\n"
        "      the intermediate and the root: EVOKAI_PROXY_CA_CERT (proxy and API).\n"
        f"  {files['key']}\n"
        "      the intermediate's key: EVOKAI_PROXY_CA_KEY, for the proxy process only. Store it\n"
        "      as a secret (Azure Key Vault) and delete this file.\n"
        f"  {files['root_key']}\n"
        "      the root's key: needed only to issue a new intermediate. Keep it offline.\n\n"
        "The intermediate may sign only for: "
        + ", ".join(catalogue.NAME_CONSTRAINTS)
        + " (X.509 Name Constraints).\n"
    )
    return 0


def add_parser(sub: Any) -> None:
    """The ``proxy`` command of the CLI."""
    p = sub.add_parser("proxy", help="run the gateway proxy (EVOKAI_PROXY=on), or create its CA")
    actions = p.add_subparsers(dest="action")
    init = actions.add_parser("init-ca", help="create a root and a constrained intermediate CA")
    init.add_argument("--out", default="evokai-proxy-ca", help="the directory to write to")
    init.add_argument("--org", default="Evokai", help="the organisation name in the certificates")
