"""The two surfaces the mascot lives on, and what they must not do.

The floating window answers consent requests, so it is the panel by another
name and carries the same auth. The character itself is drawn once and used
twice — a mascot implemented separately per surface is a mascot with two moods
for one bridge.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import vibebridge
from vibebridge.audit import AuditLog
from vibebridge.config import Settings
from vibebridge.consent import ConsentEngine
from vibebridge.state import BridgeState
from vibebridge.web import build_app

WEBUI = Path(vibebridge.__file__).parent / "webui"


@pytest.fixture()
def client(tmp_path):
    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings())
    c = TestClient(app)
    c.cookies.set("vb_panel", "pt")
    return c


def test_the_window_page_needs_the_panel_token(tmp_path):
    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings())
    assert TestClient(app).get("/mascot").status_code == 401


def test_a_tokened_link_logs_the_window_in(tmp_path):
    """The native window opens a URL; it cannot set a cookie by itself."""
    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings())
    c = TestClient(app)
    r = c.get("/mascot?token=pt", follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/mascot").status_code == 200


def test_the_state_endpoint_is_guarded(tmp_path):
    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings())
    assert TestClient(app).get("/api/mascot").status_code == 401


def test_the_state_endpoint_answers_with_a_snapshot(client):
    body = client.get("/api/mascot").json()
    assert set(body) >= {"state", "says", "actionable", "request_id"}


def test_the_character_is_one_implementation_for_both_surfaces(client):
    """`mascot.js` is served to the window and included by the panel. Two
    copies would drift into two moods for one bridge."""
    assert client.get("/mascot.js").status_code == 200
    page = (WEBUI / "index.html").read_text()
    window = (WEBUI / "mascot.html").read_text()
    assert 'src="/mascot.js"' in page
    assert 'src="/mascot.js"' in window
    assert "renderMascot" in page and "renderMascot" in window


def test_the_bubble_escapes_what_the_robot_said():
    """A reply containing markup must stay a reply. `mascot.say` stores text
    verbatim on purpose, so the escaping has to be here."""
    js = (WEBUI / "mascot.js").read_text()
    assert "textContent" in js                     # escape helper present
    assert "esc(snap.says)" in js


def test_the_window_page_is_transparent_for_a_borderless_panel():
    """The native panel behind it is clear; an opaque page would draw a grey
    rectangle on the owner's desktop."""
    html = (WEBUI / "mascot.html").read_text()
    assert "background:transparent" in html


def test_the_window_says_so_when_the_bridge_stops_answering():
    """A cheerful mascot over a dead bridge is the lie this project spends
    its whole design refusing."""
    html = (WEBUI / "mascot.html").read_text()
    assert "мост не отвечает" in html


def test_states_never_rely_on_colour_alone():
    """Style-pack rule: colour never carries a state by itself. Here the eyes
    change shape too, so the state survives greyscale."""
    js = (WEBUI / "mascot.js").read_text()
    for shape in ("calm", "busy", "wide", "closed"):
        assert f"{shape}:" in js
    assert "prefers-reduced-motion" in (WEBUI / "mascot.html").read_text()


def test_the_window_is_wide_enough_for_all_three_answers():
    """At 300px «Отклонить» was clipped off the right edge (seen on screen
    2026-08-31). A refusal button you cannot reach is the worst one to lose."""
    from vibebridge import desktop as mw
    assert mw.DEFAULT_SIZE[0] >= 340


def test_the_bubble_cannot_outgrow_the_window():
    html = (WEBUI / "mascot.html").read_text()
    assert "max-width:100%" in html


def test_the_panel_wires_the_mascot_exactly_once():
    """A careless edit defined the ticker three times and left a setInterval
    inside `attachRobot`, so every manual attach leaked another timer."""
    page = (WEBUI / "index.html").read_text()
    assert page.count("async function tickMascot") == 1
    assert page.count("setInterval(tickMascot") == 1


def test_the_welcome_card_still_loads_on_first_paint():
    page = (WEBUI / "index.html").read_text()
    tail = page.rsplit("</script>", 2)[-2]
    assert "loadOnboarding();" in tail


def test_the_mascot_speaks_the_reply_under_the_key_the_client_returns(tmp_path):
    """`RobotClient.chat` answers `{ok, reply}`. Reading `text` here made the
    mascot go quiet after every chat turn — it showed "thinking" and then
    nothing, while the chat itself worked fine."""
    import asyncio

    from vibebridge.robot import RobotClient

    class _Reply:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "жив"}}]}

    class _Http:
        async def post(self, *a, **kw):
            return _Reply()

    client = RobotClient(base_url="https://r", chat_url="https://r",
                         chat_key="k", name="Вася", http=_Http())
    answer = asyncio.run(client.chat("как дела"))
    # The contract the panel and the mascot must both read.
    assert answer["ok"] and answer["reply"] == "жив"
    assert "text" not in answer

    from pathlib import Path

    import vibebridge
    web = (Path(vibebridge.__file__).parent / "web.py").read_text()
    assert 'answer.get("reply")' in web
    assert 'answer.get("text")' not in web
