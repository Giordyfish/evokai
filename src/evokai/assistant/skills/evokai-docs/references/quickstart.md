# Quick start: Evokai in five minutes

You need Docker with Compose v2. Nothing else: no YAML to write, no keys to generate, no cloud account (a test
model answers without one, and a model on your own machine works through Ollama).

## 1. Start it

```bash
git clone https://github.com/Giordyfish/evokai && cd evokai
docker compose up -d --build        # the first build takes a few minutes, once
docker compose logs evokai          # the console address and the operator key
```

The first start prints, once:

```text
========================================================================
  Evokai is ready.

  Console:       http://localhost:8787/agent/admin/
  Operator key:  evk_op_...

  This key is shown only now. Keep it in a password manager: it signs you in to
  the console and creates applications. Lost it? Run
  `evokai init --rotate-operator-key` in the service's container.
========================================================================
```

What runs:

| Service | What it is |
| --- | --- |
| `postgres` | Postgres 17 with its data in the `evokai-pg` volume. Not published on the host. |
| `evokai` | The service and its console on port 8787, read-only root, unprivileged, with a health check. It migrates the database at start. |

The token signing key, the encryption key and the operator key are generated on the first start into the
`evokai-data` volume (`/data/keys.json`, readable by the service only) and reused afterwards. `docker compose down`
keeps both volumes; `docker compose down -v` deletes everything, keys included.

No cache is needed (Evokai keeps its state in Postgres), so there is no Valkey or Redis.

## 2. Set up your first application

Open `http://localhost:8787/agent/admin/` and enter the operator key. With no application yet, the console opens the
setup:

1. **Application**: a name (the slug follows) and the web addresses whose pages will talk to the agents.
2. **Agent**: a title, a model from the list the deployment allows (models whose credentials are missing are marked),
   and its instructions.
3. **Tools** (optional): the URL of your application's MCP server, how the agent signs in to it (the user's own token,
   a service token, or nothing) and **Test connection**, which lists the tools it found. Tools that change data always
   ask the user to confirm.
4. **Skills** (optional): one or more `SKILL.md` files (front matter `name` and `description`, then the text).
5. **Create**: the application, the MCP server, the skills and the agent, then its **server key** and **admin key**,
   shown once, with a `curl` to try.

**Talk to it in the playground** opens a real session with the new agent.

## 3. Connect your application

Your backend keeps the server key and mints a short session token for each signed-in user; the browser talks to Evokai
with it:

```bash
curl -s -X POST http://localhost:8787/agent/v1/session-tokens \
  -H "Authorization: Bearer $EVOKAI_SERVER_KEY" -H "Content-Type: application/json" \
  -d '{"subject": "user-1", "agent": "assistant"}'
```

In the browser, use [`@evokai/evokai-client`](../clients/typescript/README.md) to build your own interface, or the
ready-made panel and voice stage of [`@evokai/evokai-ui`](../clients/ui/README.md).

## Models

Only what the deployment allows is offered (`EVOKAI_MODEL_ALLOW`; the quick start allows Bedrock EU, OpenAI, Mistral,
Ollama and the test model). Pass a provider's credentials when you start:

| Provider | How |
| --- | --- |
| Test model | Nothing: `test:canned` answers with a fixed text and calls no tools. Good to try the flow. |
| Ollama (on this machine) | `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1 docker compose up -d`. Ollama must listen on an address the container reaches: start it with `OLLAMA_HOST=0.0.0.0` (and allow the Docker bridge in a host firewall). The console lists the installed models. Local models count as free against the caps. |
| OpenAI | `OPENAI_API_KEY=sk-... docker compose up -d` |
| Mistral | `MISTRAL_API_KEY=... docker compose up -d` |
| Bedrock | `eval "$(aws configure export-credentials --profile <profile> --format env)"` before `docker compose up -d`; `EVOKAI_AWS_REGION` sets the Region (default `eu-north-1`). |

Put the variables in a `.env` file next to `docker-compose.yml` to keep them across restarts.

## Settings you may change

| Variable | Default | Meaning |
| --- | --- | --- |
| `EVOKAI_HOST_PORT` | `8787` | The port on your machine |
| `EVOKAI_PUBLIC_URL` | `http://localhost:8787/agent` | Where people reach the API (printed in the first-start message) |
| `EVOKAI_POSTGRES_PASSWORD` | `evokai-local` | The database password (the database is not published on the host) |
| `EVOKAI_MODEL_ALLOW` | Bedrock EU, OpenAI, Mistral, Ollama, test | JSON list of `provider:model` patterns |
| `EVOKAI_IMAGE` | `evokai:local` | The image to run (a published one instead of building) |

## Troubleshooting

- **Port 8787 is taken**: `EVOKAI_HOST_PORT=8788 EVOKAI_PUBLIC_URL=http://localhost:8788/agent docker compose up -d`.
- **The operator key scrolled away**: `docker compose exec evokai evokai init --rotate-operator-key` prints a new one
  (the old one stops working when the service restarts: `docker compose restart evokai`).
- **A model answers "not allowed"**: add its provider to `EVOKAI_MODEL_ALLOW`.
- **The service restarts with `password authentication failed for user "evokai"`**: an `evokai-pg` volume from an
  earlier run with a different password exists. `docker compose down -v` deletes it (and its data), or set
  `EVOKAI_POSTGRES_PASSWORD` to the old password.
- **Health**: `docker compose ps` shows both services healthy; the console's Health page shows the database, models
  and MCP servers.

## From here

- [Configuration](configuration.md): the same application as a YAML file for infrastructure as code.
- [Deployment](deployment.md): production images, read-only containers, a separate database role.
- [Connectors](connectors/README.md): ready MCP servers for SQL, REST APIs, Google Drive, SharePoint and mailboxes.
- [Evals](evals.md): test cases per agent, run on every new version.
