"""The message shown when the bridge cannot start.

This text exists for the one moment the panel cannot help: the bridge is not
running, so there is no journal and no settings card. If the notification
itself fails to render, the owner gets silence at exactly the moment they
needed a sentence — which is how a port clash becomes "it just stopped
working". Live on 2026-08-30 the port-guard message did fail this way: it was
escaped with Python's repr(), and AppleScript rejected the result.
"""
from __future__ import annotations

import subprocess

from vbboot.__main__ import _applescript_string, _complain


def test_plain_text_is_quoted_for_applescript():
    assert _applescript_string("порт занят") == '"порт занят"'


def test_double_quotes_are_escaped_not_left_to_terminate_the_string():
    assert _applescript_string('он сказал "нет"') == '"он сказал \\"нет\\""'


def test_backslashes_are_escaped_first():
    assert _applescript_string(r"путь\к") == r'"путь\\к"'


def test_the_real_port_guard_message_survives_escaping():
    msg = ("порт 48620 уже занят — похоже, мост уже запущен "
           "(проверьте меню-бар и `launchctl list | grep vibe-bridge`)")
    quoted = _applescript_string(msg)
    assert quoted.startswith('"') and quoted.endswith('"')
    assert "\n" not in quoted


def test_newlines_cannot_break_out_of_the_literal():
    assert "\n" not in _applescript_string("строка\nвторая")


def test_complain_reaches_osascript_with_a_parsable_script(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(subprocess, "run", fake_run)
    _complain('сбой "важный"')

    script = seen["cmd"][-1]
    assert script.startswith("display notification ")
    # The payload must be a single balanced AppleScript literal.
    assert script.count('"') % 2 == 0
