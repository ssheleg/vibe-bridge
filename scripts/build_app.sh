#!/usr/bin/env bash
# Build, sign and package the macOS shell (ADR-0006, board B-4).
#
#   scripts/build_app.sh                 # build + sign + DMG
#   scripts/build_app.sh --notarize      # …and notarize + staple
#
# Briefcase creates the scaffold and signs inside-out; the dependency install
# is done here instead of by `briefcase create`, which hung on this machine
# (2026-08-30) resolving a requirement set containing an sdist-only package.
# Everything else about the bundle — stub binary, Python framework, plist,
# entitlements — stays Briefcase's job, and is configured in pyproject.toml.
set -euo pipefail
cd "$(dirname "$0")/.."

IDENTITY="${VIBE_SIGN_IDENTITY:-Developer ID Application: Sergei Viktorovich Sheleg (KJ35UYYL22)}"
PROFILE="${VIBE_NOTARY_PROFILE:-vibe-bridge}"
APP="build/vibe-bridge/macos/app/vibe-bridge.app"
NOTARIZE=0
[[ "${1:-}" == "--notarize" ]] && NOTARIZE=1

say() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }

say "Проверки перед сборкой"
uv run python -m pytest tests/ -q | tail -1
uv run ruff check
uv run python docs/ux/lint.py | tail -1

say "Каркас .app"
[[ -d "$APP" ]] || uv run briefcase create macOS --no-input
uv run briefcase update macOS --no-input >/dev/null   # refresh our sources

say "Зависимости внутрь бандла"
# Every Mach-O the process loads must live here and be signed by us — that is
# what lets the app run with library validation ON (ADR-0006).
rm -rf "$APP/Contents/Resources/app_packages"
uv pip install --quiet --target "$APP/Contents/Resources/app_packages" \
  --python-platform macos --python-version 3.12 \
  "mcp==1.26.0" "pywebpush>=2.4.0" "rumps>=0.4" \
  "pyobjc-framework-ServiceManagement>=12" "pyobjc-framework-Quartz>=12" \
  "std-nslog>=1.0"

say "Публичный ключ релизов в бандл"
# The trust anchor for every future payload. Signed along with the bundle, so
# replacing it means re-signing the app with our Developer ID.
uv run python scripts/release_key.py public > "$APP/Contents/Resources/release_pubkey.raw"
test "$(wc -c < "$APP/Contents/Resources/release_pubkey.raw")" -eq 32

say "Подпись и DMG"
uv run briefcase package macOS -p dmg -i "$IDENTITY" --no-notarize --no-input | tail -2

say "Проверка подписи"
codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | tail -2
# Read once into a variable. NOT `codesign … | grep -q …`: grep -q exits on
# the first match, codesign takes SIGPIPE, and `set -o pipefail` then reports
# the whole pipeline as failed — which on 2026-08-30 made this script announce
# a missing hardened runtime on a bundle that had one.
SIGINFO="$(codesign -dvv "$APP" 2>&1)"
printf '%s\n' "$SIGINFO" | grep -E "^(Identifier|TeamIdentifier|Authority=Developer)" || true
case "$SIGINFO" in
  *"flags=0x10000(runtime)"*) echo "hardened runtime: on" ;;
  *) echo "hardened runtime ОТСУТСТВУЕТ — нотаризация откажет"; exit 1 ;;
esac

if [[ $NOTARIZE -eq 1 ]]; then
  say "Нотаризация"
  DMG=$(ls -t dist/vibe-bridge-*.dmg | head -1)
  xcrun notarytool submit "$DMG" --keychain-profile "$PROFILE" --wait
  xcrun stapler staple "$DMG"
  xcrun stapler staple "$APP"
  say "Вердикт Gatekeeper"
  spctl -a -vvv -t exec "$APP" 2>&1 | tail -2
else
  say "Вердикт Gatekeeper (до нотаризации — 'rejected' здесь ожидаем)"
  spctl -a -vvv -t exec "$APP" 2>&1 | tail -2
  echo
  echo "Нотаризация не выполнялась. Один раз заведите профиль:"
  echo "  xcrun notarytool store-credentials $PROFILE \\"
  echo "    --apple-id <apple-id> --team-id KJ35UYYL22 --password <app-specific-password>"
  echo "затем: scripts/build_app.sh --notarize"
fi

say "Готово: $(ls -t dist/vibe-bridge-*.dmg | head -1)"
