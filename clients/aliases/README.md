# Aliases

Two small packages that keep the plain name `evokai` pointing at the project. They contain no code of their own and
are published by the release workflow with every version:

- `evokai` on PyPI installs the Python client, [`evokai-client`](../python);
- `evokai` on npm re-exports [`@evokai/evokai-client`](../typescript) (`evokai/server`, `evokai/browser`,
  `evokai/react`).

Their versions and their dependency on the client always equal the release version.
