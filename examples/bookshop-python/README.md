# Inkwell Books: a shop page with its assistant in Python (FastAPI)

A Python web app for Inkwell Books, the fictional bookshop of [`config/bookshop.yaml`](../../config/bookshop.yaml),
wired to Evokai the way a real application is:

- the **backend** (`server.py`, FastAPI) keeps the server key and mints a short session token for the
  signed-in user with [`evokai-client`](../../clients/python/README.md);
- the **page** (reused from `../bookshop-app/public/index.html`) lists the catalogue and drops in the `<evokai-chat>` panel of
  [`@evokai/evokai-ui`](../../clients/ui/README.md), which talks to Evokai directly with that token;
- the backend is also a tiny **MCP server** (`POST /mcp`, one read-only tool, `catalogue_search`), so the agent reads
  the shop's data through the shop, with a secret Evokai sends on every call.

```text
browser ──(session token)──▶ Evokai ──(MCP, secret)──▶ this app's /mcp
   │                            ▲
   └──▶ this app's /api/agent/token ──(server key)──┘
```

| File | What it is |
| --- | --- |
| `server.py` | The page, the token and history routes, the MCP server (FastAPI) |
| `setup.py` | One-time setup: the application, its MCP server and the agent, then `.env` |
| `catalogue.py` | The books and search function, shared by the page and the tool |
| `requirements.txt` | Minimal pinned dependencies |

## Run it

You need Python 3.11 or later and the [quick start](../../docs/quickstart.md) running (Docker, the test model, no
cloud account).

**1. Start Evokai** at the repository root and note the operator key it prints:

```bash
docker compose up -d --build
docker compose logs evokai          # Operator key:  evk_op_...
```

**2. Build the clients.** The example uses the packages of this repository (`file:` references), so it works with
the code you have checked out:

```bash
(cd clients/typescript && npm ci && npm run build)
(cd clients/ui && npm ci && npm run build)
```

**3. Set up and start the app:**

With `pip`:

```bash
cd examples/bookshop-python
pip install -r requirements.txt
EVOKAI_OPERATOR_KEY=evk_op_... python setup.py
python server.py                           # http://localhost:3000
```

Or with `uv`:

```bash
cd examples/bookshop-python
EVOKAI_OPERATOR_KEY=evk_op_... uv run setup.py
uv run server.py
```

`setup.py` creates the application `bookshop` (with `http://localhost:3000` among its allowed origins), registers
this app's MCP server with a new random secret, creates the agent `bookshop` and writes `.env` with the server key and
that secret. The admin key is not kept; the console takes the operator key.

Open `http://localhost:3000`, click the orb at the bottom right and ask something. With the test model the agent
answers with a fixed text, which shows the whole path works: page, token, session, stream.

## With a real model

The test model calls no tools. To see the agent search the catalogue, give it a model that does, for example one on
this machine through Ollama:

```bash
OLLAMA_BASE_URL=http://host.docker.internal:11434/v1 docker compose up -d     # at the repository root
rm .env && EVOKAI_APP_SLUG=bookshop-local EVOKAI_MODEL=ollama:qwen3:8b EVOKAI_OPERATOR_KEY=evk_op_... python setup.py
python server.py
```

Ask «Which poetry books do you have in stock?»: the panel shows the `catalogue_search` call, then the answer. Any
model of [docs/quickstart.md](../../docs/quickstart.md#models) works the same way, and the console can change the
agent's model later.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `EVOKAI_URL` | `http://localhost:8787/agent` | Where Evokai is mounted |
| `EVOKAI_SERVER_KEY` | from `.env` | The application's server key (never sent to the browser) |
| `EVOKAI_AGENT` | `bookshop` | The agent the tokens are minted for |
| `BOOKSHOP_MCP_SECRET` | from `.env` | What Evokai must send to `/mcp`; without it `/mcp` refuses every call |
| `PORT` | `3000` | The app's port (also the allowed origin and the MCP address `setup` registers) |
| `EVOKAI_OPERATOR_KEY` | | Setup only: creates the application |
| `EVOKAI_MODEL` | `test:canned` | Setup only: the agent's model |
| `BOOKSHOP_MCP_URL` | `http://host.docker.internal:3000/mcp` | Setup only: where Evokai reaches `/mcp` (the host, seen from the quick start's container) |
| `EVOKAI_APP_SLUG` | `bookshop` | Setup only: the application's slug |

The variables win over `.env`. The console's setup wizard ([quick start, step 2](../../docs/quickstart.md#2-set-up-your-first-application))
does what `setup.py` does by hand: put its server key in `EVOKAI_SERVER_KEY` and its agent's name in `EVOKAI_AGENT`.
