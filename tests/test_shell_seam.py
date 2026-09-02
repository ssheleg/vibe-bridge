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


# --------------------------------------------------------------------------
# Обратное направление: что оболочка ПЕРЕДАЁТ payload (B-45).
#
# Лечится оно иначе, и в этом весь смысл различения. Отсутствие ВЫЗОВА —
# `AttributeError`, отказ уместен. Отсутствие ПЕРЕДАЧИ не роняет ничего, и
# отказывать нельзя: пол ради косметики отсёк бы исправные оболочки от всего
# payload. Значит единственная защита — сказать вслух, и проверять надо
# именно то, что сказано.
# --------------------------------------------------------------------------


def test_the_shell_in_this_repo_passes_everything_it_declares():
    """Оболочка репозитория реально передаёт объявленное. Верни кто-нибудь
    `run()` вместо `run(chosen)` — и фича умрёт на всех будущих .app молча,
    ровно как она мертва на 0.19.0 сейчас."""
    import ast
    source = (ROOT / "vbboot" / "__main__.py").read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(source))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "run"]
    assert calls, "оболочка вообще не зовёт payload"
    passed = {a.id for call in calls for a in call.args
              if isinstance(a, ast.Name)}
    passed |= {kw.arg for call in calls for kw in call.keywords}
    for name in shell_api.SHELL_PROVIDES:
        assert name in passed, (
            f"оболочка объявляет, что передаёт «{name}», а зовёт "
            f"run({', '.join(sorted(passed)) or ''}) — фича payload умрёт "
            f"молча")


def test_the_payload_can_receive_everything_the_shell_passes():
    """Вторая половина того же шва: `run` обязан ПРИНЯТЬ объявленное."""
    import inspect

    from vibebridge.app import run
    parameters = inspect.signature(run).parameters
    for name in shell_api.SHELL_PROVIDES:
        assert name in parameters, f"`run` не принимает «{name}»"
        assert parameters[name].default is None, (
            f"«{name}» без умолчания — старая оболочка уронит мост TypeError'ом "
            f"вместо деградации")


def test_what_was_not_passed_is_named_with_its_consequence():
    """Не «что-то не так», а ЧТО именно теперь неточно."""
    gaps = shell_api.not_provided(chosen=None)
    assert gaps == ["chosen"]
    text = shell_api.degradation(gaps, "0.19.0")
    assert "0.19.0" in text, text
    assert "по догадке" in text, text
    assert "мост работает" in text, "деградация — не отказ, это должно быть видно"
    assert shell_api.not_provided(chosen=object()) == []


def test_an_undeclared_name_is_refused_rather_than_ignored():
    """Опечатка в имени не имеет права выглядеть как «всё передано»."""
    with pytest.raises(KeyError):
        shell_api.not_provided(chose=None)


def test_the_panel_marks_a_guess_as_a_guess():
    """Панель показывала догадку тем же шрифтом, что ответ (B-45)."""
    from vibebridge import web
    assert web.source_note() == "", "вне бандла источник ТОЧЕН — это не догадка"

    class _Bundle:
        pass
    saved_res, saved_chosen = web._bundle_resources, web._chosen
    try:
        web._bundle_resources = lambda: _Bundle()
        web._chosen = None
        note = web.source_note()
        assert "по догадке" in note, note
        web._chosen = type("C", (), {"source": "seed"})()
        assert web.source_note() == "", "ответ есть — оговорки быть не должно"
    finally:
        web._bundle_resources, web._chosen = saved_res, saved_chosen


def test_the_panel_renders_the_note_when_there_is_one():
    """Поле в JSON, которое не рисуется, — это молчание с лишним шагом."""
    page = (ROOT / "vibebridge" / "webui" / "index.html").read_text(
        encoding="utf-8")
    assert "v.source_note" in page, "панель не читает source_note"
    assert "esc(v.source_note)" in page, "оговорка вставляется без экранирования"
