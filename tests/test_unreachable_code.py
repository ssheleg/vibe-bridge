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
import sys
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


# --------------------------------------------------------------------------
# Тот же класс, но для КОНСТАНТ и КЛАССОВ — их прежняя проверка не видела,
# и именно поэтому пять сирот дожили до аудита: `RELEASE_FEED`,
# `DOWNLOAD_BASE`, `RobotUnconfigured`, `DEFAULT_SIZE`, `mascot.STATES`
# (F-13). Мёртвая константа хуже мёртвой функции: она выглядит настройкой,
# и следующий читатель поменяет её, ожидая эффекта.
# --------------------------------------------------------------------------

#: Имя → почему оно живёт без ссылок в нашем коде.
EXEMPT_NAMES = {
    "SHELL_MIN":
        "его читает сборщик payload через импорт из `shell_api` — ссылка "
        "есть, но в другом репозиторном слое; удалить нельзя, это пол "
        "совместимости оболочки",
}


def _module_level_names() -> tuple[dict[str, tuple[str, str]], dict[str, int]]:
    """(имя → (файл, вид)), (имя → сколько раз упомянуто во ВСЕЙ отгрузке)."""
    defined: dict[str, tuple[str, str]] = {}
    seen: dict[str, int] = {}
    trees = {}
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            trees[path] = ast.parse(path.read_text(encoding="utf-8"))
    for path, tree in trees.items():
        rel = str(path.relative_to(REPO))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                defined.setdefault(node.name, (f"{rel}:{node.lineno}", "class"))
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id.isupper():
                        defined.setdefault(tgt.id,
                                           (f"{rel}:{node.lineno}", "const"))
            elif (isinstance(node, ast.AnnAssign)
                  and isinstance(node.target, ast.Name)
                  and node.target.id.isupper()):
                defined.setdefault(node.target.id,
                                   (f"{rel}:{node.lineno}", "const"))
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                seen[node.id] = seen.get(node.id, 0) + 1
            elif isinstance(node, ast.Attribute):
                seen[node.attr] = seen.get(node.attr, 0) + 1
            elif isinstance(node, ast.alias):
                seen[node.name] = seen.get(node.name, 0) + 1
    return defined, seen


def test_no_constant_or_class_is_defined_and_never_referenced():
    """Порог зависит от ВИДА, и это не придирка: присваивание константы само
    даёт узел `Name`, а определение класса не даёт ничего. Спутав их, первая
    версия этого счёта объявила сиротами восемь живых классов."""
    defined, seen = _module_level_names()
    orphans = []
    for name, (where, kind) in sorted(defined.items()):
        if name in EXEMPT_NAMES or name in EXEMPT:
            continue
        floor = 1 if kind == "const" else 0
        if seen.get(name, 0) <= floor:
            orphans.append(f"{name} ({kind}) — {where}")
    assert not orphans, (
        "объявлено и ни разу не упомянуто: " + "; ".join(orphans) +
        ". Мёртвая константа выглядит настройкой, и следующий читатель "
        "поменяет её, ожидая эффекта")


def test_every_name_exemption_names_a_reason():
    for name, reason in EXEMPT_NAMES.items():
        assert len(reason) > 30, f"исключение «{name}» без объяснения"


def test_this_check_sees_a_planted_orphan(tmp_path, monkeypatch):
    """Канарейка: гейт, который ничего не видит, выглядит как успех."""
    fake = tmp_path / "vibebridge"
    fake.mkdir()
    (fake / "x.py").write_text("ОРФАН_КОНСТАНТА = 1\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOTS", (fake,))
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    defined, seen = _module_level_names()
    assert "ОРФАН_КОНСТАНТА" in defined
    assert seen.get("ОРФАН_КОНСТАНТА", 0) <= 1


def test_the_bundle_layout_is_known_in_exactly_one_place():
    """Устройство подписанного бандла — ОДИН факт, и живёт он в `shell_api`.

    Он был приватным символом веб-слоя (`web._bundle_resources`), который
    импортировала вся ветка автообновления, а трей писал тот же обход
    `parents` заново: якорь доверия обновлений доставался через деталь модуля
    HTTP-маршрутов (F-8). Подсадка «трей снова пишет свой обход» проходила
    зелёной, пока этой проверки не было.
    """
    # Тоже по AST: строковая КОНСТАНТА "Resources" рядом с обращением к
    # `.parents`. Докстринг — это одна большая константа, а не литерал
    # "Resources", поэтому проза сюда не попадает.
    знают = []
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            if path.name in ("shell_api.py", "runner.py"):
                continue          # `shell_api` — дом факта; `runner` — оболочка
            tree = ast.parse(path.read_text(encoding="utf-8"))
            literal = any(isinstance(n, ast.Constant) and n.value == "Resources"
                          for n in ast.walk(tree))
            walk = any(isinstance(n, ast.Attribute) and n.attr == "parents"
                       for n in ast.walk(tree))
            if literal and walk:
                знают.append(str(path.relative_to(REPO)))
    assert not знают, (
        "устройство бандла переписано заново в: " + ", ".join(знают) +
        " — зовите `shell_api.bundle_resources()`")


def test_no_handler_calls_another_handler_and_parses_its_body_back():
    """Обработчик — адаптер над действием, а не вызываемая функция.

    `api_wizard_prepare` звал `api_wizard_pairing_start` и разбирал обратно
    его тело: побочный эффект «выпущен одноразовый токен» прятался внутри
    выражения «получить значение», а HTTP-сериализация оказывалась в пути
    между двумя своими же функциями (F-11). Действие должно быть названо.
    """
    вызовы = []
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            handlers = {n.name for n in ast.walk(tree)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and n.name.startswith("api_")}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (node.func.id if isinstance(node.func, ast.Name)
                        else getattr(node.func, "attr", None))
                if name in handlers:
                    вызовы.append(
                        f"{path.relative_to(REPO)}:{node.lineno} → {name}()")
    assert not вызовы, (
        "обработчик зовёт обработчик: " + "; ".join(вызовы) +
        " — назовите действие и зовите его, а маршрут пусть остаётся "
        "адаптером")


def test_no_response_body_is_parsed_back_by_our_own_code():
    """`json.loads(bytes(...body))` над собственным ответом — верный признак
    того, что действие не названо."""
    # Разбор по AST, а не по тексту: первая версия искала подстроки и
    # поймала ДОКСТРИНГ, который объясняет этот самый фикс. Проза,
    # прочитанная как код, — четвёртый случай за сессию; у AST такого
    # различия нет по построению.
    следы = []
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "loads"):
                    continue
                if any(isinstance(inner, ast.Attribute) and inner.attr == "body"
                       for arg in node.args for inner in ast.walk(arg)):
                    следы.append(
                        f"{path.relative_to(REPO)}:{node.lineno}")
    assert not следы, (
        "своё же тело ответа разбирается обратно в: " + ", ".join(следы))
