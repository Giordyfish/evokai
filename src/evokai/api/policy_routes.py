"""Policies: the system policy (deployment owners), and the versions and tests of every policy.

An application's policy is changed where it always was (``PUT /v1/admin/security``, the
application's ``data_protection``); these routes list its versions, compare two, restore one, copy
another policy into it and keep its tests. The system policy has the same routes under
``/v1/console/system-policy``, for the deployment's owners only (the operator key, or a console
login that owns every application). See ``evokai.policies``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from evokai import audit, policies
from evokai.api.console_routes import deployment_actor
from evokai.api.deps import admin_app, console_login, operator, services
from evokai.core.errors import Conflict, Forbidden, NotFound
from evokai.guard import catalogue
from evokai.identity import console
from evokai.identity.auth import bearer
from evokai.identity.models import AppPrincipal
from evokai.services import Services

router = APIRouter(tags=["policies"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]
Owner = [Depends(operator)]
APP = "/v1/admin/policy"
SYSTEM = "/v1/console/system-policy"
From = Annotated[int, Query(alias="from", ge=1)]
To = Annotated[int | None, Query(alias="to", ge=1)]


# Shared by both owners


async def _versions(svc: Services, owner_id: str) -> dict[str, Any]:
    rows = await svc.store.policies.versions(owner_id)
    return {"items": [policies.version_view(v) for v in rows]}


async def _version(svc: Services, owner_id: str, number: int) -> Any:
    row = await svc.store.policies.version(owner_id, number)
    if row is None:
        raise NotFound("Unknown policy version.", code="unknown_version")
    return row


async def _diff(svc: Services, owner_id: str, start: int, end: int | None) -> dict[str, Any]:
    a = await _version(svc, owner_id, start)
    if end is None:
        latest = await svc.store.policies.latest(owner_id)
        end = latest.number if latest else start
    b = await _version(svc, owner_id, end)
    return {"from": a.number, "to": b.number, "changes": policies.diff(a.content, b.content)}


async def _tests(svc: Services, owner_id: str) -> dict[str, Any]:
    return {"items": [policies.test_view(t) for t in await svc.store.policies.tests(owner_id)]}


async def _put_test(
    svc: Services, owner_id: str, test_id: str | None, body: policies.PolicyTestBody
) -> Any:
    if test_id is not None and await svc.store.policies.test(owner_id, test_id) is None:
        raise NotFound("Unknown policy test.", code="unknown_test")
    if test_id is None and len(await svc.store.policies.tests(owner_id)) >= policies.MAX_TESTS:
        raise Conflict(f"A policy has {policies.MAX_TESTS} tests at most.", code="too_many_tests")
    same = await svc.store.policies.by_name(owner_id, body.name)
    if same is not None and same.id != test_id:
        raise Conflict("A policy test with this name exists.", code="test_name_taken")
    return await svc.store.policies.put_test(
        owner_id,
        test_id,
        name=body.name,
        input=body.input,
        outcome=body.outcome,
        controls=body.controls,
    )


async def _run(svc: Services, owner_id: str, security: Any) -> dict[str, Any]:
    rows = await svc.store.policies.tests(owner_id)
    results = await policies.run_tests(
        svc.guard, policies.effective(svc.guard, owner_id, security), rows
    )
    failed = sum(1 for r in results if not r["passed"])
    return {"items": results, "passed": len(results) - failed, "failed": failed}


def _saved(row: Any, latest: Any) -> dict[str, Any]:
    """The answer of a restore or a copy: the new version, or the current one when unchanged.

    Returns:
        created, version.
    """
    current = row or latest
    return {"created": row is not None, "version": current.number if current else 0}


# An application's policy


async def _app(svc: Services, a: AppPrincipal) -> Any:
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    return app


def _app_content(app: Any) -> policies.PolicyContent:
    return policies.content_of({
        "security": app.security,
        "data_protection": app.data_protection,
    })


@router.get(f"{APP}/versions")
async def app_versions(svc: Svc, a: Admin) -> dict[str, Any]:
    """The versions of the application's policy (security and data protection), newest first.

    Returns:
        items: number, createdAt, createdBy, note.
    """
    return await _versions(svc, a.application_id)


@router.get(f"{APP}/versions/{{number}}")
async def app_version(number: int, svc: Svc, a: Admin) -> dict[str, Any]:
    """One version of the application's policy, with its content.

    Returns:
        number, createdAt, createdBy, note, content (``security``, ``data_protection``).
    """
    return policies.version_view(await _version(svc, a.application_id, number), content=True)


@router.get(f"{APP}/diff")
async def app_diff(svc: Svc, a: Admin, start: From, end: To = None) -> dict[str, Any]:
    """What changes between two versions of the application's policy (``to``: the current one).

    Returns:
        from, to, changes: ``{path, change, before, after}`` per setting.
    """
    return await _diff(svc, a.application_id, start, end)


@router.post(f"{APP}/versions/{{number}}/restore")
async def app_restore(
    number: int, body: policies.SaveOptions, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Save an older version's content as the application's new policy version (owner).

    The policy tests run first, as for any save (409 ``policy_tests_failed`` unless ``force``).

    Returns:
        created (false when the content is already the current one), version.
    """
    app = await _app(svc, a)
    old = await _version(svc, app.id, number)
    row = await policies.save_application(
        svc,
        app,
        policies.content_of(old.content),
        by=audit.actor_of(a)[0],
        note=body.note or f"Restored from version {number}",
        force=body.force,
    )
    await audit.record(
        request,
        a,
        "policy.restore",
        target=str(number),
        before=_app_content(app).stored(),
        after=old.content,
    )
    return _saved(row, await svc.store.policies.latest(app.id))


async def _may_copy(
    request: Request, svc: Services, a: AppPrincipal, body: policies.CopyFrom
) -> tuple[policies.PolicyContent, str]:
    """The content a caller may copy.

    The system policy (deployment owners), or an application the caller has a role on.

    Returns:
        The content and the source's name.

    Raises:
        Forbidden: The caller may not see the system policy.
        NotFound: An unknown application, or one the caller may not see.
    """
    login = None
    if request.cookies.get(console.COOKIE) and not bearer(request.headers.get("authorization")):
        login = await console_login(request)
    if body.system:
        if login is None or not login.deployment_owner:
            raise Forbidden(
                "Only the deployment's owners see the system policy.", code="system_policy"
            )
        sp = policies.system()
        content = policies.PolicyContent(security=sp.security, data_protection=sp.data_protection)
        return content, "the system policy"
    src = await svc.store.applications.by_slug(body.application or "")
    visible = src is not None and (
        src.id == a.application_id or (login is not None and login.role_for(src) is not None)
    )
    if src is None or not visible:
        raise NotFound("Unknown application.", code="unknown_application")
    return _app_content(src), src.slug


@router.post(f"{APP}/copy")
async def app_copy(body: policies.CopyFrom, svc: Svc, a: Admin, request: Request) -> dict[str, Any]:
    """Copy another policy into the application's as a new version (owner).

    The source is the system policy or an application the caller can see; the policy tests run
    first.

    Returns:
        created, version.
    """
    app = await _app(svc, a)
    content, name = await _may_copy(request, svc, a, body)
    row = await policies.save_application(
        svc,
        app,
        content,
        by=audit.actor_of(a)[0],
        note=body.note or f"Copied from {name}",
        force=body.force,
    )
    await audit.record(
        request,
        a,
        "policy.copy",
        target=name,
        before=_app_content(app).stored(),
        after=content.stored(),
    )
    return _saved(row, await svc.store.policies.latest(app.id))


@router.get(f"{APP}/tests")
async def app_tests(svc: Svc, a: Admin) -> dict[str, Any]:
    """The application's policy tests.

    Returns:
        items: id, name, input, outcome, controls, updatedAt.
    """
    return await _tests(svc, a.application_id)


@router.post(f"{APP}/tests", status_code=201)
async def app_add_test(
    body: policies.PolicyTestBody, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Add a policy test (owner): a text and the outcome the guard's input checks must give it.

    Returns:
        The test.
    """
    row = await _put_test(svc, a.application_id, None, body)
    await audit.record(request, a, "policy.test_put", target=body.name, after=body.model_dump())
    return policies.test_view(row)


@router.put(f"{APP}/tests/{{test_id}}")
async def app_put_test(
    test_id: str, body: policies.PolicyTestBody, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Replace a policy test (owner).

    Returns:
        The test.
    """
    row = await _put_test(svc, a.application_id, test_id, body)
    await audit.record(request, a, "policy.test_put", target=body.name, after=body.model_dump())
    return policies.test_view(row)


@router.delete(f"{APP}/tests/{{test_id}}")
async def app_delete_test(test_id: str, svc: Svc, a: Admin, request: Request) -> dict[str, bool]:
    """Remove a policy test (owner).

    Returns:
        ok.

    Raises:
        NotFound: Unknown test.
    """
    if not await svc.store.policies.delete_test(a.application_id, test_id):
        raise NotFound("Unknown policy test.", code="unknown_test")
    await audit.record(request, a, "policy.test_delete", target=test_id)
    return {"ok": True}


@router.post(f"{APP}/tests/run")
async def app_run_tests(svc: Svc, a: Admin) -> dict[str, Any]:
    """Run the application's policy tests against its current policy (no model is called).

    Returns:
        items (per test: id, name, passed, expected, got), passed, failed.
    """
    app = await _app(svc, a)
    return await _run(svc, app.id, app.security)


# The system policy


def _system_view(svc: Services) -> dict[str, Any]:
    return {
        **policies.system_view(svc.guard),
        "enabled": svc.guard.enabled,
        "controls": catalogue(),
    }


@router.get(SYSTEM, dependencies=Owner)
async def get_system_policy(svc: Svc) -> dict[str, Any]:
    """The system policy (deployment owners) and what an application that sets nothing gets.

    Returns:
        version, content (``security``: minimum modes and default settings;
        ``data_protection``: the profile every application's processors must also meet),
        effective (modes, settings, floors), enabled, controls.
    """
    await policies.load_system(svc.store)
    return _system_view(svc)


@router.put(SYSTEM, dependencies=Owner)
async def put_system_policy(
    body: policies.SavePolicy, svc: Svc, request: Request
) -> dict[str, Any]:
    """Save the system policy as a new version (deployment owners).

    Its modes are minimums for every application's controls, its profile holds as well as each
    application's, its settings are defaults. Its policy tests run first (409
    ``policy_tests_failed`` unless ``force``). In force on this instance at once, on the others
    within ``REFRESH_SECONDS``.

    Returns:
        The system policy, as ``GET`` shows it.
    """
    before = await policies.load_system(svc.store)
    actor = deployment_actor(request, svc)
    content = policies.PolicyContent(security=body.security, data_protection=body.data_protection)
    await policies.save_system(svc, content, by=actor.actor, note=body.note, force=body.force)
    await audit.record(
        request,
        actor,
        "system_policy.put",
        target="system",
        before=policies.PolicyContent(
            security=before.security, data_protection=before.data_protection
        ).stored(),
        after=content.stored(),
    )
    return _system_view(svc)


@router.get(f"{SYSTEM}/versions", dependencies=Owner)
async def system_versions(svc: Svc) -> dict[str, Any]:
    """The system policy's versions, newest first.

    Returns:
        items: number, createdAt, createdBy, note.
    """
    return await _versions(svc, policies.SYSTEM)


@router.get(f"{SYSTEM}/versions/{{number}}", dependencies=Owner)
async def system_version(number: int, svc: Svc) -> dict[str, Any]:
    """One version of the system policy, with its content.

    Returns:
        number, createdAt, createdBy, note, content.
    """
    return policies.version_view(await _version(svc, policies.SYSTEM, number), content=True)


@router.get(f"{SYSTEM}/diff", dependencies=Owner)
async def system_diff(svc: Svc, start: From, end: To = None) -> dict[str, Any]:
    """What changes between two versions of the system policy (``to``: the current one).

    Returns:
        from, to, changes.
    """
    return await _diff(svc, policies.SYSTEM, start, end)


@router.post(f"{SYSTEM}/versions/{{number}}/restore", dependencies=Owner)
async def system_restore(
    number: int, body: policies.SaveOptions, svc: Svc, request: Request
) -> dict[str, Any]:
    """Save an older version's content as the system policy's new version.

    Returns:
        created, version.
    """
    old = await _version(svc, policies.SYSTEM, number)
    actor = deployment_actor(request, svc)
    row = await policies.save_system(
        svc,
        policies.content_of(old.content),
        by=actor.actor,
        note=body.note or f"Restored from version {number}",
        force=body.force,
    )
    await audit.record(
        request, actor, "system_policy.restore", target=str(number), after=old.content
    )
    return _saved(row, await svc.store.policies.latest(policies.SYSTEM))


class SystemCopy(policies.SaveOptions):
    """The application whose policy the system policy copies."""

    application: str


@router.post(f"{SYSTEM}/copy", dependencies=Owner)
async def system_copy(body: SystemCopy, svc: Svc, request: Request) -> dict[str, Any]:
    """Copy an application's policy into the system policy as a new version.

    Its browser declaration is left out: each application declares its own.

    Returns:
        created, version.

    Raises:
        NotFound: Unknown application.
    """
    src = await svc.store.applications.by_slug(body.application)
    if src is None:
        raise NotFound("Unknown application.", code="unknown_application")
    content = _app_content(src)
    if content.data_protection is not None:
        content.data_protection = content.data_protection.model_copy(update={"browser": None})
    actor = deployment_actor(request, svc)
    row = await policies.save_system(
        svc, content, by=actor.actor, note=body.note or f"Copied from {src.slug}", force=body.force
    )
    await audit.record(
        request, actor, "system_policy.copy", target=src.slug, after=content.stored()
    )
    return _saved(row, await svc.store.policies.latest(policies.SYSTEM))


@router.get(f"{SYSTEM}/tests", dependencies=Owner)
async def system_tests(svc: Svc) -> dict[str, Any]:
    """The system policy's tests.

    Returns:
        items.
    """
    return await _tests(svc, policies.SYSTEM)


@router.post(f"{SYSTEM}/tests", dependencies=Owner, status_code=201)
async def system_add_test(
    body: policies.PolicyTestBody, svc: Svc, request: Request
) -> dict[str, Any]:
    """Add a test of the system policy.

    Returns:
        The test.
    """
    row = await _put_test(svc, policies.SYSTEM, None, body)
    await audit.record(
        request, deployment_actor(request, svc), "system_policy.test_put", target=body.name
    )
    return policies.test_view(row)


@router.put(f"{SYSTEM}/tests/{{test_id}}", dependencies=Owner)
async def system_put_test(
    test_id: str, body: policies.PolicyTestBody, svc: Svc, request: Request
) -> dict[str, Any]:
    """Replace a test of the system policy.

    Returns:
        The test.
    """
    row = await _put_test(svc, policies.SYSTEM, test_id, body)
    await audit.record(
        request, deployment_actor(request, svc), "system_policy.test_put", target=body.name
    )
    return policies.test_view(row)


@router.delete(f"{SYSTEM}/tests/{{test_id}}", dependencies=Owner)
async def system_delete_test(test_id: str, svc: Svc, request: Request) -> dict[str, bool]:
    """Remove a test of the system policy.

    Returns:
        ok.

    Raises:
        NotFound: Unknown test.
    """
    if not await svc.store.policies.delete_test(policies.SYSTEM, test_id):
        raise NotFound("Unknown policy test.", code="unknown_test")
    await audit.record(
        request, deployment_actor(request, svc), "system_policy.test_delete", target=test_id
    )
    return {"ok": True}


@router.post(f"{SYSTEM}/tests/run", dependencies=Owner)
async def system_run_tests(svc: Svc) -> dict[str, Any]:
    """Run the system policy's tests against it, as an application that sets nothing gets it.

    Returns:
        items, passed, failed.
    """
    sp = await policies.load_system(svc.store)
    return await _run(svc, policies.SYSTEM, sp.security)
