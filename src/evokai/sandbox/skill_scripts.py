"""The ``skill_run`` tool: a skill's own script, run in the isolated code sandbox.

A skill may ship Python scripts under ``scripts/``. The model never sends code here: it names the
skill and the script and passes a JSON object of arguments. The script's text comes from the
stored skill; with it, every other script of the skill and its data files (``assets/``,
``data/``) are copied into a fresh sandbox under ``skill/`` (file mode 0444, folders 0555), the
arguments go to the script's standard input as JSON, and the script runs as its own process with
the skill folder in ``EVOKAI_SKILL_DIR``. What it prints (stdout and stderr, bounded) and the text
files and images it saves under ``output/`` come back, exactly as for ``code_run``.

The tool exists only where ``code_run`` could: the deployment has a sandbox
(``EVOKAI_CODE_ENABLED``), the application is granted ``code`` and the agent enables ``code``.
Elsewhere the scripts are listed as unavailable and no tool is offered. The sandbox has no
network (AgentCore sandbox mode, as for ``code_run``), the call has the same timeout, and on the
way back the output passes the guard like any tool output (secrets removed, instruction-like text
handled by ``untrusted.injection``, spotlighted as data).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from evokai.agents.skills import Skill
from evokai.sandbox.interpreter import Interpreter
from evokai.sandbox.tool import CodeOutcome, compose

if TYPE_CHECKING:
    from evokai.guard.engine import TurnGuard

TOOL_NAME = "skill_run"
MAX_ARGS_CHARS = 100_000
"""Characters of the JSON arguments a call may pass."""
ARGS_FILE = "args.json"
SKILL_DIR = "skill"


def tool_def(skills: list[Skill]) -> dict[str, Any]:
    """The tool's definition.

    Args:
        skills: The skills with scripts the agent may run.

    Returns:
        name, description, schema.
    """
    names = [s.name for s in skills]
    return {
        "name": TOOL_NAME,
        "description": (  # model-facing, English like every tool description
            "Run a script of a skill (listed when you load the skill) in an isolated sandbox "
            "with no internet. Pass the skill, the script's path and its arguments as a JSON "
            "object; the script reads them from stdin. You get what it prints and the files it "
            "saves under output/. You cannot change the script's code."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "enum": names, "description": "The skill"},
                "script": {"type": "string", "description": "Path, like scripts/compute.py"},
                "args": {"type": "object", "description": "The script's input, as JSON"},
            },
            "required": ["name", "script"],
        },
    }


def parse_args(args: dict[str, Any], skills: dict[str, Skill]) -> tuple[Skill, str, str] | str:
    """Check a call's arguments.

    Returns:
        (skill, script path, arguments as JSON), or the problem to tell the model.
    """
    skill = skills.get(str(args.get("name") or ""))
    if skill is None or not skill.scripts:
        return "Unknown skill, or a skill without scripts."
    script = str(args.get("script") or "")
    if script not in {f.path for f in skill.scripts}:
        listed = ", ".join(f.path for f in skill.scripts)
        return (
            f"Skill {skill.name} has no script {script or '(none given)'}; its scripts: {listed}."
        )
    raw = args.get("args")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return "args must be a JSON object."
    text = json.dumps(raw, ensure_ascii=False)
    if len(text) > MAX_ARGS_CHARS:
        return f"args are too large (at most {MAX_ARGS_CHARS:,} characters of JSON)."
    return skill, script, text


def runner(script: str, names: dict[str, str], timeout: float) -> str:
    """The code that lays out the skill in the sandbox and runs one script (written by Evokai).

    Args:
        script: The script's path in the skill.
        names: Input file name to its path in the skill.
        timeout: Seconds the script may take.

    Returns:
        Python source.
    """
    return f"""import json, os, subprocess, sys
NAMES = json.loads({json.dumps(json.dumps(names))})
SCRIPT = {json.dumps(script)}
root = os.path.abspath({json.dumps(SKILL_DIR)})
for src, rel in NAMES.items():
    dst = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(os.path.join("input", src), encoding="utf-8") as f:
        text = f.read()
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(dst, 0o444)
for here, _, _ in os.walk(root):
    os.chmod(here, 0o555)
os.makedirs("output", exist_ok=True)
with open(os.path.join("input", {json.dumps(ARGS_FILE)}), encoding="utf-8") as f:
    args = f.read()
env = dict(os.environ, EVOKAI_SKILL_DIR=root, PYTHONDONTWRITEBYTECODE="1")
try:
    r = subprocess.run(
        [sys.executable, os.path.join(root, *SCRIPT.split("/"))],
        input=args, text=True, capture_output=True, timeout={timeout!r}, env=env,
    )
except subprocess.TimeoutExpired:
    print("The script did not finish in time and was stopped.", file=sys.stderr)
    raise SystemExit(124)
sys.stdout.write(r.stdout)
sys.stderr.write(r.stderr)
if r.returncode:
    raise SystemExit(r.returncode)
"""


async def run_script(
    interpreter: Interpreter,
    args: dict[str, Any],
    skills: dict[str, Skill],
    files_of: Any,
    guard: TurnGuard | None,
    *,
    timeout: float,
) -> CodeOutcome:
    """Run one ``skill_run`` call.

    Args:
        interpreter: The sandbox.
        args: The model's arguments (name, script, args).
        skills: The skills the agent may use.
        files_of: ``async (skill name) -> [(path, text)]``: the stored files of a skill.
        guard: The turn's guard.
        timeout: Seconds for the whole call (as ``code_run``).

    Returns:
        The outcome (a failure is an outcome, never raised).
    """
    base = {
        "type": "code",
        "sandbox": interpreter.kind,
        "region": interpreter.region,
        "interpreter": interpreter.identifier,
        "network": "none",
    }
    parsed = parse_args(args, skills)
    if isinstance(parsed, str):
        return CodeOutcome(ok=False, text=parsed, attrs={**base, "outcome": "error"})
    skill, script, args_json = parsed
    base |= {"skill": skill.name, "script": script}
    stored = dict(await files_of(skill.name))
    if script not in stored:
        return CodeOutcome(
            ok=False, text="The script is not available.", attrs={**base, "outcome": "error"}
        )
    wanted = [p for p, _ in sorted(stored.items()) if _copied(skill, p)]
    names = {f"skill-{i}.txt": p for i, p in enumerate(wanted)}
    files = [(n, stored[p]) for n, p in names.items()] + [(ARGS_FILE, args_json)]
    code = runner(script, names, max(5.0, timeout * 0.8))
    try:
        ex = await interpreter.run(code, files, timeout=timeout)
    except Exception as e:
        return CodeOutcome(
            ok=False,
            text="The sandbox is not available right now. Answer without the script.",
            attrs={**base, "outcome": "error", "errorType": type(e).__name__},
        )
    out = compose(ex, guard, TOOL_NAME)
    out.attrs = {
        **base,
        **out.attrs,
        "type": "code",
        "filesIn": len(wanted),
        "argsChars": len(args_json),
    }
    out.code_chars = len(stored[script])
    out.ran = True
    return out


def _copied(skill: Skill, path: str) -> bool:
    """Whether a file goes into the sandbox with a script: scripts and data files, not references.

    Returns:
        True for kinds ``script`` and ``data``.
    """
    f = skill.file(path)
    return f is not None and f.kind in ("script", "data")


def unavailable_reason(*, deployment: bool, granted: bool, agent: bool) -> str | None:
    """Why a skill's scripts cannot run, level by level (for the console and the packs).

    Returns:
        ``deployment``, ``grant`` or ``agent``, or None when they can run.
    """
    if not deployment:
        return "deployment"
    if not granted:
        return "grant"
    if not agent:
        return "agent"
    return None
