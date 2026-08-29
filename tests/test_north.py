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

from macbridge.audit import AuditLog
from macbridge.capabilities import ALIASES, build_capabilities
from macbridge.consent import ConsentEngine
from macbridge.net import allowed_hosts
from macbridge.server import build_server
from macbridge.state import BridgeState
from macbridge.web import build_app


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
    hosts = allowed_hosts(st, tailnet_ips=["100.64.0.7"])
    assert "127.0.0.1:*" in hosts and "localhost:*" in hosts
    assert "100.64.0.7:*" in hosts          # the gateway's verbatim Host


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
