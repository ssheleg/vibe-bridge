#!/bin/bash
# Run the bridge from a source checkout, under the user's GUI session.
#
# This is the DEVELOPMENT path. It gets no automatic updates (there is no
# signed bundle, so no key to trust a payload against) and no TCC-gated screen
# reads until packaged — `scripts/build_app.sh` is the real install.
set -euo pipefail
cd "$(dirname "$0")/.."

# uv lives in a different place depending on how it was installed: Homebrew on
# Apple Silicon, Homebrew on Intel, or the official installer into ~/.local.
# Hardcoding one of them is why this script only worked on its author's mac.
UV="$(command -v uv || true)"
for candidate in /opt/homebrew/bin/uv /usr/local/bin/uv "$HOME/.local/bin/uv"; do
  [[ -n "$UV" ]] && break
  [[ -x "$candidate" ]] && UV="$candidate"
done
if [[ -z "$UV" ]]; then
  echo "vibe-bridge: не нашёл uv. Установите его — https://docs.astral.sh/uv/" >&2
  exit 1
fi

exec "$UV" run python -m vibebridge.app
