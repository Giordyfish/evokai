"""The two kinds of application rows, and the only way to query by kind.

A gateway is stored as an application row with ``kind = "gateway"`` (``evokai.gateway``); an
application has ``kind = "agents"``. Every query that lists or fetches rows of one kind goes through
:func:`applications_only` or :func:`gateways_only` (``tests/test_modules_separation.py`` walks the
source and fails on a query that does not). While a module is off (``evokai.core.modules``) its
rows are invisible through them, unless the caller is retention or erasure (``even_off``), which
keep running on the data of modules that are off.
"""

from __future__ import annotations

from typing import Any, Protocol, Self

import sqlalchemy as sa

from evokai.core import modules
from evokai.db import models as m

APPLICATION = "agents"
"""The kind of an application row that is an application."""
GATEWAY = "gateway"
"""The kind of an application row that is a gateway."""

MODULE_OF = {APPLICATION: modules.APPLICATIONS, GATEWAY: modules.GATEWAY}
"""The module that owns each kind of row."""


class _Filterable(Protocol):
    """A select, update or delete: anything with ``where``."""

    def where(self, *clauses: Any) -> Self:
        """Add criteria."""
        ...


def _only[Q: _Filterable](q: Q, kind: str, even_off: bool) -> Q:
    q = q.where(m.Application.kind == kind)
    if even_off or modules.visible(MODULE_OF[kind]):
        return q
    return q.where(sa.false())


def applications_only[Q: _Filterable](q: Q, *, even_off: bool = False) -> Q:
    """Restrict a query over ``evokai_applications`` to applications.

    Args:
        q: A select, update or delete over application rows.
        even_off: Keep the rows while the applications module is off (retention, erasure, purge).

    Returns:
        The query.
    """
    return _only(q, APPLICATION, even_off)


def gateways_only[Q: _Filterable](q: Q, *, even_off: bool = False) -> Q:
    """Restrict a query over ``evokai_applications`` to gateways.

    Args:
        q: A select, update or delete over application rows.
        even_off: Keep the rows while the gateway module is off (retention, erasure, purge).

    Returns:
        The query.
    """
    return _only(q, GATEWAY, even_off)


def module_of(app: m.Application) -> str:
    """The module an application row belongs to.

    Returns:
        ``applications`` or ``gateway``.
    """
    return MODULE_OF.get(app.kind, modules.APPLICATIONS)
