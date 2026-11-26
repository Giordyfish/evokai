# Isolated code execution

An agent can run Python for analysis of data the conversation already has (a CSV a tool returned, numbers in a
message, a file attached to a server run) and show the results and charts it computed. The code never runs inside
Evokai: it runs in **Amazon Bedrock AgentCore Code Interpreter**, an AWS-managed sandbox, in the deployment's EU
region, or in an **Azure Container Apps dynamic sessions** pool in a European Azure region (see
[Azure Container Apps sessions](#azure-container-apps-sessions)).

## Availability

AgentCore built-in tools (Code Interpreter and Browser) are available in Europe (Stockholm), `eu-north-1`, as well as
Frankfurt, Ireland, London, Milan, Paris and Spain, according to the AgentCore "Supported AWS Regions" page read on
16/11/2026; a live call in `eu-north-1` confirmed it. Evokai only uses EU regions: a region outside
`eu-*` leaves the tool unavailable.

## Turning it on

Three switches, all off by default:

| Where | Setting | Effect |
|---|---|---|
| deployment | `EVOKAI_CODE_ENABLED=true` | Evokai builds the AgentCore client (region `EVOKAI_CODE_REGION`, default `EVOKAI_AWS_REGION`) |
| application | grant `code: {"*": true}` (Permissions page, `PATCH /v1/admin/grants`, `grants.code` in YAML) | the application may use it; off until granted, like A2A |
| agent | `code: {enabled: true}` in the definition (agent editor, Knowledge section) | the agent gets the `code_run` tool |

The task role needs `bedrock-agentcore:StartCodeInterpreterSession`, `InvokeCodeInterpreter` and
`StopCodeInterpreterSession` on `arn:aws:bedrock-agentcore:<region>:aws:code-interpreter/aws.codeinterpreter.v1`
(or the custom interpreter named by `EVOKAI_CODE_INTERPRETER_ID`).

| Variable | Default | Meaning |
|---|---|---|
| `EVOKAI_CODE_ENABLED` | `false` | Offer `code_run` at all |
| `EVOKAI_CODE_REGION` | `EVOKAI_AWS_REGION` | Where the sandbox runs; EU regions only |
| `EVOKAI_CODE_INTERPRETER_ID` | `aws.codeinterpreter.v1` | The AWS-managed interpreter, or a custom one |
| `EVOKAI_CODE_TIMEOUT_SECONDS` | `60` | What one call may take, start and file transfers included |
| `EVOKAI_CODE_USD_PER_SECOND` | `0.00003` | Estimated cost of a sandbox second (1 vCPU, 2 GB), recorded for the caps |

### Azure Container Apps sessions

`EVOKAI_CODE_SANDBOX=azure` runs `code_run` and `skill_run` in a Python code interpreter **session pool** of Azure
Container Apps instead of AgentCore. Each call takes a new session identifier (each session is isolated by a Hyper-V
boundary), writes the input files under `/mnt/data/input/`, runs the code as it was sent, reads back `output/` within
the same caps, and deletes the session. Evokai authenticates with a Microsoft Entra ID token of its managed identity
(audience `https://dynamicsessions.io`), which needs the **Azure ContainerApps Session Executor** role on the pool.

| Variable | Default | Meaning |
|---|---|---|
| `EVOKAI_CODE_SANDBOX` | `agentcore` | `azure` for a session pool |
| `EVOKAI_CODE_SESSION_POOL_ENDPOINT` | none | The pool's `poolManagementEndpoint` (`https://<region>.dynamicsessions.io/subscriptions/.../sessionPools/<name>`); European regions only |

Create the pool with network egress disabled (`az containerapp sessionpool create --container-type PythonLTS
--network-status EgressDisabled`): the pool, not Evokai, decides whether code reaches the internet. One execution runs
at most 220 seconds; `EVOKAI_CODE_TIMEOUT_SECONDS` still bounds the whole call. Set `EVOKAI_CODE_USD_PER_SECOND` to the
pool's own price. The data protection profile judges the processor `azure-container-apps-sessions` in the pool's
region. Not verified against a live Azure account.
Sources: [code interpreter sessions](https://learn.microsoft.com/en-us/azure/container-apps/sessions-code-interpreter),
[the executions API](https://learn.microsoft.com/en-us/azure/container-apps/sessions-tutorial-nodejs).

## The tool

`code_run(code, files?)`: `code` is the Python source (at most 20,000 characters); `files` is a list of at most five
text files `{name, content}` (1,000,000 characters each, 2,000,000 together) written under `input/` first. pandas,
numpy and matplotlib are installed. The agent prints the numbers it needs, saves charts as PNG files under `output/`
and tables as CSV under `output/`.

What comes back:

- **to the model**: stdout and stderr (6,000 characters each), the text files of `output/` (4,000 characters each, at
  most four), the names of charts shown and of files not returned, then capped like any tool result
  (`EVOKAI_TOOL_RESULT_CHARS`);
- **to the person**: each PNG or JPEG under `output/` (at most three, 512 kB each) as a `chart` event of type
  `image`: `{type: "image", title, subtitle, mediaType, data}` with `data` in base64. `<evokai-chat>` and the console's
  playground draw it as an image; a client that does not know the type can skip it. Server runs get the text only.

A failure (a bad file name, an exception in the code, the timeout, the sandbox unreachable) is a tool error the model
reads and can recover from; it never fails the turn.

## Skill scripts: `skill_run`

A skill may ship Python scripts under `scripts/` ([configuration](configuration.md#skills-files-scripts-and-limits)).
They run in the same sandbox, behind the same three switches: only an agent that has `code_run` available
(deployment, grant and agent setting all on, and the tool policy offering `skill_run`) gets the `skill_run` tool;
everywhere else the scripts are listed to the model as unavailable and no tool is offered.

`skill_run(name, script, args?)`: the model names the skill and the script and passes `args`, a JSON object (at most
100,000 characters). It never sends code: the script is the stored one. Evokai writes the script, the skill's other
scripts and its data files (`assets/`, `data/`) into a fresh session, lays them out under `skill/` with file mode 0444
(folders 0555), and runs the script as its own process: `args` arrive as JSON on standard input, the skill folder is
in `EVOKAI_SKILL_DIR`, and the script finds its data next to it (`Path(__file__).parent.parent / "data"`). The
script gets 80% of `EVOKAI_CODE_TIMEOUT_SECONDS`, the call as a whole the full timeout. References stay out of the
sandbox.

What comes back is what `code_run` returns: stdout and stderr (6,000 characters each), the text files and images
saved under `output/` (images as charts in browser turns, text only in server runs), marked as untrusted tool output
on the way back (secrets removed, `untrusted.injection`, spotlighting). A session is recorded as usage of kind `code`
and as a tool span with `skill` and `script`.

## Isolation and safety

- **One session per call.** Each call starts a session, writes the files, runs the code, reads `output/` and stops the
  session. Nothing survives a call, so one person's data never meets another's; the session's own timeout is the call
  timeout plus 30 seconds, as a backstop if Evokai cannot stop it.
- **No network.** The AWS-managed interpreter runs in sandbox network mode: no internet (a DNS lookup fails), and with
  no execution role it reaches no AWS resource either. A custom interpreter in public network mode would change that:
  use one only on purpose.
- **Time.** Past `EVOKAI_CODE_TIMEOUT_SECONDS` the session is stopped from another thread; the call answers "did not
  finish in time".
- **Size.** Inputs and outputs are capped as above; other output files are listed by name only.
- **Guard.** The output is tool output: secrets are removed as `output.secrets` says (and recorded as security events
  with `tool: code_run`), instruction-like text is handled by `untrusted.injection`, personal data is tokenised or
  masked for the model by `pii.model` like every tool result, and the result reaches the model spotlighted. The tool
  policy applies (`tools.deny: [code_run]` removes it; `tools.args`, `tools.rate`, `tools.loop`, `tools.egress` check
  its calls). Images are bytes: they cannot be scanned and are shown as the code drew them; stored replay events keep
  them as they are.
- **What reaches the sandbox.** The arguments are restored before they leave (tokens of `pii.model` become the real
  values again, as for every tool), so the sandbox, an AWS service in the deployment's region, processes the data the
  agent passes: it is listed as a sub-processor in the GDPR pack ([compliance](compliance.md)) when it is on.

### No local fallback

Evokai ships no local sandbox. Running model-written code next to the service safely needs a separate container per
call with no network, a read-only root, seccomp and a user namespace, and CPU, memory, process and time limits, plus an
image kept patched; inside Evokai's own read-only container none of that holds. Without AgentCore the tool is simply
not offered.

## Usage, traces, costs

- Each call that used a session is a usage record of kind `code`, model `agentcore:code-interpreter`: the session time
  in `latency_ms`, the code's length in `characters`, and an estimated cost (`EVOKAI_CODE_USD_PER_SECOND` times the
  seconds, rounded up) that counts against the caps. Eval turns record it as kind `eval`. The console shows shares
  only, never the amount.
- The turn's `tool` span carries `type: code`, `sandbox: agentcore`, `region`, `interpreter`, `network: none`,
  `sessionMs`, `executionMs`, `exitCode`, `timedOut`, `filesIn`, `filesOut`, `images`, `textFiles`, `codeChars`,
  `outputChars`, `secretsFound` and `personalDataFound`; the code and the output are content, kept as
  `tracing_content` allows.

## Tests

`tests/test_code_run.py` runs everything against a fake interpreter and a fake `bedrock-agentcore` client: the three
switches, outputs and charts, usage and traces, secrets, errors and the timeout, server runs, the grant view. One live
test calls AgentCore for real and is skipped unless `EVOKAI_LIVE_CODE=1` (with AWS credentials in the environment).
