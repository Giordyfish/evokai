#!/usr/bin/env bash
# Build and push the Evokai image for linux/arm64 with BuildKit; prints the pushed digest last.
#
#   scripts/build-image.sh <registry/repo> <tag> [--flavour lite|full] [--cache-ref <ref>] [--with-voice]
#                          [--with-prompt-guard <model folder>] [--platform p] [--no-push]
#
# --flavour lite (the default) or full: the full image is tagged <tag>-full and always carries the voice stack, the
# lite one <tag>, or <tag>-voice with --with-voice (the tags a release publishes).
# --no-push builds, smoke-tests and loads the image locally, and prints its size instead of a digest.
# Example: scripts/build-image.sh ghcr.io/giordyfish/evokai "$SHA" \
#            --cache-ref ghcr.io/giordyfish/evokai-build-cache:evokai
# The caller is logged in to the registry. The image is smoke-tested before the push: read-only
# root, tmpfs /tmp, no network, no capabilities, uid 1001. --with-prompt-guard bakes the ONNX export of
# Llama Prompt Guard 2 (scripts/export-prompt-guard.py) into the image; the smoke test loads and runs it.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ $# -ge 2 ]] || { sed -n 2,12p "$0" >&2; exit 2; }
repo="$1"; tag="$2"; shift 2
cache_ref=""; voice=0; platform="linux/arm64"; prompt_guard=""; flavour="lite"; push=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cache-ref) cache_ref="$2"; shift 2 ;;
    --with-voice) voice=1; shift ;;
    --with-prompt-guard) prompt_guard="$(cd "$2" && pwd)"; shift 2 ;;
    --platform) platform="$2"; shift 2 ;;
    --flavour) flavour="$2"; shift 2 ;;
    --no-push) push=0; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
case "$flavour" in
  lite) if [[ "$voice" = 1 ]]; then image="$repo:$tag-voice"; else image="$repo:$tag"; fi ;;
  full) image="$repo:$tag-full"; voice=1 ;;
  *) echo "--flavour is lite or full" >&2; exit 2 ;;
esac
has_voice=$([[ "$voice" = 1 ]] && echo yes || echo no)
builder="${EVOKAI_BUILDER:-evokai}"
docker buildx inspect "$builder" >/dev/null 2>&1 \
  || docker buildx create --name "$builder" --driver docker-container --bootstrap >/dev/null
cache=()
if [[ -n "$cache_ref" ]]; then
  cache=(--cache-from "type=registry,ref=$cache_ref"
         --cache-to "type=registry,ref=$cache_ref,mode=max,image-manifest=true,oci-mediatypes=true")
fi
revision="$(git rev-parse HEAD 2>/dev/null || echo "$tag")"
guard=()
if [[ -n "$prompt_guard" ]]; then
  [[ -f "$prompt_guard/model.onnx" && -f "$prompt_guard/tokenizer.json" ]] \
    || { echo "$prompt_guard: no model.onnx and tokenizer.json (scripts/export-prompt-guard.py)" >&2; exit 2; }
  guard=(--build-arg PROMPT_GUARD=1 --build-context "prompt-guard-model=$prompt_guard")
fi
docker buildx build --builder "$builder" --platform "$platform" "${cache[@]}" \
  --build-arg VOICE="$voice" --build-arg IMAGE="$flavour" --build-arg COMMIT="${revision:0:12}" "${guard[@]}" \
  --label org.opencontainers.image.revision="$revision" \
  --target runtime -t "$image" --load . >&2
# The image must start as on the host: read-only, no network, unprivileged, and be the flavour asked, with the voice
# stack (its models loaded offline) exactly when it was asked for.
docker run --rm --platform "$platform" --read-only --tmpfs /tmp --network none --cap-drop ALL \
  --user 1001:1001 "$image" selftest | tee /dev/stderr | grep -q "flavour=$flavour voice=$has_voice "
docker run --rm --platform "$platform" --read-only --network none --entrypoint python "$image" \
  -c "import pathlib,sys; sys.exit(not pathlib.Path('/app/admin/dist/index.html').is_file())" >&2
if [[ "$push" = 0 ]]; then
  docker image inspect "$image" --format '{{.Size}}'
  exit 0
fi
docker push "$image" >&2
docker buildx imagetools inspect "$image" --format '{{json .Manifest}}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["digest"])'
