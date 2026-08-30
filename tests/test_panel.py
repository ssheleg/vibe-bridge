"""T-PANEL: the journal read path (filters, pagination, honest error) and
its HTTP surface — SCN-011's implementing seams.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starlette.testclient import TestClient

from vibebridge.audit import AuditLog
from vibebridge.consent import ConsentEngine
from vibebridge.state import BridgeState
from vibebridge.web import build_app


def _fill(aud: AuditLog) -> None:
    aud.record(tool="open_url", tool_class="act", decision="allow", ok=True,
               line="открыть ссылку a")
    aud.record(tool="screenshot", tool_class="read", decision="auto", ok=True,
               line="смотрю на экран")
    aud.record(tool="open_app", tool_class="act", decision="deny", ok=False,
               line="открыть «X»")
    aud.record(tool="open_app", tool_class="act", decision="timeout", ok=False,
               line="открыть «Y»")
    aud.record(tool="list_apps", tool_class="read", decision="paused", ok=False,
               line="список приложений")


def test_read_entries_newest_first_and_pagination(tmp_path):
    aud = AuditLog(tmp_path / "a.log")
    _fill(aud)
    page = aud.read_entries(limit=2)
    assert page["total"] == 5
    assert [e["decision"] for e in page["entries"]] == ["paused", "timeout"]
    page2 = aud.read_entries(offset=2, limit=2)
    assert [e["decision"] for e in page2["entries"]] == ["deny", "auto"]


def test_read_entries_filter_refused_and_class(tmp_path):
    aud = AuditLog(tmp_path / "a.log")
    _fill(aud)
    refused = aud.read_entries(flt="refused")
    assert {e["decision"] for e in refused["entries"]} == {
        "deny", "timeout", "paused"}
    acts = aud.read_entries(flt="act")
    assert all(e["class"] == "act" for e in acts["entries"])
    assert acts["total"] == 3


def test_read_entries_missing_file_is_honest(tmp_path):
    aud = AuditLog(tmp_path / "nope" / "a.log")
    (tmp_path / "nope").rmdir() if (tmp_path / "nope").exists() else None
    page = aud.read_entries()
    assert page["entries"] == [] and page["total"] == 0


def test_journal_endpoint_filters(tmp_path):
    state = BridgeState(path=tmp_path / "s.json", panel_token="panel-secret")
    aud = AuditLog(tmp_path / "a.log")
    _fill(aud)
    app = build_app(consent=ConsentEngine(), audit=aud, state=state,
                    capabilities={}, mcp_allowed_hosts=["testserver", "127.0.0.1:*"])
    with TestClient(app) as c:
        assert c.get("/api/journal").status_code == 401
        c.get("/?token=panel-secret")
        all_page = c.get("/api/journal?limit=3").json()
        assert all_page["total"] == 5 and len(all_page["entries"]) == 3
        refused = c.get("/api/journal?filter=refused").json()
        assert refused["total"] == 3
        reads = c.get("/api/journal?filter=read").json()
        assert {e["class"] for e in reads["entries"]} == {"read"}


def test_pwa_tile_serves_the_real_mark_not_the_placeholder(tmp_path):
    """The phone's home-screen tile is the app's face on a device the owner
    carries. It served a flat blue square until 2026-08-30."""
    from starlette.testclient import TestClient

    from vibebridge.audit import AuditLog
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState
    from vibebridge.web import _solid_png, build_app

    state = BridgeState.load(tmp_path / "state.json")
    app = build_app(consent=ConsentEngine(), audit=AuditLog(tmp_path / "a.log"),
                    state=state, notify=lambda *a, **k: None)
    r = TestClient(app).get("/icon-192.png")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")
    assert r.content != _solid_png(192)          # not the fallback square


def test_every_manifest_icon_size_is_served(tmp_path):
    from starlette.testclient import TestClient

    from vibebridge.audit import AuditLog
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState
    from vibebridge.web import build_app

    state = BridgeState.load(tmp_path / "state.json")
    app = build_app(consent=ConsentEngine(), audit=AuditLog(tmp_path / "a.log"),
                    state=state, notify=lambda *a, **k: None)
    client = TestClient(app)
    for size in (180, 192, 512):
        assert client.get(f"/icon-{size}.png").status_code == 200
    assert client.get("/icon-999.png").status_code == 404
