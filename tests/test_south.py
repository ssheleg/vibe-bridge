"""T-SOUTH: the robot client — SCN-007/008/009/012 seams against a mocked
robot (the live robot ships the contract in M-ROBOT). Every degraded branch
returns a speakable, honest answer; nothing spins.
"""
from __future__ import annotations

import asyncio
import sys
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


def test_chat_retries_once_on_timeout_then_succeeds():
    calls = {"n": 0}
    def h(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("slow", request=req)
        return _chat_ok(req)
    res = _run(_client(h, chat_key="k1").chat("привет"))
    assert res["ok"] is True and calls["n"] == 2


def test_chat_double_timeout_is_slow_not_undelivered():
    def h(req):
        raise httpx.ReadTimeout("slow", request=req)
    res = _run(_client(h).chat("x"))
    assert res["ok"] is False and res["undelivered"] is False
    assert "дольше обычного" in res["error"]


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
