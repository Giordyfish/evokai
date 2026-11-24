# Releasing

Work lands on `develop`; `main` holds what is released and changes only through a pull request from `develop` (or a
`hotfix/...` branch). A release is a tag `vX.Y.Z` on `main`. `.github/workflows/release.yml` then makes sure every
gate passed on that commit (it waits for the CI run of the push to `main` and reuses it; without one, it runs the CI
workflow itself) and publishes:

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
3. Run `scripts/ci.sh`, commit to `develop`, then open a pull request from `develop` to `main` and merge it when CI
   is green.
4. Tag and push the tag: `git tag -s vX.Y.Z -m "Evokai X.Y.Z" && git push origin vX.Y.Z`.
5. When the workflow is green, create the GitHub release from the tag with the changelog section as its notes.

## One-time setup

### GitHub repository

- **Settings, Actions, General, Workflow permissions**: read-only `GITHUB_TOKEN` is enough; the jobs ask for what
  they need (`packages: write`, `id-token: write`).
- **Settings, Environments**: create `pypi`, `pypi-alias` and `npm`. Restrict all three to tags matching `v*`
  (deployment branches and tags), and add yourself as a required reviewer if every publish should wait for a click.
- **Settings, Code security**: enable private vulnerability reporting (it is how [SECURITY.md](../SECURITY.md) and
  the code of conduct receive reports), Dependabot alerts and secret scanning.
- **Rulesets**: `main` takes changes only through a pull request with the CI checks green (no direct push, no force
  push, no deletion; the check **Pull request source** refuses any branch other than `develop` or `hotfix/...`);
  `develop` takes pull requests with the CI checks green, and the maintainer may push to it directly. For the DCO
  sign-off to be enforced too, add the DCO GitHub App.
- The actions in both workflows are pinned to commit SHAs; Dependabot (`.github/dependabot.yml`) keeps them current,
  and the CI job **Workflow security** runs zizmor on every change.

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
3. Add a second pending publisher for the PyPI project name `evokai` (the alias), the same except the environment:
   `pypi-alias` (PyPI refuses two pending publishers with the same repository, workflow and environment).
4. The first tag creates both projects. Afterwards each publisher shows under its project's **Publishing** settings.

Optionally do the same on TestPyPI and try a pre-release tag there first (change the publish step's
`repository-url` for that run).

### npm (trusted publishing)

No npm token is stored anywhere: the release job proves its identity to npm over OpenID Connect (npm 11.5.1 or
newer) and gets a short-lived token for the publish.

1. Create the **`evokai` organisation** on npmjs.com (free for public packages), so the `@evokai` scope belongs to
   you, and enable two-factor authentication on your account.
2. A package must exist before it can name a trusted publisher, so the very first release of a new package goes out
   with a granular access token (read and write on all packages, allowed to bypass two-factor authentication for
   publishing), stored as the secret `NPM_TOKEN` of the `npm` environment and passed as `NODE_AUTH_TOKEN` to the
   publish step.
3. Then, on npmjs.com, for each package (`@evokai/evokai-client`, `@evokai/evokai-ui`, `evokai`): **Settings,
   Trusted publishing, GitHub Actions**, with organisation or user `Giordyfish`, repository `evokai`, workflow
   `release.yml` and environment `npm`. Under *Allowed actions* allow **publish** (the workflow publishes directly)
   and leave **dist-tag** off. Optionally set **Publishing access** to require two-factor authentication and
   disallow tokens. (Allowing only staging is stricter: the workflow would then run `npm stage publish` and every
   version would wait for a maintainer's approval with two-factor authentication.)
4. Delete the token on npmjs.com and the `NPM_TOKEN` secret on GitHub.

The packages carry `publishConfig.access: public`; `--provenance` links each published version to the workflow run
that built it (shown on its npm page).
