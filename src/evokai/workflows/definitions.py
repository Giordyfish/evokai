"""Workflow definitions: steps, triggers, inputs and outputs, as data (YAML or JSON), never code.

A workflow is a list of steps with ids. Steps run in order; a ``condition`` jumps to one of two
later steps, any step may jump forward with ``next``, and ``parallel`` runs branches of steps at
the same time and joins them. Jumps only go forward (or to ``end``), so a run always ends.

Definitions are versioned like agents (the version is a hash of the content) and a run keeps the
version it started with.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from evokai.workflows import expressions as ex
from evokai.workflows.cron import Cron, CronError, zone

STEP_ID = r"^[a-z][a-z0-9_]{0,39}$"
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
RESERVED_NAMES = frozenset({"runs", "approvals", "validate", "hooks"})
END = "end"
EVENTS = ("agent_run.finished",)
MAX_STEPS = 100
MAX_WAIT_SECONDS = 366 * 86_400

Role = Literal["viewer", "operator", "owner"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Retry(_Model):
    """Retries of a step that failed for a reason that may pass (a timeout, a tool error)."""

    max_attempts: int = Field(default=3, ge=1, le=20)
    """Attempts in all, the first included."""
    backoff_seconds: float = Field(default=10, ge=0, le=3_600)
    """Wait before the second attempt; doubled for each next one."""
    max_backoff_seconds: float = Field(default=600, ge=0, le=86_400)


class _Step(_Model):
    id: str = Field(pattern=STEP_ID)
    title: str = Field(default="", max_length=200)
    next: str | None = None
    """The step to go to after this one (a later step of the same list, or ``end``)."""
    timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)
    retry: Retry | None = None
    continue_on_error: bool = False
    """A step that fails for good does not fail the run: its output is null and the run goes on."""


class AgentStep(_Step):
    """A server run of an agent of the application (its ``channels`` must include ``server``)."""

    kind: Literal["agent"]
    agent: str = Field(min_length=1, max_length=80)
    input: str | list[dict[str, Any]] = Field(min_length=1)
    """A template of the run's input text, or of its messages (``[{role, text}]``)."""
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    """Templates of text attachments: ``{name, text, mediaType?}``."""
    output_schema: dict[str, Any] | None = None
    """JSON Schema of the structured answer (an object); default the agent's own."""
    groups: list[str] = Field(default_factory=list, max_length=50)
    """Groups the run searches knowledge bases as."""
    durable: bool = False
    """Run as a pgtask task, its model calls checkpointed: a crashed step resumes from its last
    model call (Postgres and ``EVOKAI_WORKFLOW_DURABLE_AGENTS``, ``evokai.workflows.durable``)."""

    @field_validator("output_schema")
    @classmethod
    def _schema(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            Draft202012Validator.check_schema(v)
            if v.get("type") != "object":
                raise ValueError("output_schema must describe an object")
        return v


class ToolStep(_Step):
    """A call of a tool of one of the application's MCP servers, with mapped arguments."""

    kind: Literal["tool"]
    server: str = Field(min_length=1, max_length=80)
    tool: str = Field(min_length=1, max_length=120)
    """The tool's own name on that server (without the server's prefix)."""
    arguments: dict[str, Any] = Field(default_factory=dict)
    """Templates of the arguments."""
    approval: str | None = None
    """The approval step that authorises this call (required for a tool that changes data,
    unless ``safe`` and the application is granted ``workflows.autonomous``)."""
    safe: bool = False
    """The definition vouches that this call may change data without a person deciding."""
    label: str | list[str] = Field(default_factory=list)
    """What the approval shows for this call: templates over the arguments as in agents'
    ``confirm_labels`` (``{field}``, ``{field|count}``, ``{field|map:table}``); the first complete
    one wins."""
    label_values: dict[str, dict[str, str]] = Field(default_factory=dict)


def _operator() -> list[Role]:
    return ["operator"]


class Approvers(_Model):
    """Who may decide."""

    roles: list[Role] = Field(default_factory=_operator)
    """Console roles (the role or a higher one) that may decide in the console."""
    groups: list[str] = Field(default_factory=list, max_length=50)
    """Groups of the application's users that may decide through the API (any of them); empty =
    any user the application vouches for."""


class ApprovalStep(_Step):
    """A person's decision."""

    kind: Literal["approval"]
    question: str = Field(min_length=1, max_length=1_000)
    """Template of the question."""
    summary: str | list[str] | None = None
    """Templates of what will happen, one line each; default the labels of the tool steps this
    approval authorises."""
    details: dict[str, Any] = Field(default_factory=dict)
    """Templates of facts shown with the question (``{amount: "{{ steps.x.output.total }}"}``)."""
    approvers: Approvers = Field(default_factory=Approvers)
    timeout_seconds: int | None = Field(default=7 * 86_400, ge=60, le=90 * 86_400)
    """How long the decision may wait; then ``on_timeout`` applies."""
    on_timeout: Literal["reject", "approve"] = "reject"
    on_reject: Literal["end", "continue"] = "end"
    """``end``: a rejection ends the run (status ``rejected``); ``continue``: the run goes on and a
    condition can read ``steps.<id>.output.approved``."""


class ConditionStep(_Step):
    """A branch on an expression over the input and earlier outputs."""

    kind: Literal["condition"]
    if_: str = Field(alias="if", min_length=1, max_length=ex.MAX_EXPRESSION)
    then: str | None = None
    """Where to go when true (default the next step)."""
    else_: str | None = Field(default=None, alias="else")
    """Where to go when false (default the next step)."""


class WaitStep(_Step):
    """A pause: a duration, or until a date or time."""

    kind: Literal["wait"]
    seconds: int | None = Field(default=None, ge=0, le=MAX_WAIT_SECONDS)
    until: str | None = Field(default=None, max_length=500)
    """Template of an ISO date or time (a date means midnight in the workflow's time zone)."""


class NotifyStep(_Step):
    """A signed webhook to an allowed address."""

    kind: Literal["notify"]
    url: str = Field(min_length=8, max_length=2_000)
    """Template of the address (https, on a domain of ``EVOKAI_WORKFLOW_NOTIFY_ALLOW``)."""
    body: Any = None
    """Template of the JSON body; default the run's id, workflow and step outputs so far."""
    secret_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,99}$")
    """The environment variable that holds the HMAC secret (``X-Evokai-Signature``)."""


class Branch(_Model):
    """One branch of a ``parallel`` step."""

    name: str = Field(pattern=STEP_ID)
    steps: list[Step] = Field(min_length=1, max_length=MAX_STEPS)


class ParallelStep(_Step):
    """Branches run at the same time; the step ends when every branch has."""

    kind: Literal["parallel"]
    branches: list[Branch] = Field(min_length=2, max_length=10)


Step = Annotated[
    AgentStep | ToolStep | ApprovalStep | ConditionStep | WaitStep | NotifyStep | ParallelStep,
    Field(discriminator="kind"),
]


class Trigger(_Model):
    """How runs start besides a manual start (console, admin API, server API)."""

    kind: Literal["schedule", "webhook", "event"]
    cron: str | None = None
    """``schedule``: a cron expression in the workflow's time zone."""
    event: Literal["agent_run.finished"] | None = None
    """``event``: an internal event."""
    agent: str | None = None
    """``event``: only the runs of this agent."""
    status: Literal["done", "failed", "any"] = "done"
    """``event``: only runs that ended this way."""
    secret_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,99}$")
    """``webhook``: the environment variable that holds the HMAC secret callers sign with."""
    input: Any = None
    """Template of the run's input over ``trigger`` (and ``event``, ``now``); default the
    trigger's payload."""
    subject: str | None = Field(default=None, max_length=500)
    """Template of the run's subject (whose data it is), when it has one."""


class WorkflowDef(_Model):
    """A workflow definition."""

    name: str
    title: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2_000)
    input_schema: dict[str, Any] | None = None
    """JSON Schema of the run's input (an object)."""
    steps: list[Step] = Field(min_length=1, max_length=MAX_STEPS)
    triggers: list[Trigger] = Field(default_factory=list, max_length=10)
    output: Any = None
    """Template of the run's output; default the output of the last step that ran."""
    timezone: str | None = None
    """Default the application's time zone, else UTC."""
    retention_days: int = Field(default=90, ge=1, le=3_650)
    """Finished runs are deleted this many days after they started."""
    tags: list[str] = Field(default_factory=list, max_length=20)
    """Usage tags of its agent steps."""
    version: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME.match(v) or v in RESERVED_NAMES:
            raise ValueError(
                "workflow name: lowercase letters, digits, - and _ (not a reserved word)"
            )
        return v

    @field_validator("input_schema")
    @classmethod
    def _input_schema(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            Draft202012Validator.check_schema(v)
            if v.get("type") != "object":
                raise ValueError("input_schema must describe an object")
        return v

    def content(self) -> dict[str, Any]:
        """The definition without its version.

        Returns:
            A JSON-able dict (aliases as written: ``if``, ``else``).
        """
        return self.model_dump(exclude={"version"}, by_alias=True, exclude_defaults=True)

    def with_version(self) -> WorkflowDef:
        """This definition with its content hash as version.

        Returns:
            A copy with ``version`` set.
        """
        raw = json.dumps(self.content(), sort_keys=True, ensure_ascii=False)
        return self.model_copy(update={"version": hashlib.sha256(raw.encode()).hexdigest()[:12]})

    def all_steps(self) -> list[Any]:
        """Every step, nested ones included, in the order they are written.

        Returns:
            The steps.
        """
        out: list[Any] = []

        def walk(steps: list[Any]) -> None:
            for s in steps:
                out.append(s)
                if isinstance(s, ParallelStep):
                    for b in s.branches:
                        walk(b.steps)

        walk(self.steps)
        return out

    def step(self, step_id: str) -> Any:
        """A step by id.

        Returns:
            The step, or None.
        """
        return next((s for s in self.all_steps() if s.id == step_id), None)


Branch.model_rebuild()
ParallelStep.model_rebuild()
WorkflowDef.model_rebuild()


# where a step sits


def containers(defn: WorkflowDef) -> dict[str, tuple[str, list[Any]]]:
    """The list each step belongs to.

    Returns:
        Step id → (container path, the list): ``""`` for the top level, ``<parallel id>/<branch
        index>`` for a branch.
    """
    out: dict[str, tuple[str, list[Any]]] = {}

    def walk(steps: list[Any], path: str) -> None:
        for s in steps:
            out[s.id] = (path, steps)
            if isinstance(s, ParallelStep):
                for i, b in enumerate(s.branches):
                    walk(b.steps, f"{s.id}/{i}")

    walk(defn.steps, "")
    return out


# validation


class Problem(BaseModel):
    """A problem of a definition, where it is."""

    path: str
    message: str
    line: int | None = None


def _path(loc: tuple[Any, ...]) -> str:
    out = ""
    for p in loc:
        if isinstance(p, int):
            out += f"[{p}]"
        elif p in {
            "agent",
            "tool",
            "approval",
            "condition",
            "wait",
            "notify",
            "parallel",
        } and out.endswith("]"):
            continue  # the discriminator's tag pydantic adds to a union's location
        else:
            out += f".{p}" if out else str(p)
    return out


def check(defn: WorkflowDef) -> list[Problem]:
    """The problems of a parsed definition that its types cannot catch.

    Unique ids, forward jumps, approvals before the tools they authorise, expressions, cron,
    time zone.

    Returns:
        The problems (empty when it is fine).
    """
    problems: list[Problem] = []
    steps = defn.all_steps()
    where = containers(defn)
    order = {s.id: i for i, s in enumerate(steps)}
    seen: set[str] = set()
    for sid, path in _all_paths(defn.steps, "steps"):
        if sid in seen:
            problems.append(Problem(path=path, message=f"duplicate step id {sid}"))
        seen.add(sid)
    if len(steps) > MAX_STEPS:
        problems.append(Problem(path="steps", message=f"more than {MAX_STEPS} steps"))

    def target(s: Any, field: str, value: str | None) -> None:
        if value is None or value == END:
            return
        path = f"{_step_path(defn, s.id)}.{field}"
        if value not in where:
            problems.append(Problem(path=path, message=f"unknown step {value}"))
            return
        here, items = where[s.id]
        there, _ = where[value]
        if here != there:
            problems.append(Problem(path=path, message=f"{value} is not in the same list of steps"))
        elif [x.id for x in items].index(value) <= [x.id for x in items].index(s.id):
            problems.append(Problem(path=path, message=f"{value} does not come after {s.id}"))

    for s in steps:
        base = _step_path(defn, s.id)
        target(s, "next", s.next)
        if isinstance(s, ConditionStep):
            target(s, "then", s.then)
            target(s, "else", s.else_)
            try:
                ex.compile_expression(s.if_)
            except ex.ExpressionError as e:
                problems.append(Problem(path=f"{base}.if", message=str(e)))
        templated: dict[str, Any] = {}
        if isinstance(s, AgentStep):
            templated = {"input": s.input, "attachments": s.attachments}
        elif isinstance(s, ToolStep):
            templated = {"arguments": s.arguments}
            if s.approval is not None:
                a = defn.step(s.approval)
                if not isinstance(a, ApprovalStep):
                    problems.append(
                        Problem(
                            path=f"{base}.approval", message=f"{s.approval} is not an approval step"
                        )
                    )
                elif order[a.id] > order[s.id]:
                    problems.append(
                        Problem(path=f"{base}.approval", message=f"{s.approval} comes after {s.id}")
                    )
        elif isinstance(s, ApprovalStep):
            templated = {"question": s.question, "summary": s.summary, "details": s.details}
        elif isinstance(s, WaitStep):
            if (s.seconds is None) == (s.until is None):
                problems.append(
                    Problem(path=base, message="a wait has seconds or until (one of them)")
                )
            templated = {"until": s.until}
        elif isinstance(s, NotifyStep):
            templated = {"url": s.url, "body": s.body}
        for field, value in templated.items():
            for sub, msg in ex.check_template(value):
                problems.append(
                    Problem(
                        path=f"{base}.{field}{'.' if sub and not sub.startswith('[') else ''}{sub}",
                        message=msg,
                    )
                )
    for sub, msg in ex.check_template(defn.output):
        problems.append(Problem(path=f"output{'.' + sub if sub else ''}", message=msg))
    try:
        zone(defn.timezone)
    except CronError as e:
        problems.append(Problem(path="timezone", message=str(e)))
    hooks = 0
    for i, t in enumerate(defn.triggers):
        path = f"triggers[{i}]"
        if t.kind == "schedule":
            if not t.cron:
                problems.append(
                    Problem(path=f"{path}.cron", message="a schedule needs a cron expression")
                )
            else:
                try:
                    Cron.parse(t.cron)
                except CronError as e:
                    problems.append(Problem(path=f"{path}.cron", message=str(e)))
        elif t.kind == "event":
            if not t.event:
                problems.append(
                    Problem(
                        path=f"{path}.event",
                        message=f"an event trigger names one of {', '.join(EVENTS)}",
                    )
                )
        elif t.kind == "webhook":
            hooks += 1
            if not t.secret_env:
                problems.append(
                    Problem(
                        path=f"{path}.secret_env",
                        message="a webhook needs the environment variable of its secret",
                    )
                )
        for sub, msg in ex.check_template({"input": t.input, "subject": t.subject}):
            problems.append(Problem(path=f"{path}.{sub}", message=msg))
    if hooks > 1:
        problems.append(Problem(path="triggers", message="at most one webhook trigger"))
    return problems


def _all_paths(steps: list[Any], prefix: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for i, s in enumerate(steps):
        here = f"{prefix}[{i}]"
        out.append((s.id, here))
        if isinstance(s, ParallelStep):
            for j, b in enumerate(s.branches):
                out += _all_paths(b.steps, f"{here}.branches[{j}].steps")
    return out


def _step_path(defn: WorkflowDef, step_id: str) -> str:
    def walk(steps: list[Any], prefix: str) -> str | None:
        for i, s in enumerate(steps):
            here = f"{prefix}[{i}]"
            if s.id == step_id:
                return here
            if isinstance(s, ParallelStep):
                for j, b in enumerate(s.branches):
                    found = walk(b.steps, f"{here}.branches[{j}].steps")
                    if found:
                        return found
        return None

    return walk(defn.steps, "steps") or "steps"


def parse(raw: dict[str, Any]) -> tuple[WorkflowDef | None, list[Problem]]:
    """Parse and check a definition.

    Returns:
        The definition with its version (None when it has problems) and the problems.
    """
    if not isinstance(raw, dict):
        return None, [Problem(path="", message="a workflow is an object")]
    try:
        defn = WorkflowDef.model_validate(raw)
    except ValidationError as e:
        return None, [
            Problem(path=_path(tuple(err.get("loc", ()))), message=str(err.get("msg", "invalid")))
            for err in e.errors()[:50]
        ]
    except SchemaError as e:
        return None, [Problem(path="", message=f"invalid JSON Schema: {e.message}")]
    problems = check(defn)
    return (None if problems else defn.with_version()), problems


def parse_text(text: str, fmt: str = "yaml") -> tuple[WorkflowDef | None, list[Problem], Any]:
    """Parse a definition written as YAML or JSON, with the lines of its problems.

    Returns:
        The definition (None with problems), the problems, and the parsed document.
    """
    try:
        raw = json.loads(text) if fmt == "json" else yaml.safe_load(text)
    except json.JSONDecodeError as e:
        return None, [Problem(path="", message=e.msg, line=e.lineno)], None
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        msg = str(getattr(e, "problem", None) or e)
        return None, [Problem(path="", message=msg, line=mark.line + 1 if mark else None)], None
    defn, problems = parse(raw)
    if problems and fmt != "json":
        for p in problems:
            p.line = line_of(text, p.path)
    return defn, problems, raw


def line_of(text: str, path: str) -> int | None:
    """The line of a YAML document where a path (``steps[2].if``) is written.

    Returns:
        The line (1-based), or None when it cannot be found.
    """
    try:
        node = yaml.compose(text)
    except yaml.YAMLError:
        return None
    parts = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    best = node
    for p in parts:
        if node is None:
            break
        if p.startswith("["):
            i = int(p[1:-1])
            if isinstance(node, yaml.SequenceNode) and i < len(node.value):
                node = node.value[i]
                best = node
            else:
                break
        elif isinstance(node, yaml.MappingNode):
            nxt = next((v for k, v in node.value if getattr(k, "value", None) == p), None)
            if nxt is None:
                break
            node = nxt
            best = node
        else:
            break
    return best.start_mark.line + 1 if best is not None else None


def outline(defn: WorkflowDef | None, raw: Any = None) -> list[dict[str, Any]]:
    """The steps as the console draws them (a vertical flow).

    From a definition or, when it has problems, from what could be read of it.

    Returns:
        ``[{id, kind, title, next, then, else, approval, branches: [{name, steps}]}]``.
    """

    def item(s: Any) -> dict[str, Any]:
        get = s.get if isinstance(s, dict) else lambda k, d=None: getattr(s, k, d)
        kind = get("kind")
        out: dict[str, Any] = {
            "id": str(get("id") or "?"),
            "kind": str(kind or "?"),
            "title": str(get("title") or ""),
        }
        detail = {
            "agent": get("agent"),
            "tool": f"{get('server')}/{get('tool')}" if kind == "tool" else None,
            "if": get("if") if isinstance(s, dict) else getattr(s, "if_", None),
            "then": get("then"),
            "else": get("else") if isinstance(s, dict) else getattr(s, "else_", None),
            "next": get("next"),
            "approval": get("approval"),
            "question": get("question"),
            "seconds": get("seconds"),
            "until": get("until"),
            "url": get("url"),
        }
        out.update({k: v for k, v in detail.items() if v not in (None, "")})
        branches = get("branches")
        if isinstance(branches, list):
            out["branches"] = [
                {
                    "name": str((b.get("name") if isinstance(b, dict) else b.name) or "?"),
                    "steps": [
                        item(x)
                        for x in ((b.get("steps") if isinstance(b, dict) else b.steps) or [])
                        if isinstance(x, (dict, BaseModel))
                    ],
                }
                for b in branches
                if isinstance(b, (dict, BaseModel))
            ]
        return out

    if defn is not None:
        return [item(s) for s in defn.steps]
    steps = raw.get("steps") if isinstance(raw, dict) else None
    return [item(s) for s in steps or [] if isinstance(s, dict)]
