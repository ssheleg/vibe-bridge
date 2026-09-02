"""«Не мессенджер» — граница, а не лозунг (U-8).

Продукт отгрузил серверную ленту, сессии с кнопкой «Новый» и превью медиа,
а анти-визия запрещала «переписку со своими вложениями, стикерами и историей
на годы». Запрет читался как запрет на всё это разом, `mascot.py` нёс в коде
противоположную доктрину, и легализовал отгруженное СЦЕНАРИЙ — то есть слой
ниже визии.

Граница уточнена по измерению, и здесь она держится тестами. Каждое обещание
из визии проверяется отдельно: абзац, который никто не проверяет, — это
следующий обойдённый запрет.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import vibebridge

REPO = Path(vibebridge.__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from vibebridge.feed import EventFeed  # noqa: E402


def test_the_feed_is_a_session_not_an_archive():
    """«История на годы» начинается там, где нет потолка."""
    assert EventFeed.TAIL <= 500, (
        f"лента держит {EventFeed.TAIL} записей — это уже архив")
    assert EventFeed.MAX_BYTES <= 5_000_000, (
        f"лента занимает до {EventFeed.MAX_BYTES} байт на диске")


def test_the_feed_actually_drops_what_exceeds_the_cap(tmp_path):
    """Потолок, который не срабатывает, — это не потолок."""
    feed = EventFeed(tmp_path / "feed.jsonl", tail=5)
    for i in range(20):
        feed.add({"ts": f"2026-01-01T00:00:{i:02d}", "kind": "e",
                  "text": f"строка {i}"})
    хвост = feed.tail(100)
    assert len(хвост) == 5, f"в ленте {len(хвост)} записей при потолке 5"
    assert хвост[-1]["text"] == "строка 19"


def test_the_owner_cannot_attach_files():
    """«Со своими вложениями» — про то, что кладёт ВЛАДЕЛЕЦ. Превью
    показывают то, что снял и прислал робот; загрузки нет ни одной."""
    web = (Path(vibebridge.__file__).parent / "web.py").read_text(
        encoding="utf-8")
    tree = ast.parse(web)
    подозрительные = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("form", "files"):
            подозрительные.append(f"строка {node.lineno}: request.{node.attr}")
    assert not подозрительные, (
        "появился приём файлов от владельца: " + "; ".join(подозрительные) +
        " — это «переписка со своими вложениями» из анти-визии")


def test_a_new_conversation_cuts_rather_than_archives():
    """Архив — это когда старое складывается и остаётся. «Новый» обрывает."""
    web = (Path(vibebridge.__file__).parent / "web.py").read_text(
        encoding="utf-8")
    assert "chat_history.pop(" in web
    assert "— новый разговор —" in web, (
        "граница разговора не отмечена — владелец не увидит, где он начался")


def test_the_mascots_own_line_still_expires():
    """Пузырь персонажа остаётся одной текущей строкой: доктрина уточнена
    только там, где расходилась с продуктом."""
    from vibebridge.mascot import Mascot
    src = (Path(vibebridge.__file__).parent / "mascot.py").read_text(
        encoding="utf-8")
    assert "SAY_TTL_S" in src or "expires" in src.lower(), (
        "реплика питомца больше не истекает — это транскрипт")
    assert hasattr(Mascot, "SAY_MAX_CHARS"), "реплика питомца не ограничена"


def test_the_code_doctrine_does_not_contradict_the_shipped_product():
    """`mascot.py` нёс «one current line, and it expires» как правило про всё,
    включая ленту, которой уже полгода. Доктрина в коде, расходящаяся с
    продуктом, — это инструкция следующему читателю сделать неверно."""
    src = (Path(vibebridge.__file__).parent / "mascot.py").read_text(
        encoding="utf-8")
    голова = src.split('"""', 2)[1]
    assert "лента" in голова.lower(), (
        "доктрина файла не упоминает ленту виджета — значит снова описывает "
        "продукт, которого нет")


def test_the_vision_states_the_boundary_it_can_defend():
    vision = (REPO / "docs" / "ux" / "vision.md").read_text(encoding="utf-8")
    block = vision.split("Не мессенджер", 1)[1].split("## ", 1)[0]
    for обещание in ("200", "1 МБ", "нет ни одного", "Новый"):
        assert обещание in block, (
            f"визия не называет границу «{обещание}» — запрет снова шире "
            f"того, что делает продукт")
