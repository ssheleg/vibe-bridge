#!/bin/bash
# Launch the menu-bar bridge under the user's Aqua session (via LaunchAgent).
# Menu bar + consent dialogs work here without .app packaging; only the
# TCC-gated screen reads (screenshot/System Events) need the signed bundle.
cd "$(dirname "$0")/.."
exec /opt/homebrew/bin/uv run python -m vibebridge.app
