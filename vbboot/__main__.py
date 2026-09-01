"""The .app's entry point: choose the code, then get out of the way.

Everything below the hand-over may be a payload downloaded an hour ago, so
this module keeps its own failure modes to two, both of which end in a
message a person can read rather than a traceback in a log nobody opens.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import layout, runner


def _seed_dir() -> Path:
    """The copy of `vibebridge` that shipped inside the bundle.

    `Contents/Resources/seed` in a built .app; the repository root in a
    development checkout, so `python -m vbboot` behaves the same either way.
    """
    here = Path(__file__).resolve().parent
    for parent in (here, *here.parents):
        seed = parent / "seed"
        if (seed / "vibebridge" / "__init__.py").is_file():
            return seed
        if (parent / "vibebridge" / "__init__.py").is_file():
            return parent
    return here.parent


def _applescript_string(text: str) -> str:
    """Quote `text` as an AppleScript string literal.

    Not `repr()`: that escapes for Python, and on 2026-08-30 the port-guard
    message reached osascript as a Python literal and came back
    `syntax error … found unknown token`. The owner saw nothing at the one
    moment the panel could not speak for the bridge. Backslash first, then
    quotes, then the line breaks AppleScript will not carry inside a literal.
    """
    escaped = (text.replace("\\", "\\\\")
                   .replace('"', '\\"')
                   .replace("\n", " ")
                   .replace("\r", " "))
    return f'"{escaped}"'


def _complain(message: str) -> None:
    """Say it where the owner will actually see it: a notification if the
    frameworks are there, stderr regardless."""
    print(f"vibe-bridge: {message}", file=sys.stderr)
    if sys.platform != "darwin":
        return
    try:
        import subprocess
        subprocess.run(
            ["osascript", "-e",
             f"display notification {_applescript_string(message)} "
             f'with title "vibe-bridge"'],
            check=False, timeout=10)
    except Exception:                       # noqa: BLE001 - best effort only
        pass


def _configured_port() -> int:
    """Порт, который РЕАЛЬНО займёт payload: env → config.toml → умолчание.

    Оболочка охраняла `VIBE_BRIDGE_PORT` или константу и `config.toml` не
    читала никогда — то есть при изменённом порте охраняла чужой номер, а
    совет в её же сообщении («поменяйте port в config.toml») не работал.
    `tomllib` из stdlib, так что правило «оболочка без зависимостей» цело.
    """
    env = os.environ.get("VIBE_BRIDGE_PORT")
    if env and env.strip().isdigit():
        return int(env)
    try:
        import tomllib
        conf = layout.support_dir() / "config.toml"
        with open(conf, "rb") as fh:
            value = tomllib.load(fh).get("port")
        if isinstance(value, int) and 1 <= value <= 65535:
            return value
    except Exception:                       # noqa: BLE001 - умолчание честнее
        pass
    return 48620


def main() -> int:
    from .runner import guard_single_instance

    port = _configured_port()
    busy = guard_single_instance(port)
    if busy:
        _complain(busy)
        return 1

    root = layout.payload_root()

    def load(_chosen):
        from vibebridge.app import run
        return run

    try:
        run, chosen = runner.run_payload(root, seed=_seed_dir(), loader=load)
    except Exception as exc:                # noqa: BLE001 - even the seed failed
        _complain(f"не удалось загрузить код моста: {exc}")
        return 1

    if chosen.fell_back:
        _complain("обновление не запустилось — мост работает на предыдущей "
                  "версии, подробности в журнале")

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
