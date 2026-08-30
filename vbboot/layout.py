"""Where payload versions live, which one runs, and how a bad one is undone.

The whole rollback story is three files on disk, deliberately: a mechanism the
bootstrap can execute with no network, no state file to corrupt and no daemon
to be running.

    payload/
      0.1.0/                 a version
      0.1.0/.installed       written LAST — its absence means "not finished"
      .launching-0.2.0       written before handing over, removed once up
      .failed-0.2.0          that version crashed on launch; skip it

`.installed` is the completion stamp: extraction writes files first and the
stamp afterwards, so a download killed halfway leaves a directory that
`installed()` simply cannot see. `.launching-*` surviving a boot is the only
evidence a crash ever leaves, so finding one is what quarantines a version.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

STAMP = ".installed"
_LAUNCHING = ".launching-"
_FAILED = ".failed-"


def support_dir() -> Path:
    """The bridge's own directory — the same one `state.py` picks, computed
    again here because the bootstrap must not import the payload."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "vibe-bridge"
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        return Path(os.environ.get("APPDATA", Path.home())) / "vibe-bridge"
    return Path(os.environ.get(  # pragma: no cover - Linux
        "XDG_CONFIG_HOME", Path.home() / ".config")) / "vibe-bridge"


def payload_root() -> Path:
    return support_dir() / "payload"


def parse(version: str) -> tuple[int, ...] | None:
    """`"0.10.0"` → `(0, 10, 0)`; anything else → None.

    Numeric, never lexical: released in order, `0.10.0` follows `0.9.0`, and
    a string sort puts it before.
    """
    parts = version.split(".")
    if not (2 <= len(parts) <= 4):
        return None
    try:
        nums = tuple(int(p) for p in parts)
    except ValueError:
        return None
    return nums if all(n >= 0 for n in nums) else None


def installed(root: Path) -> list[str]:
    """Complete, well-named versions, oldest first. Never raises."""
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    good = [e.name for e in entries
            if e.is_dir() and parse(e.name) and (e / STAMP).exists()]
    return sorted(good, key=lambda v: parse(v))  # type: ignore[arg-type]


def mark_installed(root: Path, version: str) -> None:
    """Finish an install — and give a previously-failed version a second
    chance, because re-installing it is an explicit act, not an accident."""
    (root / version / STAMP).write_text(version)
    _unlink(root / f"{_FAILED}{version}")


def is_quarantined(root: Path, version: str) -> bool:
    return (root / f"{_FAILED}{version}").exists()


def begin_launch(root: Path, version: str) -> None:
    """Announce the hand-over. If this marker is still here next boot, the
    version did not survive its own startup."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{_LAUNCHING}{version}").write_text(version)
    except OSError:  # pragma: no cover - unwritable support dir
        pass


def complete_launch(root: Path, version: str) -> None:
    _unlink(root / f"{_LAUNCHING}{version}")


def quarantine(root: Path, version: str) -> None:
    try:
        (root / f"{_FAILED}{version}").write_text(version)
    except OSError:  # pragma: no cover
        pass
    complete_launch(root, version)


def usable(root: Path) -> Path | None:
    """Newest version not under quarantine — a PURE read.

    Deliberately blind to launch markers: a marker means "this version is
    still proving itself", and only the boot path is entitled to rule on
    that. See `resolve_for_launch`.
    """
    for version in reversed(installed(root)):
        if not is_quarantined(root, version):
            return root / version
    return None


def resolve_for_launch(root: Path) -> Path | None:
    """The version to run — the ONLY function here that writes.

    A launch marker still present means that version's last launch never
    reported success, so it is quarantined and the next candidate tried.
    None is not an error: the shell then runs the seed it shipped with. A
    bridge that refuses to start because an update went wrong is worse than a
    bridge running last month's code.

    Split from `usable` on 2026-08-30 after the read path took the bridge
    down: `/api/version` called this through `active_version`, and a panel
    request inside the settle window condemned the healthy running version.
    """
    for version in reversed(installed(root)):
        if is_quarantined(root, version):
            continue
        if (root / f"{_LAUNCHING}{version}").exists():
            quarantine(root, version)
            continue
        return root / version
    return None


def active_version(root: Path) -> str | None:
    """What the panel should name as installed. Pure — never quarantines."""
    chosen = usable(root)
    return chosen.name if chosen else None


def prune(root: Path, keep: int = 2) -> list[str]:
    """Drop old versions, keeping `keep` newest — but never the one actually
    running. Quarantined versions are dropped first: they are the reason the
    active version may not be the newest installed one."""
    active = active_version(root)
    survivors = installed(root)[-keep:]
    if active and active not in survivors:
        survivors = survivors[1:] + [active] if survivors else [active]
    removed = []
    for version in installed(root):
        if version in survivors:
            continue
        shutil.rmtree(root / version, ignore_errors=True)
        _unlink(root / f"{_FAILED}{version}")
        _unlink(root / f"{_LAUNCHING}{version}")
        removed.append(version)
    return removed


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
