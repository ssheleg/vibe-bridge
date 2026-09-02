"""Шов «оболочка → payload» объявлен и не расходится с кодом (F-9).

Оболочка и payload обновляются порознь: payload прилетает сам, оболочка —
только новым подписанным .app (ADR-0006). Значит запущенный payload всегда
может оказаться новее оболочки, в которой бежит.

Держалось это на константе `SHELL_MIN = "0.1.0"` в сборщике, которую надо
было ВСПОМНИТЬ поднять, и на `vbboot.__all__ = ["layout"]`, который не
упоминал `runner`, хотя payload импортирует `runner.shell_version`. Проверок
не было ни одной: нехватка вылезла бы `AttributeError` из фонового потока.

Здесь проверяется не «константа равна константе», а три СВОЙСТВА:
объявление совпадает с тем, что код зовёт; объявленное в оболочке есть;
старая оболочка получает фразу, а не traceback.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vibebridge import shell_api  # noqa: E402


def _seam_used_by_the_payload() -> set[str]:
    """Что код payload РЕАЛЬНО берёт у оболочки, прочитано по AST.

    Именно по AST, а не грепом: первая версия этого перечисления собиралась
    регуляркой и записала в шов `layout.py` — упоминание ФАЙЛА из
    комментария `update.py`. Проверка на живой оболочке это и показала.
    """
    used: set[str] = set()
    for path in sorted((ROOT / "vibebridge").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "vbboot":
                    for a in node.names:            # from vbboot import layout
                        aliases[a.asname or a.name] = f"vbboot.{a.name}"
                elif module.startswith("vbboot."):
                    for a in node.names:            # from vbboot.runner import x
                        used.add(f"{module}.{a.name}")
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("vbboot"):
                        aliases[a.asname or a.name.split(".")[0]] = "vbboot"
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in aliases
                    and not node.attr.startswith("__")):
                target = f"{aliases[node.value.id]}.{node.attr}"
                if target.count(".") > 1:           # vbboot.layout.prune
                    used.add(target)
    return used


def _declared() -> set[str]:
    return {f"{module}.{name}"
            for module, names in shell_api.REQUIRED.items()
            for name in names}


def test_the_declaration_matches_what_the_payload_actually_calls():
    """Взял у оболочки новое имя — объяви его. Иначе `SHELL_MIN` останется
    прежним, обновление установится на оболочку без этого имени, и мост
    упадёт `AttributeError` там, где должен был сказать «нужен новый .app».
    """
    used, declared = _seam_used_by_the_payload(), _declared()
    assert used <= declared, (
        "payload зовёт незаявленное у оболочки: "
        f"{sorted(used - declared)} — допишите в shell_api.REQUIRED "
        "с версией оболочки, в которой имя появилось")
    assert declared <= used, (
        "в шве заявлено то, чего код не зовёт: "
        f"{sorted(declared - used)} — лишнее требование поднимает "
        "SHELL_MIN и отсекает исправные оболочки")


@pytest.mark.parametrize("full", sorted(_declared()))
def test_every_declared_name_exists_in_the_shell(full):
    """Объявление — обещание про оболочку, а не про себя."""
    module_name, _, attr = full.rpartition(".")
    module = __import__(module_name, fromlist=["x"])
    assert hasattr(module, attr), f"оболочка не даёт {full}"


def test_the_shell_names_runner_in_its_own_surface():
    """`__all__` перечислял один `layout`, тогда как payload берёт и
    `runner`. Поверхность, о которой знает только вызывающий, — не шов."""
    import vbboot
    for module in {m.split(".")[1] for m in shell_api.REQUIRED}:
        assert module in vbboot.__all__, (
            f"payload берёт vbboot.{module}, а оболочка его не объявляет")


def test_shell_min_is_computed_from_the_seam_not_typed_twice():
    """Сборщик не имеет права помнить СВОЁ значение: константа, которую надо
    вспомнить поднять, и была всем содержанием F-9."""
    source = (ROOT / "scripts" / "build_payload.py").read_text(encoding="utf-8")
    assert "SHELL_MIN = \"" not in source and "SHELL_MIN = '" not in source, (
        "в сборщике снова своя копия SHELL_MIN — она разойдётся со швом")
    assert "from vibebridge.shell_api import SHELL_MIN" in source


def test_adding_a_requirement_raises_the_floor_by_itself():
    """Свойство, ради которого значение считается: новое требование из более
    новой оболочки поднимает пол само, без чьей-либо памяти."""
    было = shell_api._shell_min()
    original = shell_api.REQUIRED
    try:
        shell_api.REQUIRED = {**original,
                              "vbboot.runner": {"shell_version": "0.1.0",
                                                "новое": "0.30.0"}}
        assert shell_api._shell_min() == "0.30.0", (
            f"пол не поднялся: было {было}, осталось "
            f"{shell_api._shell_min()}")
    finally:
        shell_api.REQUIRED = original
    assert shell_api._shell_min() == было


class _OldShell:
    """Оболочка, в которой нужного имени ещё нет. Настоящую старую в тест не
    поставить, а проверять надо именно её поведение."""

    def __init__(self, without: str):
        self._module, _, self._name = without.rpartition(".")

    def __call__(self, name: str):
        module = __import__(name, fromlist=["x"])
        if name != self._module:
            return module
        class _Trimmed:
            def __getattr__(inner, attr):           # noqa: N805
                if attr == self._name:
                    raise AttributeError(attr)
                return getattr(module, attr)
        return _Trimmed()


@pytest.mark.parametrize("absent", sorted(_declared()))
def test_an_old_shell_gets_a_sentence_not_an_attributeerror(absent):
    with pytest.raises(shell_api.ShellTooOld) as err:
        shell_api.require_shell(importer=_OldShell(absent))
    text = str(err.value)
    assert "нужен новый .app" in text, text
    assert absent in text, f"фраза не называет, чего не хватает: {text}"


def test_a_shell_without_vbboot_at_all_is_still_a_sentence():
    """Крайний случай: оболочки нет вовсе (запуск не из .app). Тоже фраза."""
    def _absent(name: str):
        raise ImportError(name)
    with pytest.raises(shell_api.ShellTooOld) as err:
        shell_api.require_shell(importer=_absent)
    assert "нужен новый .app" in str(err.value)
