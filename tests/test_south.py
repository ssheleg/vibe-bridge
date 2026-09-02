"""T-SOUTH: the robot client — SCN-007/008/009/012 seams against a mocked
robot (the live robot ships the contract in M-ROBOT). Every degraded branch
returns a speakable, honest answer; nothing spins.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from starlette.testclient import TestClient

from vibebridge.audit import AuditLog
from vibebridge.consent import ConsentEngine
from vibebridge.robot import RobotClient
from vibebridge.state import BridgeState
from vibebridge.web import build_app


def _client(handler, **kw) -> RobotClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kw.setdefault("base_url", "http://robot.test:8630")
    kw.setdefault("chat_url", "http://robot.test:8642")
    return RobotClient(http=http, **kw)


def _run(coro):
    return asyncio.run(coro)


# ── status ──────────────────────────────────────────────────────────────────

def test_status_online():
    def h(req):
        assert req.url.path == "/bridge/status"
        return httpx.Response(200, json={"version": "v1.0", "build": 214,
                                         "orchestrator": "hermes",
                                         "uptime_s": 3600})
    st = _run(_client(h, name="Вася").status())
    assert st["online"] is True and st["version"] == "v1.0"
    assert st["name"] == "Вася"


def test_status_offline_is_honest_with_since():
    def h(req):
        raise httpx.ConnectError("boom", request=req)
    c = _client(h)
    st = _run(c.status())
    assert st["online"] is False and st["configured"] is True
    assert st["offline_since"] > 0
    assert "недоступен" in st["reason"]
    again = _run(c.status())                 # since is sticky, not resampled
    assert again["offline_since"] == st["offline_since"]


def test_status_unconfigured():
    st = _run(RobotClient(http=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500)))).status())
    assert st == {"configured": False, "online": False,
                  "reason": "робот не подключён к панели"}


# ── chat ────────────────────────────────────────────────────────────────────

def _chat_ok(req):
    assert req.url.path == "/v1/chat/completions"
    assert req.headers.get("authorization") == "Bearer k1"
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": "привет!"}}]})


def test_chat_happy():
    res = _run(_client(_chat_ok, chat_key="k1").chat("привет"))
    assert res == {"ok": True, "reply": "привет!"}


def test_chat_never_resends_a_turn_that_may_have_been_delivered():
    """A-6: ход мозга не идемпотентен. К 150-й секунде ход №1 мог уже
    опубликовать пост в канал, снять кадр со вспышкой и открыть вкладку —
    повтор того же payload исполняет это второй раз. `ReadTimeout` значит
    «запрос ушёл, ответа нет», и повторять его нельзя ни разу."""
    calls = {"n": 0}
    def h(req):
        calls["n"] += 1
        raise httpx.ReadTimeout("slow", request=req)
    res = _run(_client(h, chat_key="k1").chat("привет"))
    assert calls["n"] == 1                       # ровно один ход
    assert res["ok"] is False and res["undelivered"] is False
    assert "дольше обычного" in res["error"]


def test_chat_retries_only_when_the_turn_never_left_the_bridge():
    """Соединение не установилось — робот запроса не видел, побочных
    эффектов быть не может. Это единственный безопасный повтор."""
    for exc_cls in (httpx.ConnectTimeout, httpx.PoolTimeout):
        calls = {"n": 0}
        def h(req, _e=exc_cls, _c=calls):
            _c["n"] += 1
            if _c["n"] == 1:
                raise _e("no route", request=req)
            return _chat_ok(req)
        res = _run(_client(h, chat_key="k1").chat("привет"))
        assert res["ok"] is True and calls["n"] == 2, exc_cls.__name__


def test_chat_unreachable_robot_is_undelivered_not_slow():
    """Дважды не дозвонились — это НЕ «думает дольше обычного»: робот хода
    не получил, и владельцу надо сказать именно это."""
    def h(req):
        raise httpx.ConnectTimeout("no route", request=req)
    res = _run(_client(h).chat("x"))
    assert res["ok"] is False and res["undelivered"] is True
    assert "дольше обычного" not in res["error"]


def test_chat_server_error_is_undelivered():
    def h(req):
        return httpx.Response(502, text="bad gateway")
    res = _run(_client(h).chat("x"))
    assert res["ok"] is False and res["undelivered"] is True
    assert "502" in res["error"]


def test_chat_unconfigured_is_undelivered():
    res = _run(RobotClient(http=httpx.AsyncClient(
        transport=httpx.MockTransport(_chat_ok))).chat("x"))
    assert res["undelivered"] is True and "не подключён" in res["error"]


# ── update + events ─────────────────────────────────────────────────────────

def test_update_trigger_ok_and_error():
    ok = _run(_client(lambda r: httpx.Response(202)).trigger_update())
    assert ok == {"ok": True}
    err = _run(_client(lambda r: httpx.Response(503)).trigger_update())
    assert err["ok"] is False and "503" in err["error"]


def test_events_stream_yields_dicts():
    body = ('data: {"kind": "task_done", "text": "готово"}\n\n'
            'data: {"kind": "alert", "text": "батарея"}\n\n').encode()
    def h(req):
        assert req.url.path == "/bridge/events"
        return httpx.Response(200, content=body)
    async def collect():
        return [e async for e in _client(h).events()]
    evs = _run(collect())
    assert [e["kind"] for e in evs] == ["task_done", "alert"]


# ── web wiring ──────────────────────────────────────────────────────────────

def _app(tmp_path, robot):
    state = BridgeState(path=tmp_path / "s.json", panel_token="panel-secret")
    return build_app(consent=ConsentEngine(), audit=AuditLog(tmp_path / "a.log"),
                     state=state, capabilities={}, robot=robot,
                     mcp_allowed_hosts=["testserver", "127.0.0.1:*"])


def test_robot_endpoints(tmp_path):
    def h(req):
        if req.url.path == "/bridge/status":
            return httpx.Response(200, json={"version": "v1.0"})
        if req.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [
                {"message": {"content": "ответ мозга"}}]})
        if req.url.path == "/bridge/update":
            return httpx.Response(202)
        return httpx.Response(404)
    app = _app(tmp_path, _client(h))
    with TestClient(app) as c:
        assert c.post("/api/robot/chat", json={"text": "x"}).status_code == 401
        c.get("/?token=panel-secret")
        st = c.get("/api/robot/status").json()
        assert st["online"] is True
        chat = c.post("/api/robot/chat", json={"text": "привет"}).json()
        assert chat["reply"] == "ответ мозга"
        up = c.post("/api/robot/update").json()
        assert up["ok"] is True


def test_robot_endpoints_unconfigured_are_honest(tmp_path):
    app = _app(tmp_path, RobotClient(http=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500)))))
    with TestClient(app) as c:
        c.get("/?token=panel-secret")
        st = c.get("/api/robot/status").json()
        assert st["configured"] is False
        chat = c.post("/api/robot/chat", json={"text": "x"}).json()
        assert chat["undelivered"] is True


def test_bridge_api_calls_carry_shared_bearer():
    """bridge_api робота гейтит ВСЕ эндпоинты одним robot_token — статус,
    апдейт и события обязаны нести bearer, не только чат (пойман live 401)."""
    seen: list[str | None] = []

    def h(req):
        seen.append(req.headers.get("authorization"))
        if req.url.path == "/bridge/status":
            return httpx.Response(200, json={"version": "v1"})
        return httpx.Response(202)

    c = _client(h, chat_key="shared-tok")
    _run(c.status())
    _run(c.trigger_update())
    assert seen == ["Bearer shared-tok"] * 2


# ── системная телеметрия робота ────────────────────────────────────────────

def test_system_returns_what_the_robot_reports(anyio_backend=None):
    """Робот отдаёт проекцию своего канонического снимка; мост её не
    переизобретает и не досочиняет."""
    import asyncio

    import httpx

    from vibebridge.robot import RobotClient

    payload = {"cpu_temp": "48.3°C", "ram": {"pct": 41.2},
               "services": [{"label": "Мозг (Hermes)", "health": True}]}

    def handler(request):
        assert request.url.path == "/bridge/system"
        assert request.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json=payload)

    client = RobotClient(base_url="https://r", chat_url="https://r",
                         chat_key="tok", name="Вася",
                         http=httpx.AsyncClient(
                             transport=httpx.MockTransport(handler)))
    got = asyncio.run(client.system())
    assert got["ok"] and got["cpu_temp"] == "48.3°C"


def test_system_is_honest_when_the_robot_is_old():
    """Робот, который ещё не обновился, отвечает 404 — это не ошибка моста и
    не повод показать пустую панель без объяснения."""
    import asyncio

    import httpx

    from vibebridge.robot import RobotClient

    client = RobotClient(base_url="https://r", chat_url="https://r",
                         chat_key="tok", name="Вася",
                         http=httpx.AsyncClient(
                             transport=httpx.MockTransport(
                                 lambda r: httpx.Response(404))))
    got = asyncio.run(client.system())
    assert not got["ok"]
    assert "не обновился" in got["error"]


def test_system_survives_an_unreachable_robot():
    import asyncio

    import httpx

    from vibebridge.robot import RobotClient

    def boom(request):
        raise httpx.ConnectError("нет связи")

    client = RobotClient(base_url="https://r", chat_url="https://r",
                         chat_key="tok", name="Вася",
                         http=httpx.AsyncClient(
                             transport=httpx.MockTransport(boom)))
    got = asyncio.run(client.system())
    assert not got["ok"] and got["error"]


def test_system_needs_a_configured_robot():
    import asyncio

    from vibebridge.robot import RobotClient

    got = asyncio.run(RobotClient(base_url=None, chat_url=None, chat_key=None,
                                  name="робот").system())
    assert not got["ok"] and "не подключ" in got["error"]


def test_media_is_fetched_with_the_bridges_own_bearer():
    """The page must never hold the robot's token, and the photo must not be
    published outside the tailnet just to be shown to its owner."""
    import asyncio

    import httpx

    from vibebridge.robot import RobotClient

    def handler(request):
        assert request.url.path == "/bridge/media/1-snap.jpg"
        assert request.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, content=b"\xff\xd8\xff",
                              headers={"content-type": "image/jpeg"})

    client = RobotClient(base_url="https://r", chat_url="https://r",
                         chat_key="tok", name="Вася",
                         http=httpx.AsyncClient(
                             transport=httpx.MockTransport(handler)))
    got = asyncio.run(client.media("1-snap.jpg"))
    assert got["ok"] is True
    assert got["body"].startswith(b"\xff\xd8\xff") and got["type"] == "image/jpeg"


def test_media_refuses_a_name_that_walks_out():
    """Checked on both sides: directory traversal must not depend on how much
    the two ends trust each other."""
    import asyncio

    import httpx

    from vibebridge.robot import RobotClient

    client = RobotClient(base_url="https://r", chat_url="https://r",
                         chat_key="tok", name="Вася",
                         http=httpx.AsyncClient(
                             transport=httpx.MockTransport(
                                 lambda r: httpx.Response(200, content=b"x"))))
    for bad in ("../secret", "a/b", "..", ""):
        got = asyncio.run(client.media(bad))
        assert got["ok"] is False and got["kind"] == "bad-name", bad


def test_the_thread_so_far_travels_with_the_question():
    """Sending only the last message left the brain to reconstruct context
    from its own long-term memory, and it answered with something said an
    hour earlier — «он не видит того, что только что писал»."""
    import asyncio

    import httpx

    from vibebridge.robot import RobotClient

    seen = {}

    def handler(request):
        import json as _j
        seen["messages"] = _j.loads(request.content)["messages"]
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ок"}}]})

    client = RobotClient(base_url="https://r", chat_url="https://r",
                         chat_key="k", name="Вася",
                         http=httpx.AsyncClient(
                             transport=httpx.MockTransport(handler)))
    asyncio.run(client.chat("а сейчас?", history=[
        {"role": "user", "content": "запомни якорь"},
        {"role": "assistant", "content": "запомнил"}]))

    assert [m["content"] for m in seen["messages"]] == [
        "запомни якорь", "запомнил", "а сейчас?"]


def test_a_first_turn_carries_only_itself():
    import asyncio

    import httpx

    from vibebridge.robot import RobotClient

    seen = {}

    def handler(request):
        import json as _j
        seen["n"] = len(_j.loads(request.content)["messages"])
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ок"}}]})

    client = RobotClient(base_url="https://r", chat_url="https://r",
                         chat_key="k", name="Вася",
                         http=httpx.AsyncClient(
                             transport=httpx.MockTransport(handler)))
    asyncio.run(client.chat("привет"))
    assert seen["n"] == 1


# ── одновременность хода (A-6, вторая дверь) ────────────────────────────────

class _SlowChat(httpx.AsyncBaseTransport):
    """Робот, который думает: держит ход открытым три секунды. Асинхронно —
    блокирующий обработчик подвесил бы цикл, и второй запрос не дошёл бы до
    моста вовсе: тест доказывал бы устройство TestClient, а не гвард."""

    THINKING_S = 3.0

    def __init__(self) -> None:
        self.started = threading.Event()
        self.turns = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.turns += 1
        self.started.set()
        await asyncio.sleep(self.THINKING_S)
        return httpx.Response(200, json={"choices": [
            {"message": {"role": "assistant", "content": "готово"}}]})


def test_a_second_turn_in_one_session_is_refused_while_the_first_runs(tmp_path):
    """Повтор транспорта закрыт, но остаётся владелец: совет «повторите
    позже» стоит рядом с кнопкой — и второй клик по живому ходу исполняет
    tool-call'ы второй раз. Один ход на сессию за раз."""
    from starlette.testclient import TestClient

    from vibebridge.audit import AuditLog
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState
    from vibebridge.web import build_app

    slow = _SlowChat()
    robot = RobotClient(chat_url="http://robot", chat_key="k",
                        http=httpx.AsyncClient(transport=slow))
    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(state=state, consent=ConsentEngine(),
                    audit=AuditLog(path=tmp_path / "a.log"), robot=robot)

    with TestClient(app) as c:
        c.get("/?token=pt")
        out: dict = {}
        t = threading.Thread(
            target=lambda: out.update(first=c.post(
                "/api/robot/chat", json={"text": "сними кадр"})),
            daemon=True)
        t.start()
        assert slow.started.wait(10), "первый ход не дошёл до робота"

        second = c.post("/api/robot/chat", json={"text": "сними кадр"})
        assert second.status_code == 409
        body = second.json()
        assert body["ok"] is False and body["undelivered"] is True
        assert "уже" in body["error"]
        assert slow.turns == 1                 # робот увидел ОДИН ход

        t.join(20)
        assert out["first"].status_code == 200
        # ...и сессия снова свободна
        assert c.post("/api/robot/chat",
                      json={"text": "ещё"}).status_code == 200
        assert slow.turns == 2


# ── A-12: привязка обязана ПРОВЕРИТЬ, а не поверить на слово ───────────────

def test_probe_says_ok_when_a_real_robot_answers():
    res = _run(_client(lambda r: httpx.Response(200, json={
        "name": "Вася", "version": "v1.2.0", "build": 142}),
        chat_key="k").probe())
    assert res["ok"] is True and res["name"] == "Вася" and res["build"] == 142


def test_probe_calls_a_wrong_key_by_its_name():
    """Пустой ключ подставлял свежий `robot_token`, которого робот никогда
    не видел, — и мост объявлял «привязан ✓» рядом с вечным 401."""
    res = _run(_client(lambda r: httpx.Response(401, text="no")).probe())
    assert res["ok"] is False and res["kind"] == "unauthorized"
    assert "ключ" in res["error"]


def test_probe_names_an_address_that_is_not_a_robot():
    """Валидировался ТОЛЬКО префикс `http(s)://`: адрес чужого сайта
    проходил как робот."""
    res = _run(_client(lambda r: httpx.Response(200, text="<html>hello")).probe())
    assert res["ok"] is False and res["kind"] == "not-a-robot"

    wrong_shape = _run(_client(
        lambda r: httpx.Response(200, json={"что-то": "другое"})).probe())
    assert wrong_shape["ok"] is False and wrong_shape["kind"] == "not-a-robot"


def test_probe_separates_unreachable_from_unauthorized():
    def dead(req):
        raise httpx.ConnectError("no route", request=req)
    res = _run(_client(dead).probe())
    assert res["ok"] is False and res["kind"] == "unreachable"


def test_probe_without_an_address_is_not_an_error_about_the_network():
    res = _run(RobotClient(http=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200)))).probe())
    assert res["ok"] is False and res["kind"] == "unconfigured"


# ── A-20: медиа под потолком, и отказы различимы ───────────────────────────

class _Stream(httpx.AsyncBaseTransport):
    """Робот, отдающий медиа кусками — как настоящий."""

    def __init__(self, chunks, *, status=200, ctype="image/jpeg",
                 length=None) -> None:
        self.chunks = chunks
        self.status = status
        self.ctype = ctype
        self.length = length

    async def handle_async_request(self, request):
        headers = {"content-type": self.ctype}
        if self.length is not None:
            headers["content-length"] = str(self.length)

        async def body():
            for c in self.chunks:
                yield c

        return httpx.Response(self.status, headers=headers,
                              stream=httpx.AsyncByteStream() if False
                              else _Iter(body()))


class _Iter(httpx.AsyncByteStream):
    def __init__(self, gen):
        self._gen = gen

    async def __aiter__(self):
        async for chunk in self._gen:
            yield chunk


def _media_client(transport, **kw):
    return RobotClient(base_url="http://robot", chat_key="k",
                       http=httpx.AsyncClient(transport=transport), **kw)


def test_media_comes_back_with_its_type():
    got = _run(_media_client(_Stream([b"\xff\xd8", b"jpeg"])).media("shot.jpg"))
    assert got["ok"] is True
    assert got["body"] == b"\xff\xd8jpeg" and got["type"] == "image/jpeg"


def test_media_over_the_cap_is_refused_by_its_own_name():
    """A-20: тело втягивалось в ОЗУ моста целиком и без потолка. Видео ни
    одна сторона по размеру не ограничивает."""
    from vibebridge.robot import MEDIA_MAX_BYTES

    big = [b"x" * 100_000] * ((MEDIA_MAX_BYTES // 100_000) + 2)
    got = _run(_media_client(_Stream(big)).media("video.mp4"))
    assert got["ok"] is False and got["kind"] == "too-large"
    assert "велик" in got["error"]


def test_a_declared_size_is_refused_before_a_single_byte_is_read():
    """Content-Length — дешёвый отказ: тянуть гигабайт, чтобы потом сказать
    «слишком много», значит заплатить ровно ту цену, которой избегаем."""
    from vibebridge.robot import MEDIA_MAX_BYTES

    t = _Stream([b"x"], length=MEDIA_MAX_BYTES * 4)
    got = _run(_media_client(t).media("video.mp4"))
    assert got["ok"] is False and got["kind"] == "too-large"


def test_the_four_failures_are_told_apart():
    """Офлайн-робот, неверный ключ, отсутствующий файл и обход каталога
    назывались одним 404 «файл недоступен» — четыре разные беды, каждая с
    разным следующим шагом для владельца."""
    assert _run(_media_client(_Stream([], status=404)).media("нет.jpg")
                )["kind"] == "not-found"
    assert _run(_media_client(_Stream([], status=401)).media("a.jpg")
                )["kind"] == "unauthorized"
    assert _run(RobotClient(http=httpx.AsyncClient(
        transport=_Stream([]))).media("a.jpg"))["kind"] == "unconfigured"
    assert _run(_media_client(_Stream([])).media("../../etc/passwd")
                )["kind"] == "bad-name"

    class _Dead(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("no route", request=request)

    assert _run(_media_client(_Dead()).media("a.jpg"))["kind"] == "unreachable"
