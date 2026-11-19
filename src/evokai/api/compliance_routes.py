"""Compliance packs of an application: the profile, the packs as of now, snapshots and their diff.

Shares and facts only: no route here returns personal data or a cost amount. Reads need a viewer,
snapshots an operator, the profile (the controller's own statements) an owner.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from evokai import audit
from evokai.api.deps import admin_app, services
from evokai.compliance import current_facts, take_snapshot
from evokai.compliance.diff import diff as snapshot_diff
from evokai.compliance.packs import build
from evokai.compliance.profile import ComplianceProfile, load, problems
from evokai.compliance.render import to_html, to_markdown
from evokai.compliance.store import ComplianceRepository
from evokai.compliance.texts import LANGS
from evokai.core.errors import BadRequest, NotFound
from evokai.db import models as m
from evokai.guard import pii, secrets
from evokai.i18n import locale_of
from evokai.identity.models import AppPrincipal
from evokai.services import Services

router = APIRouter(prefix="/v1/admin/compliance", tags=["compliance"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]
PackName = Literal["ai-act", "gdpr", "controls"]
Format = Literal["json", "md", "html"]
Lang = Literal["en", "it"]
HTML_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"


class SnapshotBody(BaseModel):
    """A snapshot to take now."""

    label: str = Field(default="", max_length=120)
    """What it is for (``before the November release``); no personal data."""


async def _app(svc: Services, a: AppPrincipal) -> m.Application:
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    return app


def _lang(app: m.Application, lang: str | None) -> str:
    if lang in LANGS:
        return str(lang)
    own = locale_of(app).split("-", 1)[0]
    return own if own in LANGS else "en"


def _render(
    app: m.Application, pack: str, facts: dict[str, Any], lang: str, fmt: str, snap: Any = None
) -> Response:
    data = build(pack, facts, lang, snap).as_json()
    day = str(facts["collectedAt"])[:10]
    name = f"evokai-{app.slug}-{pack}-{snap['id'] if snap else day}-{lang}"
    if fmt == "md":
        return PlainTextResponse(
            to_markdown(data),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="{name}.md"'},
        )
    if fmt == "html":
        return HTMLResponse(
            to_html(data),
            headers={
                "Content-Disposition": f'inline; filename="{name}.html"',
                "Content-Security-Policy": HTML_CSP,
            },
        )
    return Response(
        content=json.dumps(data, ensure_ascii=False),
        media_type="application/json",
    )


@router.get("/profile")
async def get_profile(svc: Svc, a: Admin) -> dict[str, Any]:
    """The application's compliance profile: what only the controller can state.

    Returns:
        profile (every field, with defaults), and updatedAt when one was saved.
    """
    app = await _app(svc, a)
    raw = await ComplianceRepository(svc.db).profile(app.id)
    return {"profile": load(raw).model_dump(), "saved": raw is not None}


@router.put("/profile")
async def put_profile(
    body: ComplianceProfile, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Replace the profile (owner). Text with a personal identifier or a secret is refused.

    Returns:
        profile.

    Raises:
        BadRequest: Fields that carry personal data or secrets (``personal_data``).
    """
    app = await _app(svc, a)
    bad = problems(body)
    if bad:
        raise BadRequest(
            f"Remove personal data and secrets from: {', '.join(bad)}.", code="personal_data"
        )
    repo = ComplianceRepository(svc.db)
    before = await repo.profile(app.id)
    value = body.model_dump()
    await repo.put_profile(app.id, value, audit.actor_of(a)[0])
    await audit.record(
        request, a, "compliance.profile", target=app.slug, before=before, after=value
    )
    return {"profile": value}


@router.get("/snapshots")
async def list_snapshots(
    svc: Svc, a: Admin, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> dict[str, Any]:
    """The application's snapshots, newest first.

    Returns:
        items: id, createdAt, label, byKind, classification, agents.
    """
    app = await _app(svc, a)
    return {"items": await ComplianceRepository(svc.db).snapshots(app.id, limit)}


@router.post("/snapshots", status_code=201)
async def create_snapshot(
    body: SnapshotBody, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Record what is true now (operator): the facts every pack is written from.

    Returns:
        The snapshot, without its facts.

    Raises:
        BadRequest: A label with personal data or a secret.
    """
    app = await _app(svc, a)
    if pii.find(body.label) or secrets.find(body.label):
        raise BadRequest("Remove personal data and secrets from the label.", code="personal_data")
    kind = audit.actor_of(a)[1]
    snap = await take_snapshot(svc, app, label=body.label, by_kind=kind)
    await audit.record(
        request,
        a,
        "compliance.snapshot",
        target=snap["id"],
        after={"label": body.label, "classification": snap["classification"]},
    )
    return {k: v for k, v in snap.items() if k != "facts"}


@router.get("/snapshots/diff")
async def diff_snapshots(
    svc: Svc,
    a: Admin,
    from_: Annotated[str, Query(alias="from", max_length=40)],
    to: Annotated[str, Query(max_length=40)],
    lang: Lang | None = None,
) -> dict[str, Any]:
    """What changed between two snapshots, statement by statement and fact by fact.

    Returns:
        from, to, lang, packs (added, removed, changed; the AI Act classification before and
        after), facts, counts.

    Raises:
        NotFound: An unknown snapshot.
    """
    app = await _app(svc, a)
    repo = ComplianceRepository(svc.db)
    old, new = await repo.snapshot(app.id, from_), await repo.snapshot(app.id, to)
    if old is None or new is None:
        raise NotFound("Unknown snapshot.", code="unknown_snapshot")
    if old["id"] > new["id"]:  # ids sort by time
        old, new = new, old
    return snapshot_diff(old, new, _lang(app, lang))


@router.get("/snapshots/{snapshot_id}/{pack}", response_model=None)
async def snapshot_pack(
    snapshot_id: str,
    pack: PackName,
    svc: Svc,
    a: Admin,
    format: Format = "json",
    lang: Lang | None = None,
) -> Response:
    """A pack as it was when the snapshot was taken.

    Returns:
        The pack as JSON, Markdown or printable HTML.

    Raises:
        NotFound: An unknown snapshot.
    """
    app = await _app(svc, a)
    snap = await ComplianceRepository(svc.db).snapshot(app.id, snapshot_id)
    if snap is None:
        raise NotFound("Unknown snapshot.", code="unknown_snapshot")
    return _render(app, pack, snap["facts"], _lang(app, lang), format, snap)


@router.get("/{pack}", response_model=None)
async def current_pack(
    pack: PackName,
    svc: Svc,
    a: Admin,
    format: Format = "json",
    lang: Lang | None = None,
) -> Response:
    """A pack generated now from the configuration and recent activity.

    ``ai-act``: system, risk classification, technical documentation, significant changes.
    ``gdpr``: record of processing, sub-processors, retention, DPIA template, data subject
    rights, DPA annex. ``controls``: ISO/IEC 27001 Annex A and SOC 2 mapping (support, not
    certification). Every statement carries its sources.

    Returns:
        The pack as JSON, Markdown or printable HTML, in English or Italian.
    """
    app = await _app(svc, a)
    return _render(app, pack, await current_facts(svc, app), _lang(app, lang), format)
