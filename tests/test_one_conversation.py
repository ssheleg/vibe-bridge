"""Один разговор на две поверхности, и тост тому, кто не смотрит.

U-11: ответ из панели не попадал в ленту виджета, разговор виджета — во
вкладку «Чат», а `POST /api/mascot/session` стирал `chat_history` ЦЕЛИКОМ,
включая сессию панели: владелец нажимал «Новый» в виджете и молча терял
контекст разговора, который вёл на панели.

U-12: SCN-010 шаг 2 обещает, что клик по уведомлению открывает панель на
ленте — обработчик не задавался вовсе; шаг 3 обещает, что тост не приходит
поверх открытой панели — приходил всегда.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import vibebridge

REPO = Path(vibebridge.__file__).resolve().parents[1]
WEBUI = Path(vibebridge.__file__).parent / "webui"
WEB = (Path(vibebridge.__file__).parent / "web.py").read_text(encoding="utf-8")
sys.path.insert(0, str(REPO))


def test_a_new_pet_conversation_keeps_the_panel_thread():
    """`clear()` — это «забудь всё», а владелец просил «начни заново ЗДЕСЬ»."""
    assert "chat_history.clear()" not in WEB, (
        "«Новый» снова стирает все сессии")
    assert "chat_history.pop(" in WEB, "новая сессия не забывает свою нить"


def test_both_halves_of_the_exchange_reach_the_shared_feed():
    """Сказанное владельцем и ответ робота — обе половины, иначе лента
    покажет монолог."""
    tree = ast.parse(WEB)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "api_robot_chat")
    src = ast.unparse(node)
    assert src.count("robot_events.add") >= 2, (
        "в общую ленту попадает не весь обмен — вторая поверхность увидит "
        "половину разговора")
    assert "'by': 'owner'" in src or '"by": "owner"' in src
    assert "'by': 'robot'" in src or '"by": "robot"' in src


def test_the_panel_reads_the_shared_stream_rather_than_its_own_dom():
    page = (WEBUI / "index.html").read_text(encoding="utf-8")
    assert "loadChat" in page, "панель не читает общий поток"
    assert "/api/mascot/stream" in page
    assert 'i.kind === "chat"' in page, (
        "панель не отбирает реплики из общего потока")


def test_a_notification_that_looks_clickable_does_something():
    """Уведомление, которое выглядит кликабельным и не делает ничего, хуже
    некликабельного: оно учит не нажимать."""
    tray = (Path(vibebridge.__file__).parent / "tray.py").read_text(
        encoding="utf-8")
    assert "on_clicked" in tray, "клик по уведомлению снова никуда не ведёт"
    app = (Path(vibebridge.__file__).parent / "app.py").read_text(
        encoding="utf-8")
    assert "make_notifier(on_click=" in app, "обработчик не передан"
    assert "#feed" in app, "клик открывает панель, но не на ленте"


def test_the_backend_that_cannot_click_says_so():
    """`osascript` обработчика не умеет. Молчать об этом — обещать нажатие,
    которого не будет."""
    tray = (Path(vibebridge.__file__).parent / "tray.py").read_text(
        encoding="utf-8")
    строка = [ln for ln in tray.splitlines() if "_osa.backend" in ln
              or "без нажатия" in ln]
    assert any("без нажатия" in ln for ln in строка), (
        "запасной бэкенд не признаётся, что нажатие не работает")


def test_no_toast_lands_on_top_of_a_surface_that_is_watching():
    """Тост поверх открытой панели дублирует видимое и приучает закрывать
    уведомления не читая. Лента и питомец при этом обновляются всё равно."""
    assert "presence" in WEB and "PRESENCE_FRESH_S" in WEB
    tree = ast.parse(WEB)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "notify")
    src = ast.unparse(node)
    assert "presence" in src, "тост не спрашивает, смотрит ли кто-то"
    # ...и лента обновляется ДО этой проверки, иначе новость пропадёт
    assert src.index("robot_events.add") < src.index("presence"), (
        "проверка присутствия стоит раньше записи в ленту — на открытой "
        "панели новость исчезнет вовсе")


def test_only_a_visible_surface_claims_to_be_watching():
    """Свёрнутое окно смотрит не больше, чем закрытое."""
    for name in ("index.html", "mascot.html"):
        page = (WEBUI / name).read_text(encoding="utf-8")
        assert "vbPresence" in page, f"{name} не сообщает о присутствии"
        assert 'document.visibilityState !== "visible"' in page, (
            f"{name} считает себя видимым, даже будучи свёрнутым")
