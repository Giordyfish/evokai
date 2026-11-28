"""The background jobs of each module follow its switch (``evokai.core.modules``).

The applications module owns the knowledge ingestion, the workflow workers and scheduler, the
durable agent steps, the mailbox polling, the eval runs and the console assistant. The gateway
module has no job of its own yet. Retention and erasure are core jobs and run whatever is on.

``apply`` runs at startup, after a switch in the console on this instance, and on every refresh
of the system policy (a switch on another instance), so a module turned off stops its jobs and a
module turned on starts them without a restart.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from evokai.core import modules
from evokai.observability.logging import logger

if TYPE_CHECKING:
    from evokai.services import Services


async def _start_applications(svc: Services) -> None:
    from evokai.assistant.bootstrap import ensure_assistant
    from evokai.core.util import utcnow

    # The built-in console assistant: a locked system application, inactive until activated.
    try:
        await ensure_assistant(svc.store, svc.keyring, svc.settings)
    except Exception as e:
        logger.warning("assistant_not_ensured", extra={"error_type": type(e).__name__})
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


async def _stop_applications(svc: Services) -> None:
    # Work in progress is taken again by any instance (or this one) once the module is back on.
    for part in (svc.channels, svc.workflows, svc.durable):
        if part is not None:
            with contextlib.suppress(Exception):
                await part.close()


async def apply(svc: Services, on: frozenset[str]) -> None:
    """Start the jobs of the modules turned on and stop those of the modules turned off.

    Args:
        svc: The services.
        on: The modules that are on now.
    """
    was = svc.modules_on
    if was == on:
        return
    svc.modules_on = on
    if modules.APPLICATIONS in on and (was is None or modules.APPLICATIONS not in was):
        await _start_applications(svc)
    elif modules.APPLICATIONS not in on and was is not None and modules.APPLICATIONS in was:
        await _stop_applications(svc)
    logger.info("modules_applied", extra={"modules": ",".join(sorted(on))})
