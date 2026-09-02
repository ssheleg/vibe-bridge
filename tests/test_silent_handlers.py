"""Проглоченная ошибка — самый частый класс дефектов этого проекта.

Ретро называет его «третьим случаем», и каждый прошлый закрыт СВОИМ тестом:
уведомление, выдававшее провал за успех; позиция питомца, не сохранявшаяся
молча; канал сообщений виджета, глотавший всё. Три теста на три случая —
и ни одного на класс, поэтому четвёртый находил не набор, а владелец (A-36).

Здесь механическая проверка. Она не запрещает молчать: иногда молчание —
правильный ответ, и `try: os.unlink(...) except OSError: pass` в конце
чтения файла не нуждается ни в каком обработчике. Она требует СКАЗАТЬ,
почему молчание правильно, — рядом, в одной строке от `except`.

Формулировка маркера намеренно русская и намеренно длинная: `# молчим:`
нельзя набрать случайно, и он не выглядит как шум вроде `# noqa`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import vibebridge

MARKER = "молчим:"
ROOTS = (Path(vibebridge.__file__).parent,
         Path(vibebridge.__file__).resolve().parents[1] / "vbboot")


def _silent_handlers(path: Path) -> list[tuple[int, str]]:
    """Обработчики, тело которых — только `pass` или `...`."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.ExceptHandler) or len(node.body) != 1:
            continue
        only = node.body[0]
        silent = isinstance(only, ast.Pass) or (
            isinstance(only, ast.Expr) and isinstance(only.value, ast.Constant)
            and only.value.value is Ellipsis)
        if not silent:
            continue
        # Объяснение ищем в строке `except`, в теле и в двух строках над —
        # там, где его пишет человек.
        window = lines[max(0, node.lineno - 3):only.lineno]
        out.append((node.lineno, "\n".join(window)))
    return out


def test_every_swallowed_error_says_why_it_is_allowed_to_be_silent():
    unexplained: list[str] = []
    checked = 0
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            for lineno, window in _silent_handlers(path):
                checked += 1
                if MARKER not in window:
                    rel = path.relative_to(root.parent)
                    unexplained.append(f"{rel}:{lineno}")
    assert not unexplained, (
        "молчащий обработчик без объяснения — допишите рядом комментарий "
        f"«# {MARKER} …»: " + ", ".join(unexplained))
    # Канарейка на сам разбор: молчаливый ноль выглядит как успех, а значит
    # это второй способ для проверки стать бесполезной (первый — A-32).
    assert checked > 0, "разбор не нашёл НИ ОДНОГО обработчика — он сломан"


def test_the_check_actually_sees_an_unexplained_handler(tmp_path):
    """Подсадка внутри самой проверки: без неё «зелено» значит только то, что
    никто не смотрел."""
    bad = tmp_path / "bad.py"
    bad.write_text("try:\n    x = 1\nexcept OSError:\n    pass\n",
                   encoding="utf-8")
    assert _silent_handlers(bad) and MARKER not in _silent_handlers(bad)[0][1]

    good = tmp_path / "good.py"
    good.write_text("try:\n    x = 1\nexcept OSError:\n"
                    f"    pass  # {MARKER} причина\n", encoding="utf-8")
    assert MARKER in _silent_handlers(good)[0][1]


# ── A-38: экранировщик текста в позиции атрибута ───────────────────────────

#: Подстановки, законные без экранировщика атрибута, — каждая с причиной.
#: Список без причин — это не гейт, а его отключение (см. A-37).
SAFE_ATTR_VALUES = {
    "i": "индекс цикла: число, которое породил наш же `map`, а не данные",
    "state": "имя состояния из нашей собственной таблицы MASCOT_STATES",
    "cls": "имя css-класса из нашей же ветки, не из данных",
    "u": "уже прошло vbEscAttr строкой выше",
    "size": "число пикселей, вычисленное нами и не приходящее извне",
    "s.ink":
        "имя css-переменной из нашей таблицы MASCOT_STATES; скин выбирает "
        "функцию рисования, а не значения — снаружи сюда попасть нечем",
    "busy ? \"Робот думает…\" : \"Сказать роботу…\"":
        "две наши константы, выбор между ними",
}


def test_no_text_escaper_lands_in_an_attribute_value():
    """A-38: `esc()` строит текст (`textContent` → `innerHTML`) и НЕ трогает
    кавычку. В позиции атрибута `x" onmouseover="alert(1)` закрывает атрибут
    и открывает обработчик. Хуже всего это было в `href`/`src` медиа: URL
    туда присылает РОБОТ.
    """
    from tests.webui_rules import attribute_interpolations

    bad: list[str] = []
    seen = 0
    for page in ("index.html", "mascot.html", "mascot.js"):
        for lineno, expr in attribute_interpolations(page):
            seen += 1
            if expr in SAFE_ATTR_VALUES:
                continue
            if "escAttr" in expr or "vbEscAttr" in expr:
                continue
            bad.append(f"{page}:{lineno} — {expr}")
    assert not bad, (
        "подстановка в значение атрибута без экранировщика АТРИБУТА "
        "(`escAttr`/`vbEscAttr`): " + "; ".join(bad))
    assert seen > 5, f"разбор нашёл всего {seen} подстановок — он сломан"


def test_the_attribute_escaper_actually_escapes_the_quote():
    """Свойство, а не наличие функции: текстовый экранировщик кавычку
    пропускает, и именно это делало его непригодным."""
    import re
    from pathlib import Path

    import vibebridge
    js = (Path(vibebridge.__file__).parent / "webui" / "mascot.js").read_text()
    body = js.split("function vbEscAttr(", 1)[1].split("\n}", 1)[0]
    for ch in ('"', "&", "<", ">"):
        assert re.search(rf'replace\(/\{re.escape(ch)}/g|replace\(/{re.escape(ch)}/g',
                         body) or f'/{ch}/g' in body, f"не экранируется {ch}"
    assert "&quot;" in body, "кавычка не превращается в сущность"


def test_every_safe_attribute_value_names_a_reason():
    for expr, why in SAFE_ATTR_VALUES.items():
        assert len(why) > 25, f"{expr}: причина слишком короткая, чтобы быть ей"
