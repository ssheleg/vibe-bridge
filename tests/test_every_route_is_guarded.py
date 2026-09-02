"""Каждый маршрут либо закрыт, либо публичен НАМЕРЕННО (F-3).

Проверка панели стояла 34 копиями на 42 маршрутах, и теста, который перебрал
бы их все, не было. Копия — это то, что забывают: новый маршрут добавляется
без неё, и никто не узнаёт, потому что он работает.

Здесь маршруты перечисляются из САМОГО приложения, а не из списка рядом:
список рядом устаревает молча, а этот тест не сможет не заметить новый
маршрут — он его увидит и потребует решения.

Публичные названы поимённо и с причиной. Список без причин — способ
выключить проверку (тот же вывод, что в A-37, A-38, A-40).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibebridge.audit import AuditLog
from vibebridge.config import Settings
from vibebridge.consent import ConsentEngine
from vibebridge.state import BridgeState
from vibebridge.web import build_app

#: Путь → почему он открыт без ключа панели.
PUBLIC = {
    "/": "дверь: она и обменивает ?token= на куку, а без ключа отдаёт "
         "401 и объяснение человеку",
    "/mascot": "вторая дверь — окно питомца открывается тем же обменом",
    "/sw.js": "service worker: браузер тянет его ДО входа, иначе PWA не "
              "поставится",
    "/manifest.webmanifest": "манифест PWA — читается браузером до входа",
    "/offline.html": "страница для случая «мост не отвечает»: если бы она "
                     "требовала мост, показать её было бы некому",
    "/mascot.js": "рисунок питомца, статика; секретов не несёт",
    "/tokens.css": "токены оформления, статика",
    "/theme.js": "выбор темы, статика и без секретов. Публичен по той же "
                 "причине, что и токены: его грузят ДВЕРЬ и страница «нет "
                 "связи» — обе показываются человеку БЕЗ ключа, и без него "
                 "они мигали бы чужой темой",
    "/pair": "дверь РОБОТА, и у неё своя аутентификация — одноразовый "
             "токен пейринга, а не ключ панели",
}
#: Шаблоны путей с параметром — тот же смысл, но матчатся префиксом.
PUBLIC_PREFIXES = {
    "/icon-": "иконки PWA: браузер запрашивает их до входа",
}


def _app(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return build_app(consent=ConsentEngine(),
                     audit=AuditLog(tmp_path / "a.log"),
                     state=BridgeState(path=tmp_path / "s.json",
                                       panel_token="pt"),
                     settings=Settings(mode="gateway"))


def _routes(app):
    """Маршруты из самого приложения. `PeerGuard` не взведён в тестах, но
    если бы был — разворачиваем обёртку, чтобы список не опустел молча."""
    inner = getattr(app, "app", app)
    out = []
    for route in inner.routes:
        if isinstance(route, Route):
            out.append((route.path, sorted(route.methods - {"HEAD"})))
        elif isinstance(route, Mount):
            continue                      # /mcp — своя аутентификация, ADR-0002
    return out


def _is_public(path: str) -> bool:
    return path in PUBLIC or any(path.startswith(p) for p in PUBLIC_PREFIXES)


def _sample_path(path: str) -> str:
    """Подставить что-нибудь в параметры пути: нас интересует ОТКАЗ, а не
    успех, и до обработчика запрос дойти обязан."""
    return (path.replace("{size:int}", "180")
                .replace("{name:str}", "shot.jpg")
                .replace("{name}", "screenshot"))


#: Маршруты, которые ОТДАЮТ ПОТОК. Их нельзя щупать обычным запросом: при
#: снятой охране они отвечают заголовками и продолжают слать тело вечно, и
#: тест не падает, а ВИСНЕТ. Сигнальный таймаут pytest его не берёт —
#: ожидание сидит в портальном потоке (измерено при F-3). Поэтому решение
#: охраны проверяется напрямую, а HTTP-щуп их не трогает.
STREAMING = {"/events"}


def test_the_guard_closes_every_private_route(tmp_path):
    """Главная проверка, и она НЕ ходит по HTTP.

    Спрашиваем сам `PanelGuard`: пропустит ли он этот путь без ключа. Это
    именно то решение, ради которого он написан, и у него нет ни потоков,
    ни обработчиков, ни возможности зависнуть.
    """
    from vibebridge.web import PanelGuard

    guard = PanelGuard(app=None, is_authed=lambda request: False)
    открыты, проверено = [], 0
    for path, _methods in _routes(_app(tmp_path)):
        проверено += 1
        открыт = guard._open(_sample_path(path))
        if открыт and not _is_public(path):
            открыты.append(path)
        if not открыт and _is_public(path):
            открыты.append(f"{path} закрыт, хотя объявлен публичным")
    assert not открыты, (
        "охрана и список публичных путей разошлись: " + "; ".join(открыты))
    assert проверено > 30, f"перебрано всего {проверено} маршрутов — тест сломан"


def test_every_private_route_refuses_without_the_panel_key(tmp_path):
    """Тот же вопрос, но через настоящий HTTP — защита в глубину: гвард
    может быть прав и не подключён."""
    app = _app(tmp_path)
    открыты = []
    проверено = 0
    with TestClient(app) as c:            # без куки: клиент чистый
        for path, methods in _routes(app):
            if _is_public(path) or path in STREAMING:
                continue
            for method in methods:
                проверено += 1
                r = c.request(method, _sample_path(path), json={})
                if r.status_code not in (401, 403):
                    открыты.append(f"{method} {path} → {r.status_code}")
    assert not открыты, (
        "маршрут отвечает без ключа панели — закройте его или объясните в "
        f"PUBLIC: {открыты}")
    assert проверено > 30, f"перебрано всего {проверено} маршрутов — тест сломан"


def test_the_public_list_has_no_stale_entries(tmp_path):
    """Публичный путь, которого больше нет, — это разрешение, выданное
    призраку. Оно переживёт возвращение пути под тем же именем."""
    live = {path for path, _ in _routes(_app(tmp_path))}
    stale = {p for p in PUBLIC if p not in live}
    assert not stale, f"в PUBLIC остались пути, которых в приложении нет: {stale}"


def test_every_public_route_names_a_reason():
    for path, why in {**PUBLIC, **PUBLIC_PREFIXES}.items():
        assert len(why) > 25, f"{path}: причина слишком коротка, чтобы быть ей"


def test_the_doors_do_let_the_owner_in(tmp_path):
    """Обратная сторона: закрыв всё, легко закрыть и вход. Обе двери обязаны
    менять `?token=` на куку."""
    # По приложению на дверь: MCP-менеджер сессий поднимается ровно один раз
    # на экземпляр, и второй TestClient поверх того же приложения падает.
    with TestClient(_app(tmp_path / "a")) as c:
        assert c.get("/?token=pt", follow_redirects=False).status_code == 303
        assert c.get("/api/state").status_code == 200
    with TestClient(_app(tmp_path / "b")) as c:
        assert c.get("/mascot?token=pt", follow_redirects=False).status_code == 303
        assert c.get("/api/mascot").status_code == 200


@pytest.mark.parametrize("path", ["/sw.js", "/manifest.webmanifest",
                                  "/offline.html", "/mascot.js", "/tokens.css"])
def test_the_pwa_shell_is_reachable_before_login(tmp_path, path):
    """Иначе PWA не поставится: браузер тянет их ДО того, как владелец
    что-либо ввёл."""
    with TestClient(_app(tmp_path)) as c:
        assert c.get(path).status_code == 200
