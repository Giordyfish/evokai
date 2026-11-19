#!/usr/bin/env bash
# Software bills of materials (CycloneDX JSON) for the Python service, the two npm packages and, when an image is
# given, the container image. CI keeps them as build artifacts.
#
#   scripts/sbom.sh [--out DIR] [--image REF]
#
#   --out DIR    where to write (default sbom/)
#   --image REF  also the image (e.g. registry/repo@sha256:...), with syft (pinned, checksum verified, downloaded
#                when missing); a failure there only warns unless SBOM_STRICT=1
#
# Files: python.cdx.json (uv.lock, runtime dependencies and extras, no dev tools), client.cdx.json and
# console.cdx.json (package-lock.json, production dependencies), image.cdx.json.
set -euo pipefail
cd "$(dirname "$0")/.."

out=sbom
image=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) out="$2"; shift 2 ;;
    --image) image="$2"; shift 2 ;;
    *) echo "usage: $0 [--out DIR] [--image REF]" >&2; exit 2 ;;
  esac
done
mkdir -p "$out"

SYFT_VERSION=1.55.0
declare -A SYFT_SHA256=(
  [amd64]=bb65249542db5fedeeb4d3f9b018b75e7264dbfbbb10486f044581a92f90490d
  [arm64]=929bd163db2d7cd2255d3bcb8967208079813d7b46b0c952f7b794d8e2c7d304
)

echo "== sbom: python (uv.lock)"
uv export --frozen --format cyclonedx1.5 --all-extras --no-dev --no-emit-project -o "$out/python.cdx.json" >/dev/null

for pkg in clients/typescript:client admin:console; do
  dir="${pkg%%:*}"
  name="${pkg##*:}"
  echo "== sbom: npm ($dir)"
  (cd "$dir" && ${NPM:-npm} sbom --sbom-format cyclonedx --omit dev --package-lock-only) >"$out/$name.cdx.json"
done

image_sbom() {
  syft=$(command -v syft || true)
  if [[ -z "$syft" ]]; then
    arch=$(uname -m)
    case "$arch" in x86_64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; esac
    tmp=$(mktemp -d)
    tarball="syft_${SYFT_VERSION}_linux_${arch}.tar.gz"
    curl -fsSL -o "$tmp/$tarball" "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/$tarball"
    echo "${SYFT_SHA256[$arch]}  $tmp/$tarball" | sha256sum -c - >/dev/null
    tar -xzf "$tmp/$tarball" -C "$tmp" syft
    syft="$tmp/syft"
  fi
  echo "== sbom: image ($image)"
  "$syft" scan "registry:$image" -o "cyclonedx-json=$out/image.cdx.json" -q
}

if [[ -n "$image" ]]; then
  # The image's SBOM needs syft from GitHub: a failed download warns instead of failing a release, unless
  # SBOM_STRICT=1.
  if ! image_sbom; then
    echo "sbom: the image SBOM could not be made" >&2
    [[ "${SBOM_STRICT:-0}" == 1 ]] && exit 1
  fi
fi

ls -1 "$out"
