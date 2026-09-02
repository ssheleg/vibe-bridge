"""Телеметрия робота и медиа в ленте — последние мили двух строк борда.

B-26: обе стороны `/bridge/system` были сделаны 2026-08-31, но живая сверка
ждала, пока робот подхватит коммит своим таймером. Дождались.

B-27: транспорт медиа проверен живьём ещё в build-15 (настоящий кадр с Pi,
405 700 байт). Оставалась последняя миля — событие С МЕДИА в ленте виджета.
"""
from __future__ import annotations

import sys
from pathlib import Path

import vibebridge

REPO = Path(vibebridge.__file__).resolve().parents[1]
WEBUI = Path(vibebridge.__file__).parent / "webui"
sys.path.insert(0, str(REPO))


def test_a_media_event_keeps_its_media_through_the_normaliser():
    """Событие теряет медиа — и лента показывает подпись без кадра."""
    from vibebridge.web import normalise_robot_event

    ev = normalise_robot_event(
        {"ts": "2026-09-02T14:00:00+02:00", "kind": "photo",
         "text": "снял кадр", "media": {"name": "a.jpg", "type": "image"}},
        now_iso="2026-09-02T14:00:00+02:00")
    assert ev["media"] == {"name": "a.jpg", "type": "image"}
    assert ev["kind"] == "photo"


def test_the_widget_builds_the_media_url_through_the_proxy():
    """Имя файла идёт в URL ЗАКОДИРОВАННЫМ: обход каталога не должен даже
    доехать до сервера в виде пути. Сервер его тоже отвергает (404,
    проверено живьём в build-15) — это второй рубеж, а не единственный.
    """
    from js_runner import run

    src = (WEBUI / "mascot.html").read_text(encoding="utf-8")
    начало = src.index("function mediaOf(item)")
    конец = src.index("\n}", начало) + 2
    got = run([src[начало:конец]],
              'console.log(JSON.stringify([mediaOf({media:{name:"a.jpg",type:"image"}}),'
              ' mediaOf({media:{name:"../../etc/passwd",type:"image"}}),'
              ' mediaOf({})]))')
    обычное, обход, пусто = got
    assert обычное["url"] == "/api/robot/media/a.jpg"
    assert обычное["type"] == "image"
    assert "/" not in обход["url"].split("/api/robot/media/")[1], (
        f"имя файла не закодировано — обход каталога уезжает путём: {обход}")
    assert пусто is None, "событие без медиа не должно рождать ссылку"


def test_the_panel_renders_the_system_card_from_the_robots_own_numbers():
    """Карточка была сделана, но живая сверка ждала робота. Дождались
    2026-09-02: панель показала 55.5°C, аптайм, память, диск, PM2.5 и семь
    сервисов, один из которых честно «не отвечает».

    Здесь проверяется, что панель берёт числа у РОБОТА, а не рисует своё.
    """
    page = (WEBUI / "index.html").read_text(encoding="utf-8")
    assert "/api/robot/system" in page, "панель не спрашивает телеметрию"
    for поле in ("cpu_temp", "uptime", "services"):
        assert поле in page, f"карточка не показывает «{поле}»"
    web = (Path(vibebridge.__file__).parent / "web.py").read_text(
        encoding="utf-8")
    assert "api_robot_system" in web, "маршрут телеметрии исчез"
    robot = (Path(vibebridge.__file__).parent / "robot.py").read_text(
        encoding="utf-8")
    assert "def system" in robot, "клиент робота больше не умеет телеметрию"
