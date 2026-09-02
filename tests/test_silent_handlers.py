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
