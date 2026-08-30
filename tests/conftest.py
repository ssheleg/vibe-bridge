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
