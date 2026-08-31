"""Payload layout and the rollback it makes possible (ADR-0006).

The bootstrap lives in the signed bundle and picks WHICH copy of our code to
run. Every rule here exists because the alternative is a bridge that cannot
start and cannot say why: a half-extracted version must never be chosen, a
version that crashed on its last launch must not be chosen twice, and running
out of good versions must fall back to the copy that shipped inside the
bundle rather than raising.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from vbboot import layout


@pytest.fixture()
def root(tmp_path):
    r = tmp_path / "payload"
    r.mkdir()
    return r


def _install(root, version: str, *, complete: bool = True) -> None:
    d = root / version
    (d / "vibebridge").mkdir(parents=True)
    (d / "vibebridge" / "__init__.py").write_text(
        f'__version__ = "{version}"\n')
    if complete:
        (d / layout.STAMP).write_text(json.dumps({"version": version}))


# ---------------------------------------------------------------- versions

def test_versions_are_ordered_numerically_not_lexically(root):
    for v in ("0.9.0", "0.10.0", "0.2.0"):
        _install(root, v)
    assert layout.installed(root) == ["0.2.0", "0.9.0", "0.10.0"]


def test_incomplete_install_is_invisible(root):
    """A directory without the completion stamp is a download in progress or
    a crash mid-extract — never a candidate."""
    _install(root, "0.1.0")
    _install(root, "0.2.0", complete=False)
    assert layout.installed(root) == ["0.1.0"]
    assert layout.resolve_for_launch(root) == (root / "0.1.0")


def test_junk_directory_names_are_ignored(root):
    _install(root, "0.1.0")
    (root / "not-a-version").mkdir()
    (root / "0.1.0.tmp").mkdir()
    assert layout.installed(root) == ["0.1.0"]


def test_resolve_picks_the_newest(root):
    _install(root, "0.1.0")
    _install(root, "0.2.0")
    assert layout.resolve_for_launch(root) == (root / "0.2.0")


def test_resolve_returns_none_when_nothing_is_installed(root):
    """Не бросает: пустой payload — это первый запуск, оболочка берёт seed."""
    assert layout.resolve_for_launch(root) is None


def test_resolve_survives_a_missing_root(tmp_path):
    assert layout.resolve_for_launch(tmp_path / "never-created") is None


# ---------------------------------------------------------------- rollback

def test_version_that_crashed_on_last_launch_is_quarantined(root):
    _install(root, "0.1.0")
    _install(root, "0.2.0")

    layout.begin_launch(root, "0.2.0")          # marker written, then a crash
    # next boot sees the marker still there
    assert layout.resolve_for_launch(root) == (root / "0.1.0")
    assert layout.is_quarantined(root, "0.2.0")


def test_clean_launch_clears_the_marker_and_keeps_the_version(root):
    _install(root, "0.1.0")
    _install(root, "0.2.0")

    layout.begin_launch(root, "0.2.0")
    layout.complete_launch(root, "0.2.0")

    assert layout.resolve_for_launch(root) == (root / "0.2.0")
    assert not layout.is_quarantined(root, "0.2.0")


def test_quarantine_is_sticky_across_boots(root):
    _install(root, "0.1.0")
    _install(root, "0.2.0")
    layout.begin_launch(root, "0.2.0")
    layout.resolve_for_launch(root)              # first boot quarantines it
    assert layout.resolve_for_launch(root) == (root / "0.1.0")  # and agrees


def test_a_newer_good_version_wins_over_a_quarantined_one(root):
    for v in ("0.1.0", "0.2.0", "0.3.0"):
        _install(root, v)
    layout.begin_launch(root, "0.3.0")
    assert layout.resolve_for_launch(root) == (root / "0.2.0")
    _install(root, "0.4.0")
    assert layout.resolve_for_launch(root) == (root / "0.4.0")


def test_all_versions_quarantined_falls_back_to_none(root):
    _install(root, "0.1.0")
    layout.begin_launch(root, "0.1.0")
    assert layout.resolve_for_launch(root) is None   # → seed, not a crash


def test_reinstalling_a_quarantined_version_clears_its_quarantine(root):
    """A fixed release carries the same number only when the operator
    republished it; installing again is an explicit second chance."""
    _install(root, "0.1.0")
    _install(root, "0.2.0")
    layout.begin_launch(root, "0.2.0")
    layout.resolve_for_launch(root)
    assert layout.is_quarantined(root, "0.2.0")

    layout.mark_installed(root, "0.2.0")
    assert not layout.is_quarantined(root, "0.2.0")
    assert layout.resolve_for_launch(root) == (root / "0.2.0")


# ---------------------------------------------------------------- pruning

def test_prune_keeps_the_active_and_one_predecessor(root):
    for v in ("0.1.0", "0.2.0", "0.3.0", "0.4.0"):
        _install(root, v)
    layout.prune(root, keep=2)
    assert layout.installed(root) == ["0.3.0", "0.4.0"]


def test_prune_never_removes_a_version_it_would_roll_back_to(root):
    for v in ("0.1.0", "0.2.0"):
        _install(root, v)
    layout.begin_launch(root, "0.2.0")
    layout.resolve_for_launch(root)       # 0.2.0 quarantined, 0.1.0 active
    layout.prune(root, keep=1)
    assert (root / "0.1.0").is_dir()      # the one actually in use survives


# ---------------------------------------------------- reads must not mutate

def test_reading_the_active_version_never_quarantines_anything(root):
    """Caught live 2026-08-30, and it took the bridge down.

    `resolve` quarantines a version whose launch marker survived — that is
    its job at boot. But `active_version` was built on it, `/api/version`
    called `active_version`, and the panel polls that. One panel request
    inside the 20-second settle window therefore condemned the version that
    was running perfectly, and the next launch refused to start it.

    A read is a read. Only the boot path may write.
    """
    _install(root, "0.1.0")
    _install(root, "0.2.0")
    layout.begin_launch(root, "0.2.0")        # currently settling, healthy

    assert layout.active_version(root) == "0.2.0"
    assert not layout.is_quarantined(root, "0.2.0")
    assert (root / f"{layout._LAUNCHING}0.2.0").exists()   # untouched

    # …and the boot path still does its job.
    assert layout.resolve_for_launch(root) == (root / "0.1.0")
    assert layout.is_quarantined(root, "0.2.0")


def test_active_version_ignores_quarantined_versions(root):
    _install(root, "0.1.0")
    _install(root, "0.2.0")
    layout.quarantine(root, "0.2.0")
    assert layout.active_version(root) == "0.1.0"


def test_boot_path_is_the_only_writer(root):
    _install(root, "0.2.0")
    layout.begin_launch(root, "0.2.0")
    before = sorted(p.name for p in root.iterdir())
    layout.installed(root)
    layout.active_version(root)
    layout.usable(root)
    assert sorted(p.name for p in root.iterdir()) == before


def test_the_shell_and_the_payload_carry_one_version():
    """Two files hold it — `tool.briefcase.version` in pyproject and
    `__version__` in the package — and on 2026-08-31 they drifted: the shell
    said 0.14.0 while the payload still said 0.13.0. The update check compares
    the RUNNING version against the newest release tag, so a payload that
    misreports its version either reinstalls what is already there or refuses
    an update that exists. The build script gates on this too; the test is
    here so a bump caught in review never reaches the build.
    """
    import tomllib

    import vibebridge

    proj = tomllib.loads(
        (pathlib.Path(__file__).parent.parent / "pyproject.toml").read_text())
    assert vibebridge.__version__ == proj["tool"]["briefcase"]["version"]

