# Releasing

A release is a tag `vX.Y.Z` on `main`. `.github/workflows/release.yml` then runs every gate (the CI workflow) and
publishes:

| What | Where | How |
|---|---|---|
| The service image, `lite` and `full`, linux/amd64 and linux/arm64 | `ghcr.io/giordyfish/evokai:X.Y.Z` (and `latest`), `ghcr.io/giordyfish/evokai:X.Y.Z-full` | `GITHUB_TOKEN`, signed keyless with cosign (`scripts/sign-image.sh`), with build provenance and an SBOM attestation |
| The Python client `evokai-client` | [PyPI](https://pypi.org/project/evokai-client/) | trusted publishing (OpenID Connect): no token in the repository |
| `@evokai/evokai-client` and `@evokai/evokai-ui` | [npm](https://www.npmjs.com/org/evokai) | `npm publish --provenance` with the `NPM_TOKEN` secret |
| The aliases `evokai` on PyPI (installs `evokai-client`) and `evokai` on npm (re-exports `@evokai/evokai-client`) | PyPI and npm | as above ([clients/aliases](../clients/aliases)) |

The service itself is distributed as the image, not on PyPI: its migrations (`migrations/`, `alembic.ini`) and the
built console (`admin/dist`) live outside the Python package, so a wheel of it would not run on its own. The capability
worker (`workers/capability`) is built from its own Dockerfile and is not published by this workflow.

A tag with a suffix (`v0.2.0-rc.1`) is a pre-release: the image gets no `latest` tag and npm gets the `next`
dist-tag.

## Cutting a release

1. Set the same version everywhere: `pyproject.toml`, `clients/python/pyproject.toml`,
   `workers/capability/pyproject.toml`, `clients/typescript/package.json`, `clients/ui/package.json` (its version and
   its peer dependency on `@evokai/evokai-client`), `admin/package.json`, and the aliases in `clients/aliases` (their
   versions and their dependency on the client). Refresh the lockfiles (`uv lock`,
   `uv lock` in `clients/python` and `workers/capability`, `npm install --package-lock-only` in the three npm
   folders). The workflow's first job refuses a tag that does not match.
2. Move the entries under **Unreleased** in [CHANGELOG.md](../CHANGELOG.md) to a new section with the version and
   the date.
3. Run `scripts/ci.sh`, commit, and push to `main`.
4. Tag and push the tag: `git tag -s vX.Y.Z -m "Evokai X.Y.Z" && git push origin vX.Y.Z`.
5. When the workflow is green, create the GitHub release from the tag with the changelog section as its notes.

## One-time setup

### GitHub repository

- **Settings, Actions, General, Workflow permissions**: read-only `GITHUB_TOKEN` is enough; the jobs ask for what
  they need (`packages: write`, `id-token: write`).
- **Settings, Environments**: create `pypi` and `npm`. Restrict both to tags matching `v*` (deployment branches and
  tags), and add yourself as a required reviewer if every publish should wait for a click.
- **Settings, Code security**: enable private vulnerability reporting (it is how [SECURITY.md](../SECURITY.md) and
  the code of conduct receive reports), Dependabot alerts and secret scanning.
- **Branch protection** on `main`: require the CI checks and, if you want it enforced, the DCO sign-off (for example
  with the DCO GitHub App).
- Consider pinning the actions in both workflows to commit SHAs (Dependabot keeps them current).

### GitHub Container Registry

The first push creates the package `ghcr.io/giordyfish/evokai`, private by default. Then, in the package's settings:

- **Change visibility** to public, so the image can be pulled without a login;
- **Manage Actions access**: make sure the `Giordyfish/evokai` repository has the **Write** role (it has it when the
  package was created by its workflow, which links them).

Verify a released image with the keyless command in [deployment](deployment.md#image-signing).

### PyPI (trusted publishing)

No API token is stored anywhere. On PyPI, signed in as the owner:

1. **Your account, Publishing, Add a new pending publisher** (the project does not exist before the first release).
2. PyPI project name `evokai-client`; owner `Giordyfish`; repository `evokai`; workflow name `release.yml`;
   environment `pypi`.
3. Add a second pending publisher, identical, for the PyPI project name `evokai` (the alias).
4. The first tag creates both projects. Afterwards each publisher shows under its project's **Publishing** settings.

Optionally do the same on TestPyPI and try a pre-release tag there first (change the publish step's
`repository-url` for that run).

### npm

1. Create the **`evokai` organisation** on npmjs.com (free for public packages), so the `@evokai` scope belongs to
   you, and enable two-factor authentication on your account.
2. Create a **granular access token**: read and write on all packages (the first release creates the unscoped
   `evokai`, which no scope covers; narrow it to `@evokai` and `evokai` afterwards), an expiry you are
   comfortable rotating, and, so that CI can publish, allow it to bypass two-factor authentication for publishing.
3. Store it as the secret `NPM_TOKEN` of the `npm` environment (not as a repository-wide secret).
4. The packages carry `publishConfig.access: public`; `--provenance` links each published version to the workflow
   run that built it (shown on its npm page).

npm also supports trusted publishing over OpenID Connect (no token, npm 11.5.1 or newer): once the packages exist,
configure `Giordyfish/evokai`, `release.yml` and the `npm` environment as their trusted publisher on npmjs.com, drop
`NODE_AUTH_TOKEN` from the workflow, and delete the token.
