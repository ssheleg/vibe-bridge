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

say "Иконка"
# Regenerated every build: the mark is code, so it cannot drift from the
# tokens it is drawn from (docs/design/ui.md).
uv run python scripts/make_icon.py | tail -1

say "Каркас .app"
# `briefcase update` refreshes our sources but NOT Info.plist, so a version
# bump left the bundle claiming the old number while the DMG carried the new
# one (2026-08-30). That number is what a payload checks `shell_min` against,
# so a stale one silently accepts an incompatible payload. Recreate the
# scaffold whenever it disagrees with pyproject.
WANT_VERSION="$(uv run python -c "import tomllib,pathlib;print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['tool']['briefcase']['version'])")"
HAVE_VERSION="$(plutil -extract CFBundleShortVersionString raw "$APP/Contents/Info.plist" 2>/dev/null || echo none)"
if [[ "$WANT_VERSION" != "$HAVE_VERSION" ]]; then
  echo "версия оболочки: $HAVE_VERSION → $WANT_VERSION, пересоздаю каркас"
  # The WHOLE platform directory: briefcase treats its parent as the template
  # and refuses to recreate over it ("existing application template will not
  # be overwritten"), leaving a half-empty tree that `update` then trips on.
  rm -rf build/vibe-bridge/macos
fi
if [[ ! -d "$APP" ]]; then
  # `briefcase create` builds the bundle and THEN installs requirements. We do
  # not use its installer — it resolves with `--only-binary`, and both `rumps`
  # and `http-ece` publish sdists only, so the step always errors here. The
  # bundle it produced before erroring is exactly what we want, so the failure
  # is tolerated and the scaffold is verified instead of the exit code.
  uv run briefcase create macOS --no-input || true
  [[ -f "$APP/Contents/Info.plist" ]] || {
    echo "briefcase create не оставил каркаса — смотрите logs/"; exit 1; }
fi
uv run briefcase update macOS --no-input >/dev/null   # refresh our sources
# Renames the stub binary to CFBundleExecutable and thins it. Missing from
# this script until 2026-08-30 and invisible while one long-lived scaffold was
# reused — the first rebuild from scratch left `Contents/MacOS/Stub` beside the
# real executable and signing died with "code object is not signed at all".
uv run briefcase build macOS --no-input >/dev/null

say "Зависимости внутрь бандла"
# Every Mach-O the process loads must live here and be signed by us — that is
# what lets the app run with library validation ON (ADR-0006).
rm -rf "$APP/Contents/Resources/app_packages"
uv pip install --quiet --target "$APP/Contents/Resources/app_packages" \
  --python-platform macos --python-version 3.12 \
  "mcp==1.26.0" "pywebpush>=2.4.0" "rumps>=0.4" \
  "pyobjc-framework-ServiceManagement>=12" "pyobjc-framework-Quartz>=12" \
  "pyobjc-framework-WebKit>=12" \
  "std-nslog>=1.0"

# Debug symbols and test suites: dead weight the owner downloads, and a
# signing hazard — codesign cannot sign the DWARF file inside a .dSYM bundle,
# which failed the whole package step on 2026-08-30 over PyObjCTest.
PKGS="$APP/Contents/Resources/app_packages"
find "$PKGS" -name '*.dSYM' -type d -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$PKGS/PyObjCTest"
find "$PKGS" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
echo "app_packages: $(du -sh "$PKGS" | cut -f1) после чистки"

say "Публичный ключ релизов в бандл"
# The trust anchor for every future payload. Signed along with the bundle, so
# replacing it means re-signing the app with our Developer ID.
uv run python scripts/release_key.py public > "$APP/Contents/Resources/release_pubkey.raw"
test "$(wc -c < "$APP/Contents/Resources/release_pubkey.raw")" -eq 32

# The gate this whole block exists for: never ship a bundle whose declared
# version is not the one we built.
GOT_VERSION="$(plutil -extract CFBundleShortVersionString raw "$APP/Contents/Info.plist")"
if [[ "$GOT_VERSION" != "$WANT_VERSION" ]]; then
  echo "Info.plist говорит $GOT_VERSION, собирали $WANT_VERSION — сборка остановлена"; exit 1
fi
echo "версия оболочки в Info.plist: $GOT_VERSION"

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
