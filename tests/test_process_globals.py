"""Процессный глобал, который пишет мост, обязан восстанавливаться (A-41).

`build_app` писал `capabilities._notifier`, и никто не клал его обратно.
Десятки тестов зовут `build_app`, часть потом дёргает нотифаер напрямую — и
полагается на то, ЧЬЁ приложение победило последним. Такой набор зеленеет и
краснеет от порядка файлов, а не от кода.

Эта сессия добавила второй такой глобал (`_notify_limit`, A-25) — то есть
класс живой, и списка, который ведётся руками, мало. Здесь список сверяется
с кодом: каждое `global` в отгружаемых модулях обязано быть в фикстуре.
"""
from __future__ import annotations

import ast
from pathlib import Path

import vibebridge
from tests.conftest import _PROCESS_GLOBALS

REPO = Path(vibebridge.__file__).resolve().parents[1]
ROOTS = (REPO / "vibebridge", REPO / "vbboot")

#: Имя → почему его не нужно восстанавливать между тестами.
NOT_STATE = {}


def _globals_written_by_shipped_code() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            module = ".".join(
                path.relative_to(REPO).with_suffix("").parts)
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Global):
                    for name in node.names:
                        found.add((module, name))
    return found


def test_every_process_global_is_restored_between_tests():
    declared = set(_PROCESS_GLOBALS)
    actual = {g for g in _globals_written_by_shipped_code()
              if g[1] not in NOT_STATE}
    missing = actual - declared
    assert not missing, (
        "процессный глобал пишется мостом и НЕ восстанавливается между "
        "тестами — допишите его в `_PROCESS_GLOBALS` в conftest: "
        f"{sorted(missing)}")
    stale = declared - actual
    assert not stale, (
        "в списке восстановления есть то, чего код больше не пишет — "
        f"снимите: {sorted(stale)}")
    # Канарейка: пустой разбор выглядит как успех.
    assert actual, "разбор не нашёл ни одного `global` — он сломан"


def test_the_fixture_actually_puts_the_value_back():
    """Сквозная проверка, а не наличие фикстуры: пишем глобал так же, как это
    делает `build_app`, и следующий тест обязан увидеть исходное значение."""
    import vibebridge.capabilities as caps

    assert caps._notify_limit is None, (
        "предыдущий тест оставил тормоз уведомлений включённым — "
        "фикстура не сработала")
    caps._set_notify_limit(caps.RateLimit(per_window=1, window_s=1.0))
    assert caps._notify_limit is not None


def test_the_next_test_sees_a_clean_global():
    """Пара к предыдущему: он оставил глобал ЗАПИСАННЫМ, и если бы фикстура
    не работала, этот бы это и увидел. Порядок держит имя файла — оба теста
    в одном модуле, и pytest идёт по нему сверху вниз."""
    import vibebridge.capabilities as caps

    assert caps._notify_limit is None


# ── A-42: набор не уходит в медленные внешние бинари ───────────────────────

def test_the_suite_refuses_to_shell_into_slow_binaries():
    """A-42: класс дважды стоил набору времени — десятисекундный прогон
    становился четырёхминутным. Чинили его кэшем с TTL, то есть смягчали
    симптом: вызовы оставались, просто реже.

    Хуже скорости другое: ответ `tailscale` ЗАВИСИТ ОТ МАШИНЫ. На ноутбуке в
    тейлнете `allowed_hosts` получает лишние адреса, в CI — не получает, и
    тест проверяет разное в разных местах, не сообщая об этом.
    """
    from tests.conftest import SLOW_BINARIES

    assert "tailscale" in SLOW_BINARIES
    # Заглушка стоит на трёх функциях, а не на одной: каждая шеллится сама.
    from vibebridge import net

    assert net.tailscale_ips() == []
    assert net.tailnet_dns_name() is None
    assert net.serve_active(48620) is False


def test_a_test_that_shells_out_is_named_at_teardown():
    """Проверяется ФОРМА вердикта: он на teardown, а не бросок в момент
    вызова. Бросок код под тестом ловит своим `except` и честно отвечает
    «нет tailscale» — предохранитель промолчал бы (тот же вывод, что в
    A-35)."""
    import re
    from pathlib import Path

    src = (Path(__file__).parent / "conftest.py").read_text()
    guard = src.split("if shelled:", 1)[1].split("if unguarded:", 1)[0]
    assert "pytest.fail" in guard
    assert re.search(r"CompletedProcess\(argv, 1", src), (
        "заглушка обязана возвращать ОТКАЗ, а не выдуманный успех")
