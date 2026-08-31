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
    state = BridgeState(path=tmp_path / "s.json", panel_token="panel-secret")
    app = build_app(consent=ConsentEngine(), audit=AuditLog(tmp_path / "a.log"),
                    state=state, runner=FakeRunner(),
                    mcp_allowed_hosts=["127.0.0.1:*", "good.host:*"])
    with TestClient(app) as c:
        bad = c.get("/mcp", headers={"Host": "evil.example:48620"})
        assert bad.status_code == 421
        good = c.get("/mcp", headers={"Host": "good.host:48620"})
        assert good.status_code != 421


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
