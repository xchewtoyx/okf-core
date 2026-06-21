#!/usr/bin/env bash
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# CLAUDE_PROJECT_DIR is always set and valid when CLAUDE_CODE_REMOTE=true,
# but guard defensively so a misconfigured environment warns rather than crashes.
if [ -z "${CLAUDE_PROJECT_DIR:-}" ] || [ ! -d "$CLAUDE_PROJECT_DIR" ]; then
  echo "warning: CLAUDE_PROJECT_DIR not set or not a directory; skipping session hook" >&2
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Claude Code on the web runs on Debian/Ubuntu as root, so apt-get is
# available without sudo. The install is best-effort: a transient network
# failure should not abort the session.
if ! command -v just &>/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y just \
    || echo "warning: could not install just via apt-get" >&2
fi

# Create venv and install package with test deps. Best-effort: a pip or
# network failure should warn but not abort the session — the user can
# run 'just install' manually, and just recipes will prompt them if needed.
if command -v just &>/dev/null; then
  just install || echo "warning: just install failed; run 'just install' manually" >&2
fi
