"""Экран связки: связка проверяется ФАКТАМИ, а не обещанием (U-7).

SCR-07 описывает семь состояний. До 2026-09-02 не было построено ни одного:
владелец записывал карту и получал фразу «когда робот свяжется, мост скажет —
и карточка „Робот“ оживёт». Ни экрана ожидания, ни «робот найден», ни
чеклиста, ни диагностики по таймауту.

Четвёртый пункт чеклиста — «согласие работает» — единственная проверка
основного обещания продукта («руки агенту, вето владельцу»), и её тоже не
было. Здесь она проверяется НАСТОЯЩИМ движком: подделанная проверка проверяет
заглушку.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibebridge.web import PAIRING_CHECKS, pairing_state  # noqa: E402

OK_PROBE = {"ok": True, "version": "1.2.3", "name": "Вася"}


def _state(**kw):
    base = dict(armed=False, robot_url=None, robot_name=None,
                probe=None, consent_ok=None)
    base.update(kw)
    return pairing_state(**base)


def test_nothing_written_yet_shows_nothing():
    """Экран не появляется там, где сообщать нечего."""
    assert _state()["phase"] == "idle"


def test_a_written_card_puts_the_owner_on_a_waiting_screen():
    """Именно это состояние отсутствовало: карта записана, и дальше тишина."""
    view = _state(armed=True)
    assert view["phase"] == "waiting"
    assert view["done"] == 0


def test_a_robot_that_knocked_moves_the_screen_to_the_checklist():
    view = _state(armed=True, robot_url="https://robot:8630", robot_name="Вася")
    assert view["phase"] == "checklist"
    assert view["robot_name"] == "Вася"
    assert [c["id"] for c in view["checks"]] == list(PAIRING_CHECKS)


def test_reached_and_identified_are_not_the_same_fact():
    """«Связан» — дозвонились. «Проверен» — отвечает ИМЕННО робот.

    Слить их значило бы объявить связку исправной, когда по адресу сидит
    чужой HTTP: ровно та беда, которую A-12 уже ловил в ручной привязке.
    """
    чужой = _state(armed=True, robot_url="u", probe={"ok": True})
    имена = {c["id"]: c["ok"] for c in чужой["checks"]}
    assert имена["связан"] is True
    assert имена["проверен"] is False
    причина = next(c["why"] for c in чужой["checks"] if c["id"] == "проверен")
    assert "не робот" in причина, причина


def test_an_unreachable_robot_says_why():
    view = _state(armed=True, robot_url="u",
                  probe={"ok": False, "error": "нет маршрута до хоста"})
    почему = next(c["why"] for c in view["checks"] if c["id"] == "связан")
    assert почему == "нет маршрута до хоста"


def test_not_yet_run_is_not_the_same_as_failed():
    """«Ещё не знаем» не имеет права читаться как «не работает»."""
    не_запускали = _state(armed=True, robot_url="u", probe=OK_PROBE)
    assert next(c["why"] for c in не_запускали["checks"]
                if c["id"] == "согласие") == "не запускали"
    провал = _state(armed=True, robot_url="u", probe=OK_PROBE, consent_ok=False)
    assert "молчание" in next(c["why"] for c in провал["checks"]
                              if c["id"] == "согласие")


def test_green_needs_all_four_including_consent():
    """Три из четырёх — это не «связка проверена»: без четвёртого пункта не
    проверено ГЛАВНОЕ обещание продукта."""
    три = _state(armed=True, robot_url="u", robot_name="Вася", probe=OK_PROBE)
    assert три["phase"] == "checklist" and три["done"] == 3

    все = _state(armed=True, robot_url="u", robot_name="Вася", probe=OK_PROBE,
                 consent_ok=True)
    assert все["phase"] == "green" and все["done"] == все["total"] == 4


def test_the_consent_check_runs_through_the_real_engine(tmp_path):
    """Подделанная проверка проверяет заглушку.

    Ход движка блокирует поток до ответа, поэтому запрос запускается в
    отдельном, а решение отдаётся с другой поверхности — ровно как это делает
    владелец.
    """
    import threading

    from vibebridge.capabilities import ToolClass
    from vibebridge.consent import ConsentEngine, Decision

    engine = ConsentEngine(ask_timeout_s=2.0)
    ответ: list = []
    поток = threading.Thread(
        target=lambda: ответ.append(engine.request(
            "pairing_test", ToolClass.ACT, "Проверка связки")))
    поток.start()
    for _ in range(200):
        if engine.pending() is not None:
            break
        threading.Event().wait(0.01)
    заявка = engine.pending()
    assert заявка is not None, "проверка не создала настоящего запроса"
    assert заявка.tool_class is ToolClass.ACT, (
        "проверка согласия обязана идти классом ACT — иначе она проходит "
        "без вопроса и не проверяет ничего")
    assert engine.resolve_by_id(заявка.id, Decision.ALLOW, by="владелец")
    поток.join(timeout=3)
    assert ответ == [Decision.ALLOW]


def test_the_screen_renders_every_phase_it_can_report():
    """Фаза, которую движок отдаёт, а страница не рисует, — это пустой экран
    в момент, когда владельцу нужнее всего."""
    import vibebridge
    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text(
        encoding="utf-8")
    for phase in ("waiting", "checklist", "green"):
        assert f'{phase}:' in page or f'"{phase}"' in page, (
            f"страница не знает фазы «{phase}»")
    assert "loadPairing" in page and "pairConsentTest" in page
    assert "Проверить согласие" in page, (
        "нет кнопки, запускающей единственную проверку главного обещания")


def test_the_handler_itself_asks_with_a_class_that_requires_an_answer():
    """Разрыв, найденный подсадкой: тест выше строит СВОЙ движок и проверяет
    класс у запроса, который создаёт сам. Подмена класса в ОБРАБОТЧИКЕ на
    READ проходила мимо него зелёной — а READ исполняется без вопроса, то
    есть «проверка согласия» подтверждала бы сама себя.

    Разбор по AST: нужен именно аргумент вызова, а не строка в файле.
    """
    import ast

    import vibebridge
    source = (Path(vibebridge.__file__).parent / "web.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    найдено = []
    for node in ast.walk(tree):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "api_pairing_consent_test"):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            имена = [a for a in call.args
                     if isinstance(a, ast.Attribute)
                     and isinstance(a.value, ast.Name)
                     and a.value.id == "ToolClass"]
            найдено += [a.attr for a in имена]
    assert найдено == ["ACT"], (
        f"обработчик проверки связки просит класс {найдено or 'неизвестно'} — "
        f"а вопрос владельцу задаёт только ACT")
