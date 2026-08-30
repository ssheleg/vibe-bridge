"""What the shell decides before any of our code runs.

Two decisions live here and both are about not lying. `choose_payload` picks
between an installed version and the seed that shipped in the bundle — and
when it falls back it must SAY it fell back, because "running the version you
installed" and "running the version from the DMG" look identical from the
tray. `guard_single_instance` refuses the second copy: two bridges on port
48620 is one bridge answering the robot and one silently dead, which is worse
than a startup that says the port is taken.
"""
from __future__ import annotations

import socket

import pytest

from vbboot import layout, runner


@pytest.fixture()
def bundle(tmp_path):
    seed = tmp_path / "seed"
    (seed / "vibebridge").mkdir(parents=True)
    (seed / "vibebridge" / "__init__.py").write_text('__version__ = "0.1.0"\n')
    return seed


@pytest.fixture()
def root(tmp_path):
    r = tmp_path / "payload"
    r.mkdir()
    return r


def _install(root, version):
    d = root / version
    (d / "vibebridge").mkdir(parents=True)
    (d / "vibebridge" / "__init__.py").write_text(
        f'__version__ = "{version}"\n')
    layout.mark_installed(root, version)


# ------------------------------------------------------------ which code runs

def test_seed_runs_when_nothing_is_installed(bundle, root):
    chosen = runner.choose_payload(root, seed=bundle)
    assert chosen.path == bundle
    assert chosen.version == "0.1.0"
    assert chosen.source == "seed"


def test_installed_version_wins_over_the_seed(bundle, root):
    _install(root, "0.2.0")
    chosen = runner.choose_payload(root, seed=bundle)
    assert chosen.path == root / "0.2.0"
    assert chosen.version == "0.2.0"
    assert chosen.source == "payload"


def test_seed_wins_when_it_is_newer_than_everything_installed(bundle, root):
    """A freshly installed .app carries a newer seed than the payload left by
    the previous install — taking the older payload would silently downgrade
    the owner right after they dragged a new build to Applications."""
    _install(root, "0.0.9")
    chosen = runner.choose_payload(root, seed=bundle)
    assert chosen.source == "seed" and chosen.version == "0.1.0"


def test_a_crashed_version_is_skipped_on_the_next_launch(bundle, root):
    _install(root, "0.2.0")
    _install(root, "0.3.0")
    layout.begin_launch(root, "0.3.0")            # crash: marker survives
    chosen = runner.choose_payload(root, seed=bundle)
    assert chosen.version == "0.2.0"
    assert layout.is_quarantined(root, "0.3.0")


def test_falling_all_the_way_back_to_the_seed_is_reported_not_hidden(bundle,
                                                                    root):
    _install(root, "0.2.0")
    layout.begin_launch(root, "0.2.0")
    chosen = runner.choose_payload(root, seed=bundle)
    assert chosen.source == "seed"
    assert chosen.fell_back is True               # the panel says so


def test_a_payload_missing_its_package_is_not_chosen(bundle, root):
    """Stamped complete but empty — disk corruption, or a release built
    wrong. The seed is a working bridge; this is not."""
    d = root / "0.2.0"
    d.mkdir()
    layout.mark_installed(root, "0.2.0")
    chosen = runner.choose_payload(root, seed=bundle)
    assert chosen.source == "seed"


# --------------------------------------------------------------- one instance

def test_guard_allows_a_free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert runner.guard_single_instance(free) is None


def test_guard_reports_a_taken_port_instead_of_racing_for_it():
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        taken = held.getsockname()[1]
        why = runner.guard_single_instance(taken)
    assert why is not None
    assert str(taken) in why


# ------------------------------------------------------- the shell's own version

def test_shell_version_comes_from_the_bundle_not_the_payload(tmp_path,
                                                             monkeypatch):
    """ADR-0006 splits the two on purpose: dependencies live in the shell,
    our code lives in the payload. If the shell reported the PAYLOAD's
    version, then updating to 0.2.0 would make the shell claim to be 0.2.0 —
    and a later payload declaring `shell_min = 0.2.0` would install against a
    shell that is still 0.1.0 and cannot import what it needs.
    """
    resources = tmp_path / "vibe-bridge.app" / "Contents" / "Resources"
    (resources / "app" / "vbboot").mkdir(parents=True)
    (resources / "app" / "vbboot" / "__init__.py").write_text("")
    (tmp_path / "vibe-bridge.app" / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>'
        '<key>CFBundleShortVersionString</key><string>0.1.0</string>'
        "</dict></plist>\n")

    import vbboot
    monkeypatch.setattr(vbboot, "__file__",
                        str(resources / "app" / "vbboot" / "__init__.py"))
    assert runner.shell_version() == "0.1.0"


def test_shell_version_outside_a_bundle_is_reported_as_unknown(monkeypatch):
    """A development checkout has no shell at all. Returning a real-looking
    number would let a payload's compatibility check pass on nothing."""
    import vbboot
    monkeypatch.setattr(vbboot, "__file__", "/Users/x/repo/vbboot/__init__.py")
    assert runner.shell_version() is None
