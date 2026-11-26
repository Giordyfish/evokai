# Running and deploying Evokai

## Run it

```bash
uv sync --all-extras --dev          # Python 3.12 to 3.14 (the image runs 3.14)
uv run evokai migrate               # SQLite ./evokai.db by default
EVOKAI_CONFIG_FILE=config/bookshop.yaml uv run evokai serve   # http://localhost:8787/agent/health
```

SQLite runs with a write-ahead log (`journal_mode=WAL`, `synchronous=NORMAL`) and waits up to 30 seconds for a write
in progress (`busy_timeout`): a read never waits for a write, and concurrent writes take turns instead of failing with
"database is locked". The database file then has two companions, `evokai.db-wal` and `evokai.db-shm`: back up the
three together, or stop Evokai first. Keep it on a local disk (the write-ahead log does not work on network file
systems); for more than one instance use Postgres.

With Docker, two compose files:

- `docker-compose.yml`, the [quick start](quickstart.md): Evokai and its own Postgres, keys generated on the first
  start into a volume (`EVOKAI_STATE_DIR`), migrations at start (`EVOKAI_AUTO_MIGRATE`), the setup wizard in the
  console. `docker compose up -d --build`, then `http://localhost:8787/agent/admin/`.
- `docker-compose.dev.yml`, a production-like development setup: Postgres, the one-off database setup with a
  separate runtime role, read-only container as in production, and the example `bookshop` application from
  `config/bookshop.yaml` with a development server key (`evk_srv_devlocal_…`), so a backend on
  `http://localhost:3000` can mint session tokens at once.

```bash
eval "$(aws configure export-credentials --profile <profile> --format env)"   # Bedrock, optional
docker compose -f docker-compose.dev.yml up --build           # http://localhost:18787/agent
EVOKAI_VOICE=1 docker compose -f docker-compose.dev.yml up --build   # with server voice (Pipecat)
```

### Keys in a state directory

Production injects `EVOKAI_SIGNING_KEY`, `EVOKAI_ENCRYPTION_KEY` and `EVOKAI_OPERATOR_KEY` from its secret store. An
install without one sets `EVOKAI_STATE_DIR` to a persistent, private directory: on the first start (or
`evokai init`) the three keys are generated into `keys.json` (mode 0600) and the operator key is printed once; later
starts read them back. A key given in the environment always wins over the file. `evokai init
--rotate-operator-key` prints a new operator key (it applies from the next start, and then ends every console login
and assistant delegation made with the old one).


## The admin console

```bash
cd admin && npm ci && npm run build      # admin/dist, served by `evokai serve` at {base}/admin/
open http://localhost:8787/agent/admin/  # sign in with an admin key (EVOKAI_BOOTSTRAP_ADMIN_KEY) or the operator key
cd admin && EVOKAI_URL=http://localhost:8787 npm run dev   # live reload on :5173, API proxied (base /agent)
```

The image builds the console in its own stage and ships it at `/app/admin/dist`. It is internal: in production keep
`{base}/admin` off the public proxy and reach the service through a port-forward, for example with SSM to the
service's loopback port 8788: `http://localhost:8788/evokai/admin/`. `EVOKAI_ADMIN_UI_ENABLED=false` turns it off.

### Signing in from a script

A local script (for example one that opens a port-forward first) can open the console already
signed in, with nothing typed and no key in the browser:

```bash
base=http://localhost:8788/evokai
code=$(curl -fsS -X POST -H "Authorization: Bearer $EVOKAI_ADMIN_KEY" "$base/v1/console/login-code" | jq -r .code)
xdg-open "$base/admin/#/login?code=$code"     # `open` on macOS
```

`POST /v1/console/login-code` takes an admin key of an application or the operator key, exactly like the sign-in form
(the same per-address limit on failed attempts), and answers `{"code": "...", "expiresIn": 60}`. The code is random,
valid for 60 seconds and works once: the console sends it to `POST /v1/console/login` as `{"code": "..."}`, gets the
usual HttpOnly cookie, removes the code from the address bar and opens the applications. An unknown, expired or
already used code is a 401 (`login_code`) and counts as a failed attempt; the console then shows the normal sign-in
form. Codes live in the shared state: with `EVOKAI_REDIS_URL` any instance redeems a code another issued; without it,
in the memory of the instance that issued it, so with several tasks behind a load balancer the browser must reach the
same one (the port-forward to one task does).

## On Azure

[Running on Azure](azure.md) has the Azure Container Apps guide: managed identity, Key Vault references for the keys,
Azure Database for PostgreSQL, Azure Monitor, and the self-hosted alternative of every Azure piece.

## More than one instance

Evokai keeps no state of its own outside the database, except what must be counted across requests. To run several
tasks, give them a Redis or Valkey (`EVOKAI_REDIS_URL`, for example `redis://valkey:6379/1` on a Valkey the application already runs):
abuse and tool rates, concurrent turns per person, session-token request rates, console sign-in codes, failed sign-ins
and sign-outs are then shared ([shared limits](configuration.md#shared-limits-across-instances)). Without it every task
counts alone. Keep a browser session on one task (sticky sessions, or one task per session's SSE stream): a turn is
cancelled on the task that runs it. Redis failing never stops the service; each task falls back to its own memory and
logs a warning.

## Commands

| Command | What it does |
|---|---|
| `evokai serve` | HTTP server on `EVOKAI_PORT` (8787), API under `EVOKAI_BASE_PATH` (`/agent`); with `EVOKAI_STATE_DIR` it reads (or first generates) its keys there, with `EVOKAI_AUTO_MIGRATE=true` it migrates first |
| `evokai init [--rotate-operator-key]` | Generates the keys in `EVOKAI_STATE_DIR` if missing (printing the operator key once) and migrates the database |
| `evokai migrate` | Alembic migrations with the runtime URL |
| `evokai db-setup` | With an **owner** URL, on Postgres: creates the runtime role (`evokai_app`, password from `EVOKAI_APP_DB_PASSWORD`) and the schema (`evokai`), migrates into it and grants the role USAGE + CRUD only. Idempotent. Elsewhere it only migrates. |
| `evokai bootstrap FILE` | Applies an application YAML (and `EVOKAI_BOOTSTRAP_APPS`) once |
| `evokai keygen srv\|adm\|signing\|encryption` | Prints a new server key, admin key, token signing key or Fernet key |
| `evokai openapi [--out F]` | The OpenAPI schema with the voice routes (input of the TypeScript client) |
| `evokai connectors list\|check FILE\|serve FILE` | The ready connectors and their keys; check a connector's configuration and list its tools; or serve one as a standalone MCP server (`--port`, `--token-env`) |
| `evokai purge` | Deletes conversations past their application's time to live (the service also does it hourly) |
| `evokai selftest` | Builds the app, imports the voice stack if installed and loads and runs the Prompt Guard model when `EVOKAI_PROMPT_GUARD_MODEL` is set (used on read-only images) |


## Gates and images

```bash
scripts/ci.sh                    # ruff, ty, pytest (SQLite; Postgres too with EVOKAI_TEST_DATABASE_URL),
                                 # OpenAPI up to date, the client: lint, typecheck, test, build,
                                 # the UI components: lint, typecheck, test, build with the size budget,
                                 # then the console: typecheck, test, build
scripts/build-image.sh <registry/repo> <tag> [--flavour lite|full] [--cache-ref <ref>] [--with-voice]
                       [--with-prompt-guard <model folder>] [--no-push]
                                 # linux/arm64, smoke-tested read-only, pushed; prints the digest
```

### Images: lite, lite with voice, full

Evokai ships two flavours of one Dockerfile (`--build-arg IMAGE=lite|full`), and the voice stack ([server
speech](voice.md), WebRTC voice) with `--build-arg VOICE=1`. A release publishes three images:

| Image | Installs | Tag | Size (linux/amd64, uncompressed, measured 17 November 2026) |
|---|---|---|---|
| `lite` (default) | Evokai, the harness core with Code Mode, compaction and subagents, and every harness extra under 30 MB (`harness-lite` in pyproject.toml) | `<commit>` | 1.07 GB |
| `lite` with voice | the same and the `voice` extra | `<commit>-voice` | not measured |
| `full` | every harness extra that resolves (`harness`) and `voice` | `<commit>-full` | 2.79 GB without voice; not measured with it |

Voice stays off until `EVOKAI_VOICE_ENABLED=true`, whatever the image: `<version>` suits the browser's own speech,
`<version>-voice` server speech on the small image.

The lite image leaves out the extras over 30 MB each, measured as their site-packages beyond Evokai's base: pixeltable
(574 MB, with pyarrow, pandas and av), prompt-injection-defender-ml (186 MB, onnxruntime and numpy), playwright
(168 MB), boxlite (115 MB), smol (93 MB), temporal (52 MB), ipython (49 MB) and daytona (38 MB). Their capabilities
are listed there with `installed: false` and the reason «not in the lite image» (Permissions, Capabilities), and are
left out of a run like any capability that is not installed. Every other extra is in both (the integrations about
20 MB each, sharing their MCP client; e2b 27 MB; coder 27 MB; modal 26 MB; the rest smaller).

`scripts/build-image.sh --flavour full` tags `<tag>-full` (with voice), `--with-voice` on lite `<tag>-voice`; the smoke
test (read-only root, no network, no capabilities, uid 1001) runs on each, and `evokai selftest` prints the flavour and
the voice stack (`ok flavour=lite voice=yes ...`, its models loaded offline), which the build checks. The release
workflow (`.github/workflows/release.yml`) publishes all three.
`--no-push` builds and smoke-tests locally and prints the image's size. The capability worker image
(`workers/capability`) is separate from both ([capabilities](capabilities.md#separate-workers)).

### Recorded HTTP cassettes

Most tests use hand-written fakes (scripted models, a fake MCP server). `tests/test_recorded.py` adds real traffic,
recorded once with [vcrpy](https://vcrpy.readthedocs.io) (through pytest-recording) at the HTTP boundary: Bedrock
Runtime through botocore's urllib3, MCP through httpx. The tests drive Evokai through its HTTP API and assert on the
events a client receives, so they do not depend on how a turn runs inside. Cassettes live in `tests/cassettes/`.

- **Replay is the default.** A plain `pytest` (and `scripts/ci.sh`) runs with `--record-mode=none`: a request that is
  not in its cassette fails instead of reaching the network. A test whose cassette has not been recorded yet is
  skipped and says so.
- **Recording is opt-in**, with `scripts/record-cassettes.sh mcp` (against the reference MCP SDK's server on
  `127.0.0.1:8765`, no credentials) or `AWS_PROFILE=<profile> scripts/record-cassettes.sh bedrock` (a few calls to
  Claude Haiku 4.5 in `eu-north-1` with tiny prompts; the profile needs Bedrock access to that model). The script
  replays what it recorded, offline, and runs `gitleaks dir tests/cassettes` when gitleaks is installed.
- **No secrets in cassettes.** `Authorization`, cookies, every `x-amz-*` and `amz-sdk-*` header (signatures, dates,
  session tokens), API keys in query strings and the user agent are dropped before anything is written;
  `test_cassettes_carry_no_secrets` checks every cassette in the repository, and they must pass gitleaks before they
  are committed. Replaying signs with throw-away keys and ignores the local AWS configuration.
- **Bodies are not matched** (the system prompt carries the date): requests to the same model or server are answered
  in the order they were recorded. Re-record when an outgoing request changes in substance (a new tool, another model).
- **Seeds** (`tests/cassettes/seeds/`) hold what a provider cannot be made to send on demand, written by hand: the
  fallback test's throttling answer. Recording copies the seed into place and adds the real interactions after it.

| Cassette | What it covers | Recorded |
|---|---|---|
| `test_mcp_streamable_http_tools_list_and_call` | initialize, `tools/list` and `tools/call` over streamable HTTP (event-stream answers, session id) | against the reference SDK server |
| `test_bedrock_converse_stream_turn_with_a_tool_call` | a ConverseStream turn: a tool call, the MCP result, the answer | needs a Bedrock profile |
| `test_bedrock_fallback_after_a_throttled_primary` | the primary throttled (seed), the fallback answers | needs a Bedrock profile |

`test_a_cassette_records_and_replays_botocore_event_streams_and_httpx` needs no cassette: it records against local
stand-ins (a Bedrock endpoint speaking the AWS event stream, the fake MCP server over real HTTP) into a temporary
cassette and replays it with the stand-ins stopped.

### An image with Llama Prompt Guard 2

The default image has no local classifier. To bake one in (see [security](security.md#injection-classifiers) for the
license and what to do first):

```bash
HF_TOKEN=hf_... uv run scripts/export-prompt-guard.py \
  --model meta-llama/Llama-Prompt-Guard-2-86M --out models/prompt-guard     # or --model <your downloaded folder>
scripts/build-image.sh <registry/repo> <tag> --with-prompt-guard models/prompt-guard
# which is: docker buildx build --target runtime --build-arg PROMPT_GUARD=1 \
#             --build-context prompt-guard-model=models/prompt-guard .
```

The export runs once on a workstation (PyTorch and Transformers live only in that script's own environment) and
checks that ONNX Runtime and PyTorch agree on sample texts. The build installs the `prompt-guard` extra (ONNX Runtime,
`tokenizers`, NumPy, about 150 MB installed), copies the folder to `/opt/prompt-guard` and sets `EVOKAI_PROMPT_GUARD_MODEL`; the
smoke test loads and runs the model with no network. Nothing is downloaded during the build or at run time. The model
folder is about 1.1 GB (about 340 MB with `--int8`); keep it out of the repository and the build context, it travels
only through `--build-context`.

The image (with the console built in) runs as uid 1001 on a read-only root filesystem with a tmpfs `/tmp`, listens on 8787
and has a health check on `{EVOKAI_BASE_PATH}/health`. Released images are published to
`ghcr.io/giordyfish/evokai` by `.github/workflows/release.yml` (releasing).


## Supply chain

### SBOMs

`scripts/sbom.sh [--out DIR] [--image REF]` writes CycloneDX JSON bills of materials:

| File | Source | Tool |
|---|---|---|
| `python.cdx.json` | `uv.lock`: runtime dependencies and extras, no dev tools | `uv export --format cyclonedx1.5` |
| `client.cdx.json`, `console.cdx.json` | `package-lock.json` of the client and the console, production dependencies | `npm sbom` |
| `image.cdx.json` | the pushed image (OS packages, Python and npm packages inside it) | syft 1.55.0, pinned by checksum and downloaded when missing |

A pipeline runs it after the image is pushed (`--image <repo>@<digest>`) and keeps `sbom/` as the build's
artifacts (`evokai-sbom-<commit>`). The image SBOM only warns when syft cannot be fetched, unless `SBOM_STRICT=1`.

### Third-party licences

The SBOMs list every dependency with its licence. Two Python packages of the service are under the GNU Lesser General
Public License: `num2words` (LGPL-2.1, numbers in spoken text) and `psycopg` (LGPL-3.0, when installed with the
Postgres tooling). They are used unmodified, as separately installed packages that can be replaced with another
version of the same library, and their source code is on the Python Package Index and in their own repositories;
an image that ships them carries their licence files in their `dist-info` folders.

### Image signing

`scripts/sign-image.sh <repo>@sha256:<digest> [--sbom sbom/image.cdx.json]` signs the image with cosign (2.6.5,
pinned by checksum) and attests its SBOM (`--type cyclonedx`). It signs digests only, never tags, and does nothing
(exit 0) without credentials, so CI always calls it:

- **KMS key** (recommended on AWS): `COSIGN_KEY=awskms:///alias/evokai-cosign`. Create an asymmetric KMS key
  (`ECC_NIST_P256`, `SIGN_VERIFY`) in the deployment's region; the build role needs `kms:Sign`, `kms:GetPublicKey`
  and `kms:DescribeKey` on it, and pushing the signature needs the same ECR push permissions as the image. Any other
  cosign key reference works too (`gcpkms://`, `hashivault://`, a key file with `COSIGN_PASSWORD`).
- **Keyless** (Sigstore): with an OIDC identity (`SIGSTORE_ID_TOKEN`, or GitHub Actions' token): a short-lived Fulcio
  certificate for that identity, recorded in the public Rekor log. A CI without an OIDC identity for Fulcio uses a
  key instead.

Verify before deploying (an infrastructure pipeline can refuse unsigned images):

```bash
# KMS
cosign verify --key awskms:///alias/evokai-cosign "$IMAGE@sha256:<digest>"
cosign verify-attestation --key awskms:///alias/evokai-cosign --type cyclonedx "$IMAGE@sha256:<digest>"
# keyless (the release workflow of Giordyfish/evokai)
cosign verify "ghcr.io/giordyfish/evokai@sha256:<digest>" \
  --certificate-identity-regexp '^https://github.com/Giordyfish/evokai/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## HTTP hardening

Every response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, a `Referrer-Policy`,
`Cross-Origin-Opener-Policy: same-origin` and, for the API, `Content-Security-Policy: default-src 'none';
frame-ancestors 'none'` and `Cache-Control: no-store` (the console has its own strict CSP).
`Strict-Transport-Security` is added when the request arrived over HTTPS, directly or through a proxy that sets
`X-Forwarded-Proto: https`. The browser session API (`/v1/session/*`) answers CORS for the origins the applications
allow (never with credentials); bodies over 2 MB there (a recording to transcribe: `EVOKAI_TRANSCRIBE_MAX_BYTES`;
speech and tool results: 256 KB), or 8 MB elsewhere, get 413. See
security assessment.
