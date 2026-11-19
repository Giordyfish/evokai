# Contributing to Evokai

Thank you for wanting to help. Evokai is experimental: interfaces still move, so an issue that describes what you want
to do before a large change saves everyone time.

## Working with AI, or without

This repository is developed with heavy use of AI agents. Work however you like, by hand or with agents: nobody is
asked to use AI, and nobody is asked to avoid it. Complaining that the project is built with AI is not a useful
contribution, though.

Every contribution, human or agentic, is welcome as long as it is reasoned, structured and well described. You are
responsible for what you submit: read it, understand it, run the gates, and be ready to explain every line. Pull
requests that look generated and unreviewed (vague descriptions, unrelated changes, invented APIs, tests that do not
test anything) are closed without a detailed review.

Agents working in this repository follow [AGENTS.md](AGENTS.md); its rules apply to people too.

## The gates

Everything CI runs is in `scripts/ci.sh`, and it runs the same on your machine. You need
[uv](https://docs.astral.sh/uv/) (Evokai supports Python 3.12 or newer; uv installs 3.14 from `.python-version`, and
`UV_PYTHON=3.12` runs the gates on the oldest supported version, as CI does) and Node.js 22 with npm.

```bash
scripts/ci.sh            # everything: repo checks, service, TypeScript client, Python client, UI, console, worker
scripts/ci.sh service    # repo checks, then ruff, ty, pytest (SQLite) and the OpenAPI check
scripts/ci.sh client     # the TypeScript client: lint, typecheck, test, build
scripts/ci.sh python     # the Python client: ruff, ty, pytest, build
scripts/ci.sh ui         # the UI components: lint, typecheck, test, build with the size budget
scripts/ci.sh console    # the admin console: typecheck, test (i18n checks included), build
scripts/ci.sh worker     # the capability worker
EVOKAI_TEST_DATABASE_URL=postgresql://... scripts/ci.sh service   # the suite on Postgres too
```

The repo checks refuse the em dash character (U+2014) anywhere and scan the tree for secrets with
[gitleaks](https://github.com/gitleaks/gitleaks) (installed if missing). A pull request that changes the HTTP API
regenerates the schema and the client's types:

```bash
uv run evokai openapi --out clients/typescript/openapi.json
(cd clients/typescript && npm run gen)
```

Tests never reach the network: models, MCP servers and speech are faked, and real HTTP traffic is replayed from
cassettes ([recorded HTTP cassettes](docs/deployment.md#recorded-http-cassettes)).

## What a good pull request looks like

- **One problem.** Say what is wrong or missing, for whom, and how you noticed. Link the issue if there is one.
- **The approach.** Why this way, what you considered and left out, and what it changes for existing deployments
  (configuration, API, stored data, defaults). A behaviour change is said in the description, never only in the code.
- **How it was tested.** The gates you ran and their result, the tests you added, and anything you checked by hand
  (with the commands). A bug fix comes with a test that fails without it.
- **Small and focused.** No unrelated formatting or refactoring in the same change.
- **Docs and texts in the same change.** Documentation under `docs/` follows the code. Every text the admin console
  shows is written in English and Italian (`admin/src/i18n/en.ts` and `it.ts`), as [AGENTS.md](AGENTS.md) explains.
- **English** for code, comments, docs and commit messages.

## Sign your commits (DCO)

Evokai uses the [Developer Certificate of Origin](https://developercertificate.org/) instead of a contributor license
agreement. By signing off a commit you certify that you wrote it, or otherwise have the right to submit it, under the
project's license (Apache-2.0). Add the sign-off with `-s`:

```bash
git commit -s -m "Explain what the commit changes"
```

It adds a `Signed-off-by: Your Name <you@example.com>` line with the name and e-mail of your Git configuration. Every
commit of a pull request needs one; `git rebase --signoff main` adds it to commits you already made.

## Security issues

Never open a public issue for a vulnerability: see [SECURITY.md](SECURITY.md).

## Conduct

Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md).
