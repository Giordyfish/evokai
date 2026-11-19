#!/bin/sh
# Secrets in what is being released: every file of the checked-out commit, as `git archive HEAD` gives it.
#
# The tree, not the history: a CI clone's depth is not fixed (one commit, or a merge with the commits
# it brings), and a history cut short shows its oldest commits as every file added - reviewed findings came
# back as new ones and stopped the deploy. The whole history can be scanned by hand with --history.
# Reviewed false positives: `gitleaks:allow` on the line (fake keys in tests and the local dev compose).
# Pinned upstream release, checksum verified on x86_64 and ARM runners.
set -eu
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
if command -v gitleaks >/dev/null 2>&1; then
  scanner=$(command -v gitleaks)
else
  case "$(uname -m)" in
    x86_64) scan_arch=x64; scan_sha=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb ;;
    aarch64|arm64) scan_arch=arm64; scan_sha=e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080 ;;
    *) echo "Unsupported scanner architecture" >&2; exit 1 ;;
  esac
  curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_${scan_arch}.tar.gz" -o "$work/gitleaks.tar.gz"
  printf '%s  %s\n' "$scan_sha" "$work/gitleaks.tar.gz" | sha256sum -c -
  tar -xzf "$work/gitleaks.tar.gz" -C "$work" gitleaks
  scanner="$work/gitleaks"
fi
if [ "${1:-}" = "--history" ]; then
  # Every commit reachable here, with the reviewed history findings in .gitleaksignore.
  exec "$scanner" git --redact --no-banner
fi
mkdir "$work/tree"
git archive HEAD | tar -x -C "$work/tree"
cd "$work/tree"
"$scanner" dir --redact --no-banner .
