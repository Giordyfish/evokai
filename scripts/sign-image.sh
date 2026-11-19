#!/usr/bin/env bash
# Sign a pushed image with cosign, and attest its SBOM when one is given. Without credentials it does nothing and
# exits 0, so CI can always call it.
#
#   scripts/sign-image.sh <registry/repo@sha256:digest> [--sbom sbom/image.cdx.json]
#
# Credentials, the first one found:
#   COSIGN_KEY=awskms:///alias/evokai-cosign   a KMS key (AWS credentials from the default chain), or any cosign key
#                                              reference (a file with COSIGN_PASSWORD, gcpkms://, hashivault://)
#   SIGSTORE_ID_TOKEN, or a CI with an OIDC identity (GitHub Actions: ACTIONS_ID_TOKEN_REQUEST_URL)
#                                              keyless: a Fulcio certificate, recorded in the Rekor log
# Always sign a digest, never a tag. Verify with the commands in docs/deployment.md.
set -euo pipefail

ref="${1:-}"
sbom=""
if [[ "${2:-}" == "--sbom" ]]; then sbom="${3:-}"; fi
if [[ -z "$ref" || "$ref" != *@sha256:* ]]; then
  echo "usage: $0 <registry/repo@sha256:digest> [--sbom FILE]" >&2
  exit 2
fi

mode=""
if [[ -n "${COSIGN_KEY:-}" ]]; then
  mode=key
elif [[ -n "${SIGSTORE_ID_TOKEN:-}" || -n "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" ]]; then
  mode=keyless
fi
if [[ -z "$mode" ]]; then
  echo "sign-image: no signing credentials (COSIGN_KEY or an OIDC identity): skipped"
  exit 0
fi

COSIGN_VERSION=2.6.5
declare -A COSIGN_SHA256=(
  [amd64]=c3b4f5410e608af03a5eb0aaac84a4313d8da131248e08ff1759ac70c79d1644
  [arm64]=426193b4c5da4d4d643e822f48fe0cc8a476ca1782a272704831f5a0cef716d7
)
cosign=$(command -v cosign || true)
if [[ -z "$cosign" ]]; then
  arch=$(uname -m)
  case "$arch" in x86_64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; esac
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/cosign" "https://github.com/sigstore/cosign/releases/download/v${COSIGN_VERSION}/cosign-linux-${arch}"
  echo "${COSIGN_SHA256[$arch]}  $tmp/cosign" | sha256sum -c - >/dev/null
  chmod +x "$tmp/cosign"
  cosign="$tmp/cosign"
fi

args=(--yes)
if [[ "$mode" == key ]]; then args+=(--key "$COSIGN_KEY"); fi
annotations=(-a "org.opencontainers.image.source=https://github.com/Giordyfish/evokai")
if [[ -n "${REVISION:-}" ]]; then annotations+=(-a "org.opencontainers.image.revision=$REVISION"); fi

echo "== sign: $ref ($mode)"
"$cosign" sign "${args[@]}" "${annotations[@]}" "$ref"
if [[ -n "$sbom" && -f "$sbom" ]]; then
  echo "== attest: $sbom"
  "$cosign" attest "${args[@]}" --type cyclonedx --predicate "$sbom" "$ref"
fi
