"""Ассерт кодирует СВОЙСТВО, а не текущее значение (A-43).

Три теста проверяли строки исходника вместе с пробелами: `VB_DUR = 220`,
`width:300px`, `moved > 4`. Каждый ломался от переформатирования и молчал
при смене поведения — то есть срабатывал ровно наоборот тому, зачем написан.
Докстрока при этом называла настоящее свойство: «движение не длиннее 300 мс»,
«пузырь не перерастает окно», «драг не читается как клик».

Проверка узкая и потому честная: она ловит ОДНУ форму — ассерт на строку
вида «имя = число», то есть кусок исходника со значением внутри. Отличить
«значение вместо свойства» вообще машина не может, и притворяться, будто
может, значило бы завести гейт, которому не верят.

Где значение И ЕСТЬ свойство — пак задал размер до пикселя, — исключение
названо с причиной.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

TESTS = Path(__file__).parent
VALUE_SHAPE = re.compile(r"^[A-Za-z_][\w.-]*\s*[:=]\s*-?\d")

#: Строка → почему здесь значение и есть свойство.
VALUE_IS_THE_PROPERTY = {
    "height:4px":
        "пак задаёт таймер-бар согласия до пикселя: «4px, заливка --warn, "
        "дренаж слева направо». Четыре — это и есть требование, а не "
        "текущее состояние кода",
    "bottom:0":
        "пак говорит «вкладки снизу» — это про достижимость большим пальцем. "
        "Прижатость к низу и есть свойство; иначе сформулировать её не через "
        "`bottom` нельзя, а «где-то внизу» проверить невозможно (V-4)",
    "grid-template-columns:1fr":
        "пак говорит «до 900px одна колонка». Одна колонка и есть свойство, "
        "а не текущее число (V-4)",
    "outline:2px":
        "пак задаёт кольцо фокуса до пикселя: «2px --accent, offset 2px — на "
        "всём». Двойка — требование, а не состояние кода (V-5)",
    "outline-offset:2px":
        "вторая половина того же требования пака: без отступа кольцо липнет "
        "к кнопке и перестаёт читаться на тёмной заливке (V-5)",
    "max-width:100%":
        "«не шире окна» и есть свойство. Абсолютное число здесь было бы "
        "ВТОРОЙ копией `desktop.PET_SIZE`, то есть ровно тем дефектом, от "
        "которого проценты и защищают (V-6)",
}


def _value_assertions() -> list[tuple[str, int, str]]:
    out = []
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            for sub in ast.walk(node.test):
                if (isinstance(sub, ast.Compare)
                        and any(isinstance(o, ast.In) for o in sub.ops)
                        and isinstance(sub.left, ast.Constant)
                        and isinstance(sub.left.value, str)
                        and VALUE_SHAPE.match(sub.left.value.strip())):
                    out.append((path.name, node.lineno, sub.left.value))
    return out


def test_no_assertion_quotes_a_value_from_the_source():
    bad = [f"{name}:{line} — {text!r}"
           for name, line, text in _value_assertions()
           if text.strip() not in VALUE_IS_THE_PROPERTY]
    assert not bad, (
        "ассерт цитирует значение из исходника: он сломается от "
        "переформатирования и промолчит при смене поведения. Проверяйте "
        "свойство из докстроки — или объясните исключение в "
        f"VALUE_IS_THE_PROPERTY: {bad}")


def test_every_exemption_names_a_reason():
    for text, why in VALUE_IS_THE_PROPERTY.items():
        assert len(why) > 40, f"{text}: причина слишком коротка, чтобы быть ей"


def test_the_check_sees_the_shape_it_hunts(tmp_path, monkeypatch):
    """Подсадка внутри проверки — иначе «зелено» значит только, что никто не
    смотрел."""
    import tests.test_assertion_quality as mod

    probe = tmp_path / "test_probe.py"
    probe.write_text('def test_x():\n    assert "VB_DUR = 220" in "…"\n',
                     encoding="utf-8")
    monkeypatch.setattr(mod, "TESTS", tmp_path)
    found = mod._value_assertions()
    assert found and found[0][2] == "VB_DUR = 220"

    # ...а ассерт на СВОЙСТВО этой формой не является
    ok = tmp_path / "test_ok.py"
    ok.write_text('def test_y():\n    assert "overflow-y:auto" in "…"\n',
                  encoding="utf-8")
    probe.unlink()
    assert mod._value_assertions() == []
