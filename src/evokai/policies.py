"""Policies: the deployment's system policy, the versions of every policy, and policy tests.

A **policy** is a security policy (``evokai.guard.config``) and a data protection profile
(``evokai.protection``). Each application has its own (``security`` and ``data_protection`` on its
row; a gateway, stored as an application row, too), and the deployment has one **system policy**
that can only make things stricter:

- its modes are **minimums**: the effective mode of a control is the stricter of the system's
  minimum and the mode resolved from the agent, the application and the defaults
  (``off < monitor < enforce``); its ``mode`` is the minimum of every control it does not name;
- its data protection profile holds **as well as** the application's: a processor must pass both;
- its settings (personal data types, scope deny lists, limits...) are **defaults**, used where the
  application or the agent sets nothing. They are not floors: an application may set its own.

Every save of a policy, through any path, writes an immutable **version** (number, content, who,
when, note); the current version is what applies. A restore saves an old content as a new version.
The system policy's current content is its latest version (``owner_id`` ``system``); an
application's is on its row and is also its latest version.

**Policy tests** are texts with the outcome the guard's input checks must give them, overall or
per control (``allowed``, ``flagged``, ``blocked``). They run those checks only, with the effective
policy, never a model or a classifier, so they cost nothing. They run on demand and before a new
version is saved: a save whose tests fail is refused (409 ``policy_tests_failed``) unless it is
forced.

The system policy is read on every turn, so each process keeps it in memory: loaded at start,
reloaded right after a save, and every ``REFRESH_SECONDS`` (another instance's save reaches this
one within that time).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evokai.core.errors import BadRequest, Conflict
from evokai.guard.config import Effective, SecurityPolicy, resolve
from evokai.protection import DataProtection

SYSTEM = "system"
"""The owner id of the system policy's versions and tests."""
REFRESH_SECONDS = 15
"""How often a process reloads the system policy (another instance's save reaches it by then)."""
MAX_TESTS = 200
"""Tests a policy may have: every save runs them all, synchronously."""
Outcome = Literal["allowed", "flagged", "blocked"]
OUTCOMES: tuple[Outcome, ...] = ("allowed", "flagged", "blocked")
TESTABLE: tuple[str, ...] = (
    "input.size",
    "input.encoding",
    "input.secrets",
    "input.injection",
    "scope",
    "untrusted.injection",
    "pii.model",
)
"""The controls the guard's input checks run (``untrusted.injection`` when the policy treats the
person's input as data). ``input.moderation`` is a classifier model: policy tests never call it.
``pii.model`` acts in a gateway's tests only: a gateway blocks or records personal data on its way
to the provider, while an application tokenises or masks it after the input checks."""


# The system policy, as this process knows it


@dataclass(frozen=True)
class SystemPolicy:
    """The system policy in force in this process."""

    version: int = 0
    """Its latest version's number (0: never saved)."""
    security: SecurityPolicy | None = None
    data_protection: DataProtection | None = None


_current = SystemPolicy()


def system() -> SystemPolicy:
    """The system policy in force.

    Returns:
        It (empty until one is saved).
    """
    return _current


def system_security() -> SecurityPolicy | None:
    """The system policy's security part.

    Returns:
        It, or None.
    """
    return _current.security


def system_protection() -> DataProtection | None:
    """The system policy's data protection profile.

    Returns:
        It, or None.
    """
    return _current.data_protection


def set_system(version: int, content: dict[str, Any] | None) -> SystemPolicy:
    """Put a system policy in force in this process.

    Returns:
        It.
    """
    global _current
    content = content or {}
    sec, dp = content.get("security"), content.get("data_protection")
    _current = SystemPolicy(
        version,
        SecurityPolicy.model_validate(sec) if sec else None,
        DataProtection.from_stored(dp) if dp else None,
    )
    return _current


async def load_system(store: Any) -> SystemPolicy:
    """Read the system policy's latest version and put it in force.

    Returns:
        It.
    """
    row = await store.policies.latest(SYSTEM)
    return set_system(row.number, row.content) if row else set_system(0, None)


# Contents and their differences


class PolicyContent(BaseModel):
    """What a policy holds: a security policy and a data protection profile."""

    model_config = ConfigDict(extra="forbid")
    security: SecurityPolicy | None = None
    data_protection: DataProtection | None = None

    def stored(self) -> dict[str, Any]:
        """The content as a version keeps it.

        Returns:
            ``{"security": ..., "data_protection": ...}``.
        """
        return {
            "security": self.security.stored() if self.security else None,
            "data_protection": self.data_protection.stored() if self.data_protection else None,
        }


class SavePolicy(PolicyContent):
    """A new version of a policy."""

    note: str | None = Field(default=None, max_length=500)
    """Why it changes (kept with the version)."""
    force: bool = False
    """Save even when policy tests fail."""


class SaveOptions(BaseModel):
    """How to save a restored or copied content."""

    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=500)
    force: bool = False
    """Save even when policy tests fail."""


class CopyFrom(SaveOptions):
    """Where to copy a policy from: the system policy or another application (by slug)."""

    system: bool = False
    application: str | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def _one(self) -> CopyFrom:
        if self.system == bool(self.application):
            raise ValueError("name exactly one source: system or application")
        return self


def content_of(raw: dict[str, Any] | None) -> PolicyContent:
    """A stored content, validated.

    Returns:
        The content.
    """
    raw = raw or {}
    dp = raw.get("data_protection")
    return PolicyContent(
        security=SecurityPolicy.model_validate(raw["security"]) if raw.get("security") else None,
        data_protection=DataProtection.from_stored(dp) if dp else None,
    )


def _flatten(value: Any, path: tuple[str, ...], out: dict[tuple[str, ...], Any]) -> None:
    if isinstance(value, dict) and value:
        for k, v in value.items():
            _flatten(v, (*path, str(k)), out)
    elif value not in (None, {}):
        out[path] = value


def diff(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[dict[str, Any]]:
    """What changes from one content to another, setting by setting.

    Lists are compared whole. A path is a list of keys (control ids hold dots).

    Returns:
        ``{path, change, before, after}`` per setting (``change``: added, removed, changed),
        in path order.
    """
    a: dict[tuple[str, ...], Any] = {}
    b: dict[tuple[str, ...], Any] = {}
    _flatten(before or {}, (), a)
    _flatten(after or {}, (), b)
    out = []
    for path in sorted(set(a) | set(b)):
        x, y = a.get(path), b.get(path)
        if x == y:
            continue
        change = "added" if path not in a else "removed" if path not in b else "changed"
        out.append({"path": list(path), "change": change, "before": x, "after": y})
    return out


def version_view(row: Any, *, content: bool = False) -> dict[str, Any]:
    """A version as the API shows it.

    Returns:
        number, createdAt, createdBy, note, and content when asked.
    """
    out = {
        "number": row.number,
        "createdAt": row.created_at.isoformat(),
        "createdBy": row.created_by,
        "note": row.note,
    }
    if content:
        out["content"] = row.content
    return out


# Policy tests


class PolicyTestBody(BaseModel):
    """A policy test: a text and the outcome it must get, overall or per control (or both)."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    input: str = Field(min_length=1, max_length=100_000)
    """The person's message the guard's input checks look at."""
    outcome: Outcome | None = None
    """``blocked`` (a control refuses it), ``flagged`` (a control records or changes it, nothing
    refuses it) or ``allowed`` (no control acts)."""
    controls: dict[str, Outcome] | None = None
    """Control id to its expected outcome (``allowed``: that control does not act)."""

    @field_validator("controls")
    @classmethod
    def _controls(cls, v: dict[str, Outcome] | None) -> dict[str, Outcome] | None:
        if v and (bad := [k for k in v if k not in TESTABLE]):
            raise ValueError(
                f"policy tests check only the input controls {', '.join(TESTABLE)}: {bad}"
            )
        return v or None

    @model_validator(mode="after")
    def _expects(self) -> PolicyTestBody:
        if self.outcome is None and not self.controls:
            raise ValueError("a policy test expects an outcome, per control or overall")
        return self


def test_view(row: Any) -> dict[str, Any]:
    """A test as the API shows it.

    Returns:
        id, name, input, outcome, controls, updatedAt.
    """
    return {
        "id": row.id,
        "name": row.name,
        "input": row.input,
        "outcome": row.outcome,
        "controls": row.controls,
        "updatedAt": row.updated_at.isoformat(),
    }


async def evaluate(guard: Any, policy: Effective, text: str) -> dict[str, Any]:
    """What the guard's input checks do with a text under a policy, without a model.

    The checks run as on a person's chat message, offline: classifiers are not asked, no event
    is stored or logged. A blocking control ends the checks, as in a turn.

    Returns:
        ``outcome`` and ``controls`` (each control that acted: ``blocked`` or ``flagged``).
    """
    from evokai.guard.engine import TurnGuard

    tg = TurnGuard(
        guard=guard,
        policy=policy,
        application_id="",
        agent="",
        subject=None,
        session_id=None,
        turn_id=None,
        channel="browser",
        boundary="policy-test",
        offline=True,
    )
    decision = await tg.check_input(text)
    controls: dict[str, str] = {}
    for e in tg.events:
        got = "blocked" if e["action"] == "blocked" else "flagged"
        if controls.get(e["control"]) != "blocked":
            controls[e["control"]] = got
    outcome = "blocked" if decision.blocked else "flagged" if controls else "allowed"
    return {"outcome": outcome, "controls": controls}


async def run_tests(
    guard: Any, policy: Effective, rows: list[Any], *, gateway: bool = False
) -> list[dict[str, Any]]:
    """Run policy tests under a policy.

    Args:
        guard: The guard.
        policy: The effective policy.
        rows: The tests.
        gateway: The policy is a gateway's: its own input checks run (``evokai.gateway.checks``).

    Returns:
        Per test: id, name, passed, expected (outcome, controls), got (outcome, controls).
    """
    from evokai.gateway import checks

    out = []
    for row in rows:
        got = (
            checks.evaluate(policy, row.input, guard.known_secrets)
            if gateway
            else await evaluate(guard, policy, row.input)
        )
        ok = row.outcome is None or got["outcome"] == row.outcome
        for control, want in (row.controls or {}).items():
            ok = ok and got["controls"].get(control, "allowed") == want
        out.append({
            "id": row.id,
            "name": row.name,
            "passed": ok,
            "expected": {"outcome": row.outcome, "controls": row.controls},
            "got": got,
        })
    return out


def effective(
    guard: Any, owner_id: str, security: SecurityPolicy | dict[str, Any] | None
) -> Effective:
    """The policy a policy's tests run under.

    An application's (with the system policy in force), or the system policy's (what an
    application that sets nothing gets).

    Returns:
        The effective policy.
    """
    disabled = not guard.enabled
    if owner_id == SYSTEM:
        return resolve(None, disabled=disabled, system=security)
    return resolve(security, disabled=disabled, system=system_security())


async def is_gateway(svc: Any, owner_id: str) -> bool:
    """Whether a policy is a gateway's (its tests run the gateway's checks).

    Returns:
        True for a gateway.
    """
    if owner_id == SYSTEM:
        return False
    app = await svc.store.applications.get(owner_id)
    return app is not None and app.kind == "gateway"


class PolicyTestsFailed(Conflict):
    """A save refused because policy tests fail (409 ``policy_tests_failed``)."""

    def __init__(self, failures: list[dict[str, Any]]) -> None:
        """Hold the failures.

        Args:
            failures: The results of the tests that fail.
        """
        super().__init__(
            f"{len(failures)} policy test(s) fail with this policy: "
            + ", ".join(f["name"] for f in failures[:10])
            + ". Fix the policy or the tests, or save with force.",
            code="policy_tests_failed",
        )
        self.extra = {"failures": failures}


async def gate(svc: Any, owner_id: str, security: Any, *, force: bool) -> list[dict[str, Any]]:
    """Run a policy's tests against a candidate security policy before it is saved.

    Returns:
        The results (every test), when none fails or the save is forced.

    Raises:
        PolicyTestsFailed: A test fails and the save is not forced.
    """
    rows = await svc.store.policies.tests(owner_id)
    if not rows:
        return []
    results = await run_tests(
        svc.guard,
        effective(svc.guard, owner_id, security),
        rows,
        gateway=await is_gateway(svc, owner_id),
    )
    failures = [r for r in results if not r["passed"]]
    if failures and not force:
        raise PolicyTestsFailed(failures)
    return results


# Saving


def check_pii_types(security: SecurityPolicy | None, declared: set[str]) -> None:
    """``pii.types`` names only built-in identifiers or the application's declared ones.

    Raises:
        BadRequest: An unknown type (400 ``unknown_pii_types``).
    """
    from evokai.guard.pii import PII_TYPES

    types = security.pii.types if security and security.pii else None
    if bad := [t for t in types or [] if t not in PII_TYPES and t not in declared]:
        raise BadRequest(
            f"Unknown personal data types: {', '.join(bad)}. An application's own identifiers "
            "are declared in its config file (application.identifiers).",
            code="unknown_pii_types",
        )


async def save_application(
    svc: Any,
    app: Any,
    content: PolicyContent,
    *,
    by: str,
    note: str | None = None,
    force: bool = False,
    parts: tuple[str, ...] = ("security", "data_protection"),
) -> Any:
    """Save an application's policy (or a part of it) as a new version, after its tests.

    Its tests failing refuse the save (``PolicyTestsFailed``) unless it is forced.

    Returns:
        The new version, or None when nothing changed.
    """
    from evokai.guard import identifiers

    if "security" in parts:
        check_pii_types(content.security, {i.name for i in identifiers.of_app(app)})
        await gate(svc, app.id, content.security, force=force)
    stored = content.stored()
    return await svc.store.applications.update(
        app.id, by=by, note=note, **{k: stored[k] for k in parts}
    )


async def save_system(
    svc: Any, content: PolicyContent, *, by: str, note: str | None = None, force: bool = False
) -> Any:
    """Save the system policy as a new version, after its tests, and put it in force here.

    Its tests failing refuse the save (``PolicyTestsFailed``) unless it is forced.

    Returns:
        The new version, or None when nothing changed.

    Raises:
        BadRequest: The profile declares what browsers may send (an application's declaration),
            or ``pii.types`` names an application's own identifier.
    """
    if content.data_protection and content.data_protection.browser:
        raise BadRequest(
            "The system policy's data protection profile has no browser declaration: each "
            "application declares what its browser sends.",
            code="system_browser",
        )
    check_pii_types(content.security, set())
    await gate(svc, SYSTEM, content.security, force=force)
    row = await svc.store.policies.add_version(SYSTEM, content.stored(), by, note)
    await load_system(svc.store)
    return row


def system_view(guard: Any) -> dict[str, Any]:
    """The system policy and what an application that sets nothing gets, for the console.

    Returns:
        version, content, effective (modes, settings, floors).
    """
    sp = system()
    content = PolicyContent(security=sp.security, data_protection=sp.data_protection)
    return {
        "version": sp.version,
        "content": content.stored(),
        "effective": effective(guard, SYSTEM, sp.security).view(),
    }
