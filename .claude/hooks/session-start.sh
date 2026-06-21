#!/usr/bin/env bash
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
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

# Create venv and install package with test deps
if command -v just &>/dev/null; then
  just install
fi
