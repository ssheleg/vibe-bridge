"""Capabilities: right command built, blocklist enforced, errors honest.

A FakeRunner records argv and returns canned output — no screen, no osascript.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibebridge.capabilities import (
    CapabilityError,
    Runner,
    build_capabilities,
)
from vibebridge.consent import ToolClass


class FakeRunner(Runner):
    def __init__(self, out: str = "ok"):
        self.calls: list[tuple[list[str], str | None]] = []
        self._out = out

    def run(self, argv, *, timeout=20.0, input_text=None):
        self.calls.append((argv, input_text))
        return self._out


CAPS = build_capabilities()


def test_all_expected_tools_present():
    assert set(CAPS) == {
        "screenshot", "list_apps", "frontmost", "notify",
        "open_app", "open_url", "shortcut_run",
        "automation", "clipboard_read", "clipboard_write",
    }


def test_read_vs_act_classes():
    read = {"screenshot", "list_apps", "frontmost", "notify"}
    for name, cap in CAPS.items():
        want = ToolClass.READ if name in read else ToolClass.ACT
        assert cap.tool_class is want, name


def test_open_app_builds_open_command():
    r = FakeRunner()
    out = CAPS["open_app"].handler(r, {"app": "Safari"})
    assert r.calls[0][0] == ["open", "-a", "Safari"]
    assert "Safari" in out


def test_open_url_rejects_non_http():
    r = FakeRunner()
    with pytest.raises(CapabilityError):
        CAPS["open_url"].handler(r, {"url": "file:///etc/passwd"})
    assert r.calls == []   # nothing executed


def test_applescript_blocklist():
    r = FakeRunner()
    with pytest.raises(CapabilityError):
        CAPS["automation"].handler(
            r, {"script": 'tell application "Terminal" to do script "rm -rf ~"'})
    assert r.calls == []


def test_applescript_allows_ordinary_app():
    r = FakeRunner(out="done")
    CAPS["automation"].handler(
        r, {"script": 'tell application "Music" to play'})
    assert r.calls  # executed


def test_clipboard_write_pipes_text():
    r = FakeRunner()
    CAPS["clipboard_write"].handler(r, {"text": "hello"})
    argv, inp = r.calls[0]
    assert argv == ["pbcopy"]
    assert inp == "hello"


def test_summary_renders_args():
    assert "Safari" in CAPS["open_app"].summary({"app": "Safari"})
    # missing arg must not crash the consent line
    assert CAPS["open_app"].summary({})


def test_runner_error_is_capability_error():
    r = Runner()
    with pytest.raises(CapabilityError):
        r.run(["definitely-not-a-real-binary-xyz"])
