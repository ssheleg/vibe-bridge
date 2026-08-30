"""The hand-over: pick a payload, guard the port, run it, confirm it lived.

This is the last code that is guaranteed to work. Everything it calls
afterwards is the payload, which may be a version downloaded ten minutes ago,
so each step here assumes the next one might not survive.
"""
from __future__ import annotations

import contextlib
import socket
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from . import layout

# How long a payload must stay up before its launch counts as successful.
# Long enough to cover an import error or a crash inside startup, short
# enough that a real crash is still attributed to the version that caused it.
SETTLE_SECONDS = 20


@dataclass(frozen=True)
class Chosen:
    path: Path
    version: str
    source: str            # "payload" (installed) | "seed" (shipped in .app)
    fell_back: bool = False   # a newer version existed and could not be used


def _package_ok(root: Path) -> bool:
    return (root / "vibebridge" / "__init__.py").is_file()


def _seed_version(seed: Path) -> str:
    """Read the seed's version without importing it — importing would bind
    `vibebridge` to the seed before we have decided what to run."""
    try:
        text = (seed / "vibebridge" / "__init__.py").read_text()
    except OSError:
        return "0.0.0"
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"\' ')
    return "0.0.0"


def choose_payload(root: Path, *, seed: Path) -> Chosen:
    """Newest usable code wins, and the seed is a candidate like any other.

    The seed competing on version — rather than only being a last resort —
    is what stops a fresh .app from being silently downgraded by the payload
    directory an older install left behind.
    """
    seed_ver = _seed_version(seed)
    seed_key = layout.parse(seed_ver) or ()

    while True:
        chosen = layout.resolve_for_launch(root)
        if chosen is None:
            break
        if not _package_ok(chosen):
            # Stamped complete but unusable — do not offer it again.
            layout.quarantine(root, chosen.name)
            continue
        if (layout.parse(chosen.name) or ()) <= seed_key:
            break                      # the bundle carries newer code
        return Chosen(chosen, chosen.name, "payload")

    # The seed runs. That is a FALLBACK only if something newer is sitting
    # right there unusable — otherwise the seed simply is the newest code.
    stranded = any((layout.parse(v) or ()) > seed_key
                   for v in layout.installed(root))
    return Chosen(seed, seed_ver, "seed", fell_back=stranded)


def bundle_root() -> Path | None:
    """The `.app` this shell lives in, or None outside one."""
    from . import __file__ as anchor
    for parent in Path(anchor).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def shell_version() -> str | None:
    """The SHELL's version, read from the bundle — never the payload's.

    The two move independently (ADR-0006): the payload updates freely, the
    shell only with a new signed .app. Asking the payload how old the shell
    is means a bridge that has updated twice believes its shell grew with it,
    and a payload declaring a `shell_min` it does not meet installs anyway.
    """
    root = bundle_root()
    if root is None:
        return None
    plist = root / "Contents" / "Info.plist"
    try:
        import plistlib
        with open(plist, "rb") as fh:
            data = plistlib.load(fh)
    except (OSError, ValueError):
        return None
    version = data.get("CFBundleShortVersionString")
    return str(version) if version else None


def guard_single_instance(port: int, host: str = "127.0.0.1") -> str | None:
    """None when the port is ours to take; otherwise the reason, in words the
    owner can act on. Binding is the only honest test — a running bridge is
    exactly a process already holding this port."""
    probe = socket.socket()
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, port))
    except OSError:
        return (f"порт {port} уже занят — похоже, мост уже запущен "
                f"(проверьте меню-бар и `launchctl list | grep vibe-bridge`)")
    finally:
        probe.close()
    return None


def settle_later(root: Path, version: str,
                 delay: float = SETTLE_SECONDS) -> threading.Timer:
    """Clear the launch marker once the payload has stayed up.

    Deferred rather than immediate: a payload that raises during startup must
    leave its marker behind, and that marker is the only evidence the next
    boot has that this version does not work.
    """
    timer = threading.Timer(delay, layout.complete_launch, (root, version))
    timer.daemon = True
    timer.start()
    return timer


def run_payload(root: Path, *, seed: Path, loader):
    """Choose code, load it, and if it will not load, fall back HERE.

    The launch-marker mechanism rolls a bad version back on the *next* boot,
    which for a login-item app means the owner has no bridge until they
    notice and start it again (observed 2026-08-30 with a payload that raised
    on import). So a failure to load is handled in this process: quarantine
    the version, pick the next candidate, try again. The seed is the floor —
    if that will not load either, the exception belongs to the caller.
    """
    while True:
        chosen = choose_payload(root, seed=seed)
        activate(chosen, root)
        try:
            return loader(chosen), chosen
        except Exception:
            _deactivate(chosen)
            if chosen.source == "seed":
                raise                    # nothing left to fall back to
            layout.quarantine(root, chosen.version)


def _deactivate(chosen: Chosen) -> None:
    """Undo `activate`'s path entry so the next attempt starts clean."""
    with contextlib.suppress(ValueError):
        sys.path.remove(str(chosen.path))
    for name in [m for m in sys.modules if m == "vibebridge"
                 or m.startswith("vibebridge.")]:
        del sys.modules[name]


def activate(chosen: Chosen, root: Path) -> None:
    """Put the chosen code on `sys.path` ahead of anything else and arm the
    crash detector. The bundle's own directory stays on the path behind it —
    that is where the dependencies live (ADR-0006)."""
    sys.path.insert(0, str(chosen.path))
    if chosen.source == "payload":
        layout.begin_launch(root, chosen.version)
        settle_later(root, chosen.version)
