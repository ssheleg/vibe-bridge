"""«Написано, протестировано, не вызвано» — второй самый частый класс.

Три случая, каждый найденный владельцем или аудитом, не набором: `prune`
чистил старые payload'ы и не вызывался; `migrate_from_state` переносил
настройки и не вызывался (мост ушёл с loopback на tailnet-адрес и приложение
сломалось целиком); `top_up` дописывал новые ключи в существующий конфиг и не
вызывался. Каждый закрыт СВОИМ тестом, и все три теста — grep по точному
тексту исходника, который разоружает любое переформатирование (A-37).

Здесь проверка на класс. Она находит функции, чьё имя не встречается в
отгружаемом коде НИ РАЗУ — ни вызовом, ни ссылкой. Тест, зовущий такую
функцию, ничего не доказывает: он проверяет код, который в продукте не
исполняется.

Ссылка, а не вызов — намеренно: обработчик маршрута передаётся в `Route(...)`
по имени, обработчик способности лежит в таблице, и всё это законные способы
«вызвать» без скобок.

Исключения перечислены поимённо и с причиной. Список, куда можно дописать
что угодно без объяснения, — это не гейт.
"""
from __future__ import annotations

import ast
from pathlib import Path

import vibebridge

REPO = Path(vibebridge.__file__).resolve().parents[1]
#: `scripts/` входит в область: `build_payload.py` — часть выпуска, и функция,
#: которую зовёт только он, вызывается по-настоящему.
ROOTS = (REPO / "vibebridge", REPO / "vbboot", REPO / "scripts")

#: Имя → почему его не зовёт наш код. Каждая строка — обещание, что автор
#: посмотрел, а не отмахнулся.
EXEMPT = {
    "acceptsFirstMouse_":
        "селектор Cocoa: его зовёт AppKit, когда мышь приходит в неактивное "
        "окно. Без него первый клик по питомцу съедался (измерено 2026-08-31)",
    "userContentController_didReceiveScriptMessage_":
        "селектор WKScriptMessageHandler: его зовёт WebKit, когда страница "
        "шлёт сообщение в нативную часть",
    "main":
        "точка входа процесса — её зовёт интерпретатор через __main__",
}


def _definitions_and_references() -> tuple[dict[str, list[str]], set[str]]:
    defs: dict[str, list[str]] = {}
    refs: set[str] = set()
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            rel = path.relative_to(REPO)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defs.setdefault(node.name, []).append(
                        f"{rel}:{node.lineno}")
                elif isinstance(node, ast.Name):
                    refs.add(node.id)
                elif isinstance(node, ast.Attribute):
                    refs.add(node.attr)
    return defs, refs


def test_nothing_shipped_is_written_but_never_reached():
    defs, refs = _definitions_and_references()
    orphans = {
        name: where for name, where in defs.items()
        if name not in refs
        and not name.startswith("__")
        and name not in EXEMPT
    }
    assert not orphans, (
        "функция есть в отгружаемом коде и не вызывается им ни разу — "
        "подключите её или удалите (тест, который её зовёт, ничего не "
        f"доказывает): {orphans}")
    # Канарейка: пустой разбор выглядит как успех — тот же способ стать
    # бесполезной, что у A-32 и A-36.
    assert len(defs) > 100, f"разбор нашёл всего {len(defs)} функций — он сломан"


def test_every_exemption_names_a_reason():
    """Список исключений без причин — это не гейт, а его отключение."""
    for name, why in EXEMPT.items():
        assert len(why) > 30, f"{name}: причина слишком короткая, чтобы быть ей"


def test_the_check_actually_sees_an_orphan(tmp_path, monkeypatch):
    """Подсадка внутри проверки: без неё «зелено» значит только то, что никто
    не смотрел."""
    import tests.test_unreachable_code as mod

    lonely = tmp_path / "lonely.py"
    lonely.write_text("def никто_не_зовёт():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOTS", (tmp_path,))
    monkeypatch.setattr(mod, "REPO", tmp_path)
    defs, refs = mod._definitions_and_references()
    assert "никто_не_зовёт" in defs and "никто_не_зовёт" not in refs
