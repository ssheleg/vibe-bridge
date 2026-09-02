"""Три платформенных пака объявляют ОДИН контракт (F-6).

Фикс A-1 лёг только в macOS-копию — не по невнимательности, а потому что
копий было три и никто не обязан помнить про остальные две. На Windows в
блоклисте не было ни `SendKeys`, ни `System.Windows.Forms`: на первой же
сборке инструмент делал то, что продукт объявляет невозможным.

Проверяется не «списки одинаковы» — они и не должны быть одинаковы, у
платформ разные опасные имена, — а что ОБЩЕЕ применяется везде и что паки
обещают роботу одни и те же инструменты.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibebridge.capabilities import CapabilityError, refuse_if_dangerous

#: Скрипты, которые обязана отвергнуть КАЖДАЯ платформа. Это анти-визия
#: продукта, а не вкус: «никакого shell» и «не автопилот компьютера».
FORBIDDEN_EVERYWHERE = (
    'do shell script "whoami"',
    'Start-Process cmd.exe',
    "powershell -c whoami",
    "/bin/sh -c id",
    'tell application "System Events" to keystroke "a"',
    "[System.Windows.Forms.SendKeys]::SendWait('%{F4}')",
    "SendKeys('hello')",
    "xdotool type hello",
)


def _pack(name: str):
    if name == "darwin":
        from vibebridge.capabilities import _build_darwin
        return _build_darwin()
    module = __import__(f"vibebridge.platforms.{name}", fromlist=["x"])
    return module.build_capabilities()


@pytest.mark.parametrize("script", FORBIDDEN_EVERYWHERE)
def test_the_shared_rule_refuses_what_the_product_denies(script):
    with pytest.raises(CapabilityError) as err:
        refuse_if_dangerous(script)
    assert "shell" in str(err.value) or "нажат" in str(err.value)


class _Loud:
    """Раннер, который кричит, если до него дошли. Отказ обязан случиться
    ДО исполнения, а не вместо результата."""

    def run(self, *a, **kw):
        raise AssertionError("скрипт доехал до исполнителя")


@pytest.mark.parametrize("script", FORBIDDEN_EVERYWHERE)
@pytest.mark.parametrize("pack", ["darwin", "windows"])
def test_each_pack_actually_calls_the_shared_rule(pack, script):
    """Первая версия этого файла проверяла САМО правило и не проверяла, что
    паки его зовут. Подсадка это и показала: вернув Windows его собственный
    список, я получил исполненный `SendKeys` при зелёном гейте — ровно тот
    класс F-6, ради которого гейт и писался.

    Linux здесь не участвует намеренно: его `automation` — заглушка, она
    отказывает ВСЕГДА и потому ничего не доказывает про общее правило.
    """
    handler = _pack(pack)["automation"].handler
    with pytest.raises(CapabilityError):
        handler(_Loud(), {"script": script})


def test_a_platform_can_only_add_to_the_rule_not_replace_it():
    """Пак добавляет свои имена — и НЕ может ослабить общее. Иначе фикс
    снова ляжет в одну копию из трёх."""
    with pytest.raises(CapabilityError):
        refuse_if_dangerous('do shell script "x"', extra=("своё-имя",))
    with pytest.raises(CapabilityError):
        refuse_if_dangerous("трогает своё-имя", extra=("своё-имя",))
    refuse_if_dangerous("tell application \"Music\" to play")  # безобидное живёт


def test_all_three_packs_promise_the_robot_the_same_tools():
    """Робот видит ОДИН набор имён независимо от того, на чём стоит мост.
    Разойдись они — и скил робота стал бы верным на одной платформе из трёх.
    """
    packs = {name: _pack(name) for name in ("darwin", "linux", "windows")}
    names = {name: set(caps) for name, caps in packs.items()}
    base = names["darwin"]
    for name, got in names.items():
        assert got == base, (
            f"пак «{name}» обещает другой набор инструментов: "
            f"лишние {sorted(got - base)}, недостающие {sorted(base - got)}")


def test_the_class_of_a_tool_is_the_same_everywhere():
    """READ на одной платформе и ACT на другой — это разное согласие за
    одним именем."""
    packs = {name: _pack(name) for name in ("darwin", "linux", "windows")}
    base = {n: cap.tool_class for n, cap in packs["darwin"].items()}
    for name, caps in packs.items():
        for tool, cap in caps.items():
            assert cap.tool_class is base[tool], (
                f"«{tool}» в паке «{name}» другого класса: "
                f"{cap.tool_class} против {base[tool]}")


def test_automation_shows_the_owner_what_it_will_run_everywhere():
    """Самый опасный инструмент нёс самую бессодержательную строку согласия.
    Владелец обязан видеть СКРИПТ — на любой платформе."""
    # Собираем ВСЕ расхождения: падение на первом скрыло бы второе, и я
    # починил бы Linux, не узнав про Windows (так и вышло при F-6).
    молчат = []
    for name in ("darwin", "linux", "windows"):
        cap = _pack(name)["automation"]
        if "{script}" not in cap.summary_template:
            молчат.append(f"{name}: «{cap.summary_template}»")
    assert not молчат, (
        "строка согласия не показывает скрипт: " + "; ".join(молчат))
