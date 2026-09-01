"""T-NORTH: platform-neutral tool names with mac_* aliases the fleet still
calls, and transport security back ON with an explicit host allowlist —
the M4 off-switch becomes configuration (ADR-0002, spec §2/§5).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starlette.testclient import TestClient

from vibebridge.audit import AuditLog
from vibebridge.capabilities import ALIASES, build_capabilities
from vibebridge.consent import ConsentEngine
from vibebridge.net import allowed_hosts
from vibebridge.server import build_server
from vibebridge.state import BridgeState
from vibebridge.web import build_app


class FakeRunner:
    def run(self, argv, *, timeout=20.0, input_text=None):
        return "ok"


# ── neutral names + aliases ─────────────────────────────────────────────────

def test_capabilities_use_neutral_names():
    names = set(build_capabilities())
    assert "screenshot" in names and "open_url" in names
    assert "automation" in names            # ex mac_applescript
    assert not any(n.startswith("mac_") for n in names)


def test_aliases_cover_every_fleet_name():
    # The fleet calls these today (M1–M4 wire contract) — every one must
    # keep answering until the Hermes bump retires them (board B-7).
    fleet = {"mac_screenshot", "mac_list_apps", "mac_frontmost", "mac_notify",
             "mac_open_app", "mac_open_url", "mac_shortcut_run",
             "mac_applescript", "mac_clipboard_read", "mac_clipboard_write"}
    assert set(ALIASES) == fleet
    assert set(ALIASES.values()) == set(build_capabilities())


def test_server_registers_neutral_and_alias_tools(tmp_path):
    mcp = build_server(consent=ConsentEngine(),
                       audit=AuditLog(tmp_path / "a.log"),
                       runner=FakeRunner())
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert "screenshot" in names and "mac_screenshot" in names
    assert len(names) == 20
    alias_doc = next(t for t in tools if t.name == "mac_screenshot").description
    assert "screenshot" in alias_doc        # alias points at the real name


# ── host allowlist ──────────────────────────────────────────────────────────

def test_allowed_hosts_gateway_mode_covers_loopback_and_tailnet():
    st = BridgeState(path=Path("/dev/null"), panel_token="x")
    hosts = allowed_hosts(st, tailnet_ips=["100.64.0.7"],
                          dns_name="mac.tn.ts.net")
    assert "127.0.0.1:*" in hosts and "localhost:*" in hosts
    assert "100.64.0.7:*" in hosts          # the gateway's verbatim Host
    assert "mac.tn.ts.net:*" in hosts       # tailscale-serve clients


def test_mcp_rejects_foreign_host_and_accepts_allowed(tmp_path):
    """The Host allowlist, reached with a valid bearer.

    The bearer now answers FIRST — an unpaired bridge in standalone refuses
    /mcp outright — so a request that means to exercise the host check has to
    get past the token. That order is deliberate: `Host` comes from the client
    and cannot be a boundary on its own.
    """
    state = BridgeState(path=tmp_path / "s.json", panel_token="panel-secret",
                        robot_token="robot-secret")
    app = build_app(consent=ConsentEngine(), audit=AuditLog(tmp_path / "a.log"),
                    state=state, runner=FakeRunner(),
                    mcp_allowed_hosts=["127.0.0.1:*", "good.host:*"])
    auth = {"Authorization": "Bearer robot-secret"}
    with TestClient(app) as c:
        bad = c.get("/mcp", headers={"Host": "evil.example:48620", **auth})
        assert bad.status_code == 421
        good = c.get("/mcp", headers={"Host": "good.host:48620", **auth})
        assert good.status_code != 421


def test_an_unpaired_bridge_refuses_mcp_in_standalone(tmp_path):
    """The hole this closes, stated plainly.

    `BearerGuard` used to read «token exists → check it», which meant a FRESH
    install — standalone by default, no robot paired yet — served /mcp with no
    authentication at all. The only thing left guarding it was the Host
    allowlist, and that cannot guard: `Host` is sent by the client, so naming
    `127.0.0.1` passed the check (measured 2026-09-01 on this machine). Until
    a robot is paired there is nobody to serve.
    """
    state = BridgeState(path=tmp_path / "s.json", panel_token="panel-secret")
    assert state.robot_token is None
    app = build_app(consent=ConsentEngine(), audit=AuditLog(tmp_path / "a.log"),
                    state=state, runner=FakeRunner())
    with TestClient(app) as c:
        r = c.get("/mcp", headers={"Host": "127.0.0.1:48620"})
        assert r.status_code == 401
        assert "unpaired" in r.text


def test_a_paired_bridge_still_answers_its_own_robot(tmp_path):
    """The mirror of the test above, and the reason it is here: an earlier
    version of this guard keyed on «token exists» in the WRONG direction and
    silently 401'd a paired robot's own tools for ~15 hours (2026-08-29)."""
    state = BridgeState(path=tmp_path / "s.json", panel_token="p",
                        robot_token="robot-secret")
    app = build_app(consent=ConsentEngine(), audit=AuditLog(tmp_path / "a.log"),
                    state=state, runner=FakeRunner())
    with TestClient(app) as c:
        assert c.get("/mcp", headers={"Authorization": "Bearer robot-secret"}
                     ).status_code != 401
        assert c.get("/mcp").status_code == 401       # …and nobody else


# ── the network boundary is the peer, not a header ──────────────────────────


def _scope(client_host):
    return {"type": "http", "method": "GET", "path": "/", "headers": [],
            "client": (client_host, 51234) if client_host else None,
            "query_string": b"", "scheme": "http", "http_version": "1.1"}


def test_the_peer_guard_refuses_the_local_network(tmp_path):
    """Bound on all interfaces, the only real boundary is where the packet
    came from. A LAN peer cannot forge a source address and still complete a
    TCP handshake; it CAN forge `Host`."""
    import asyncio

    from vibebridge.web import PeerGuard

    seen, sent = [], []

    async def inner(scope, receive, send):
        seen.append(scope["client"])

    async def send(msg):
        sent.append(msg)

    guard = PeerGuard(inner, armed=True)
    asyncio.run(guard(_scope("192.168.1.50"), None, send))
    assert seen == []                                  # never reached the app
    assert sent[0]["status"] == 403

    seen.clear()
    sent.clear()
    asyncio.run(guard(_scope("100.72.246.104"), None, send))
    assert seen and not sent                           # tailnet peer passes

    seen.clear()
    sent.clear()
    asyncio.run(guard(_scope("127.0.0.1"), None, send))
    assert seen and not sent                           # loopback passes


def test_an_unknown_peer_is_refused_not_assumed_friendly():
    """Fail-open in a security boundary is the absence of a boundary."""
    import asyncio

    from vibebridge.web import PeerGuard

    sent = []

    async def inner(scope, receive, send):
        raise AssertionError("не должно быть вызвано")

    async def send(msg):
        sent.append(msg)

    asyncio.run(PeerGuard(inner, armed=True)(_scope(None), None, send))
    assert sent[0]["status"] == 403


def test_the_guard_is_armed_only_when_the_bind_is_wider_than_loopback():
    """At a loopback bind every peer IS loopback, and the check would be
    decoration. The launcher is the only place that knows the bind."""
    from pathlib import Path

    import vibebridge

    src = (Path(vibebridge.__file__).parent / "app.py").read_text()
    assert "peer_guard=bind_host != BRIDGE_HOST" in src


# ── the loop must never freeze on a blocked consent ─────────────────────────

def test_act_tool_does_not_block_event_loop(tmp_path):
    """Regression: an ACT call awaiting consent parks in a worker thread.
    Awaiting dispatch() inline froze the whole loop — panel, SSE and other
    sessions went dark for the length of the dialog (caught live 2026-08-29)."""
    from vibebridge.capabilities import Capability, ToolClass

    async def scenario():
        eng = ConsentEngine(ask_timeout_s=0.6)   # request will time out
        cap = Capability("do", ToolClass.ACT, "делаю",
                         lambda r, a: "done", {})
        mcp = build_server(consent=eng, audit=AuditLog(tmp_path / "a.log"),
                          runner=FakeRunner(), capabilities={"do": cap})
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(40):
                await asyncio.sleep(0.02)
                ticks += 1

        result, _ = await asyncio.gather(mcp.call_tool("do", {}), ticker())
        assert ticks >= 20          # the loop kept breathing while blocked
        return result

    asyncio.run(scenario())


def test_tailnet_addresses_are_not_re_shelled_on_every_lookup(monkeypatch):
    """It runs the Tailscale CLI. Called per `build_app`, that was sixty
    subprocesses in one suite and an eighty-second run — the cost that makes
    people stop running the tests."""
    from vibebridge import net

    calls = []

    def fake_run(*a, **kw):
        calls.append(a)
        class R:
            returncode = 0
            stdout = "100.64.0.1\n"
        return R()

    net._cache.clear()
    monkeypatch.setattr(net.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(net.subprocess, "run", fake_run)

    first = net.tailscale_ips()
    for _ in range(20):
        net.tailscale_ips()
    assert first == ["100.64.0.1"]
    assert len(calls) == 1


def test_a_forced_lookup_still_asks(monkeypatch):
    from vibebridge import net

    calls = []

    def fake_run(*a, **kw):
        calls.append(a)
        class R:
            returncode = 0
            stdout = "100.64.0.2\n"
        return R()

    net._cache.clear()
    monkeypatch.setattr(net.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(net.subprocess, "run", fake_run)
    net.tailscale_ips()
    net.tailscale_ips(force=True)
    assert len(calls) == 2


def test_the_cache_does_not_leak_between_callers(monkeypatch):
    """A caller mutating the returned list must not poison the next one."""
    from vibebridge import net

    net._cache["ips"] = (__import__("time").monotonic(), ["100.64.0.9"])
    got = net.tailscale_ips()
    got.append("подделка")
    assert net.tailscale_ips() == ["100.64.0.9"]


def test_the_magicdns_name_is_cached_too(monkeypatch):
    """It is the second subprocess `allowed_hosts` runs, and it runs on the
    same path as the first."""
    from vibebridge import net

    calls = []

    def fake_run(*a, **kw):
        calls.append(a)
        class R:
            returncode = 0
            stdout = '{"Self": {"DNSName": "mac.tn.ts.net."}}'
        return R()

    net._cache.clear()
    monkeypatch.setattr(net.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(net.subprocess, "run", fake_run)
    assert net.tailnet_dns_name() == "mac.tn.ts.net"
    for _ in range(10):
        net.tailnet_dns_name()
    assert len(calls) == 1
