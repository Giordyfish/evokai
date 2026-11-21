# Evokai

<p class="evokai-hero"><img src="assets/evokai-mascot.png" alt="Evokai, a small mage in a big violet hat" width="168" /></p>

**Summon AI agents into any app, with the governance a company needs.** Any model, from the big clouds to your own
servers; your app's tools over MCP, skills and voice; always acting as the signed-in user, within budgets, with an
audit trail.

Every product ends up wanting an assistant that can *do things*: read the data on screen, run the app's own actions,
answer by voice, and never step outside what the user is allowed to do. Evokai is that layer, written once. An
application connects with a key, describes its agents in a YAML file (or in the admin console), and its users talk to
them through a small headless client. The application keeps its identity, its data and its interface; Evokai brings
the agents.

> **Experimental.** Evokai is early-stage. Interfaces (the HTTP API, the configuration, the clients) may change
> between 0.x releases, and a release may need a manual migration step; read the [changelog](../CHANGELOG.md) before
> you upgrade.

## Start here

| | |
| --- | --- |
| [Quick start](quickstart.md) | Evokai, its console and its own Postgres with Docker Compose, in five minutes |
| [Features](features.md) | Everything Evokai does, in one table |
| [Architecture](architecture.md) | Concepts, the protocol, voice, limits, storage and the hard rules |
| [Configuration](configuration.md) | Environment variables, application YAML, bootstrap, keys, time to live |
| [Deployment](deployment.md) | Running it, Docker, database setup, CI gates, images, SBOMs and signing |
| [Security](security.md) | Threat model and the security layer's controls, mapped to the OWASP LLM Top 10 |
| [Data protection](data-protection.md) | Residency, jurisdiction and retention rules for every processor |
| [Connectors](connectors/README.md) | Ready MCP servers: SQL, REST/OpenAPI, Google Drive, SharePoint and OneDrive, IMAP |

## In your app

Your backend mints a short session token for the signed-in user with its server key, and the page talks to the agent
with it, through [`@evokai/evokai-client`](../clients/typescript/README.md) (headless) or the ready-made panel and
voice stage of [`@evokai/evokai-ui`](../clients/ui/README.md). Python backends use the
[`evokai-client`](../clients/python/README.md) package. A complete app is in
[examples/bookshop-app](../examples/bookshop-app).

The source, the issues and the releases are on [GitHub](https://github.com/Giordyfish/evokai). Evokai is licensed
under the [Apache License 2.0](../LICENSE); the name, its logo and its mascot are not covered by the license (see
[TRADEMARKS.md](../TRADEMARKS.md)).
