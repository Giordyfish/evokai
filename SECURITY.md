# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately, through GitHub's private vulnerability reporting:

1. Open <https://github.com/Giordyfish/evokai/security/advisories/new> (or the repository's **Security** tab, then
   **Report a vulnerability**).
2. Describe the issue, the affected version or commit, how to reproduce it, and its impact as you understand it.

Do not open a public issue, pull request or discussion for a vulnerability until a fix is published.

What to expect:

- an acknowledgement within a few days;
- an assessment, and a plan or a question, as soon as the issue is understood;
- a fix and a published advisory crediting you, unless you prefer to stay anonymous.

Evokai is maintained by one person in their spare time, so these are intentions, not guarantees.

## Supported versions

Evokai is experimental (0.x). Security fixes go into the latest release only.

## Scope

In scope: the service (`src/evokai`), its container image, the admin console, the clients under `clients/` and the
capability worker. The security layer's own limits (heuristics that a new phrasing can evade, for example) are
documented in [docs/security.md](docs/security.md#limits); a bypass is still worth reporting.

Out of scope: vulnerabilities in third-party dependencies with no impact on Evokai (report them upstream), and
deployments that ignore the documented hardening, such as an admin console exposed to the internet.
