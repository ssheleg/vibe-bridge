"""Пауза глушит ПРИСУТСТВИЕ, а не только эффект (U-10).

Принцип 3 визии: «kill switch делает устройство неотличимым от выключенного;
мы не ведём с агентом переговоров о том, почему нельзя, — для него устройства
просто нет».

До 2026-09-02 пауза проверялась ровно в одном месте — перед `notify`. Поллер
статуса стучался к роботу каждые десять секунд, SSE-консьюмер держал открытое
соединение, а панель дёргала робота напрямую при каждом открытии. С точки
зрения робота выключенный компьютер звонил ему каждые десять секунд и держал
поток.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import vibebridge

REPO = Path(vibebridge.__file__).resolve().parents[1]
WEB = Path(vibebridge.__file__).parent / "web.py"
sys.path.insert(0, str(REPO))


def _function(name: str) -> ast.AST:
    tree = ast.parse(WEB.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name):
            return node
    raise AssertionError(f"функции {name} нет — путь наружу переименован?")


def _calls_out(node: ast.AST) -> list[str]:
    """Обращения к роботу внутри функции: `robot.<что-то>()`."""
    return [c.func.attr for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
            and isinstance(c.func.value, ast.Name) and c.func.value.id == "robot"]


def _mentions_pause(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Attribute) and n.attr == "paused"
               and isinstance(n.value, ast.Name) and n.value.id == "consent"
               for n in ast.walk(node))


def _unguarded_calls(node: ast.AST) -> list[str]:
    """Вызовы к роботу, НАД которыми нет ветки, спрашивающей про паузу.

    Первая версия проверяла, что слово `paused` встречается в функции хоть
    где-нибудь. Подсадка это и показала: убрал `not consent.paused` из
    условия поллера — гейт остался зелёным, потому что `was_paused =
    consent.paused` строкой ниже никуда не делось. Наличие имени не равно
    свойству «вызов стоит под этим условием»; та же подмена уже подводила в
    гейте про слово питомца.
    """
    родитель: dict[int, ast.AST] = {}
    for parent in ast.walk(node):
        for child in ast.iter_child_nodes(parent):
            родитель[id(child)] = parent

    плохие = []
    for call in ast.walk(node):
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "robot"):
            continue
        # Сторожей две формы, и обе законны: ветка НАД вызовом и ранний
        # выход ПЕРЕД ним. Первая версия знала только первую и покраснела на
        # `if consent.paused: return …` — то есть на более ясной из двух.
        сторож, узел = False, call
        while id(узел) in родитель:
            родительский = родитель[id(узел)]
            if isinstance(родительский, (ast.If, ast.While)) \
                    and _mentions_pause(родительский.test):
                сторож = True
                break
            # ВАЖНО: проверка ВНУТРИ `async for` сторожем не считается.
            # `async for raw in robot.events()` открывает соединение ДО
            # первого элемента, и `if paused: break` в теле срабатывает уже
            # после того, как робот увидел подключение. Подсадка это и
            # показала: убрал паузу из сторожевого условия — гейт остался
            # зелёным, потому что `break` в теле никуда не делся.
            # Ранний выход: сосед ВЫШЕ по телу спрашивает про паузу и уходит.
            тело = getattr(родительский, "body", None)
            if isinstance(тело, list) and узел in тело:
                для_проверки = тело[:тело.index(узел)]
                if any(isinstance(s, ast.If) and _mentions_pause(s.test)
                       and any(isinstance(x, (ast.Return, ast.Break,
                                              ast.Continue))
                               for x in ast.walk(s))
                       for s in для_проверки):
                    сторож = True
                    break
            узел = родительский
        if not сторож:
            плохие.append(f"robot.{call.func.attr}() (строка {call.lineno})")
    return плохие


def test_every_outbound_path_asks_whether_the_bridge_is_paused():
    """Три пути наружу, и каждый обязан спросить про паузу."""
    for name in ("_robot_poller", "_robot_event_consumer", "api_robot_status"):
        node = _function(name)
        зовёт = _calls_out(node)
        assert зовёт, f"{name} больше не ходит к роботу — проверка устарела"
        свободные = _unguarded_calls(node)
        assert not свободные, (
            f"{name}: вызов наружу не стоит под проверкой паузы — "
            f"{'; '.join(свободные)}. Для робота компьютер остаётся включённым")


def test_the_panel_says_the_status_is_stale_rather_than_guessing():
    """Перестать спрашивать и показывать старое как текущее — это подмена.
    Стереть статус тоже нельзя: «офлайн» было бы неправдой, мы просто не
    спрашивали."""
    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text(
        encoding="utf-8")
    assert "stale_paused" in page, (
        "панель не знает, что на паузе показанное — последнее известное")
    assert "не спрашивает робота" in page


def test_the_snapshot_is_kept_not_wiped_while_paused():
    """Свойство: на паузе снимок помечается, а не обнуляется."""
    node = _function("api_robot_status")
    src = ast.unparse(node)
    assert "stale_paused" in src, "ответ панели не помечен как несвежий"
    assert "robot_state" in src, "снимок не отдаётся — панель увидит пустоту"
