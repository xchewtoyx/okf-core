#!/bin/bash
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Install just if not present
if ! command -v just &>/dev/null; then
  apt-get install -y just
fi

# Create venv and install package with test deps
just install
