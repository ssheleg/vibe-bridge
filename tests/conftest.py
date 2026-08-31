"""Test isolation for things that live in the user's home.

`config.load()` reads `~/Library/Application Support/vibe-bridge/config.toml`.
Without this fixture the suite would read — and its behaviour would depend on
— the settings of whoever runs it, which is how a test starts passing on one
machine and failing on another for reasons nobody can see in the diff.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path_factory, monkeypatch):
    from vibebridge import config

    home = tmp_path_factory.mktemp("vb-config")
    monkeypatch.setattr(config, "config_path", lambda: home / "config.toml")
    for var in ("VIBE_BRIDGE_PORT", "VIBE_BRIDGE_MODE"):
        monkeypatch.delenv(var, raising=False)

@pytest.fixture(autouse=True)
def _no_real_notifications(monkeypatch):
    """No test may put a toast on the owner's screen.

    One did, for weeks: `test_notifier_never_raises` hid `desktop_notifier` to
    reach the osascript fallback and then called it for real, so every suite
    run posted «заголовок / текст» to the owner — attributed to Script Editor,
    which is who `osascript` posts as, which is why it never appeared in the
    bridge's own journal and why I twice told the owner it was not ours.

    The verdict is delivered at TEARDOWN, not by raising at the call. Raising
    was tried first and did nothing: `_osa` wraps its call in
    `except Exception` and honestly reported "уведомление не показано", so the
    planted defect passed green. A guard that the code under test is allowed
    to catch is not a guard — verified 2026-08-31 by planting exactly that
    defect and watching it pass.
    """
    import subprocess
    real = subprocess.run
    caught: list[str] = []

    def guarded(argv, *a, **kw):
        flat = " ".join(str(x) for x in (
            argv if isinstance(argv, (list, tuple)) else [argv]))
        if "display notification" in flat:
            caught.append(flat[:140])
            return subprocess.CompletedProcess(argv, 0, "", "")
        return real(argv, *a, **kw)

    monkeypatch.setattr(subprocess, "run", guarded)
    yield
    if caught:
        pytest.fail("тест дошёл до настоящего уведомления владельцу: "
                    + caught[0], pytrace=False)
