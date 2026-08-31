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
    # The panel uses the shared renderer; the pet window draws the same
    # character through `mascotSvg` and adds its own dialogue around it.
    assert "renderMascot" in page
    assert "mascotSvg" in window


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
    """It grew into a half-screen column of two-word lines and the whole
    window jumped as it appeared and expired (seen 2026-08-31)."""
    html = (WEBUI / "mascot.html").read_text()
    rule = html.split(".bubble{", 1)[1].split("}", 1)[0].replace(" ", "")
    assert "width:300px" in rule
    assert "max-height:230px" in rule
    assert "overflow-y:auto" in rule


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


def test_the_robots_markdown_is_rendered_not_shown_raw():
    """A reply arrived as `**Система:** работает 63+ часа` and the asterisks
    were on screen."""
    html = (WEBUI / "mascot.html").read_text()
    assert "<b>$1</b>" in html
    # …and escaping happens FIRST, so markup in a reply stays a reply.
    fmt = html.split("function fmt(", 1)[1].split("}", 1)[0]
    assert fmt.index("esc(text)") < fmt.index("replace")


def test_the_dialogue_keeps_no_history_beyond_the_session():
    """Vision, «Не мессенджер»: the turns live in this page and die with it;
    the context lives in the robot's brain."""
    html = (WEBUI / "mascot.html").read_text()
    assert "let stream = []" in html
    assert "localStorage" not in html and "sessionStorage" not in html


def test_a_new_dialogue_mints_a_new_session_for_the_brain():
    """The id now comes from the bridge — a page-local one did not survive a
    reload while the feed did."""
    html = (WEBUI / "mascot.html").read_text()
    assert "newDlg" in html
    assert '"/api/mascot/session",\n        {method:"POST"}' in html.replace(
        "\r", "")
    assert "JSON.stringify({text, session})" in html


def test_a_consent_question_cannot_be_dismissed_like_news():
    html = (WEBUI / "mascot.html").read_text()
    assert "if (bubble && !snap.actionable) bubble.onclick = dismiss" in html


def test_the_widget_repaints_only_when_something_changed():
    """Rewriting the DOM every second destroyed the character between
    `pointerdown` and `pointerup`, so a click never completed — and it is
    what made the widget look like it was flickering."""
    html = (WEBUI / "mascot.html").read_text()
    assert "if (!force && (dragging || key === painted)) return;" in html
    # The countdown changes every tick by design and must not force a repaint.
    key = html.split("const key = JSON.stringify(", 1)[1].split(")", 1)[0]
    assert "says_left_s" not in key


def test_the_character_keeps_its_place_when_a_bubble_appears():
    """`#mascot` was a plain block: a 300px bubble made it 300 wide and the
    character sat at its left edge, jumping sideways whenever the bubble came
    or went."""
    html = (WEBUI / "mascot.html").read_text()
    rule = html.split("#mascot{", 1)[1].split("}", 1)[0].replace(" ", "")
    assert "align-items:flex-end" in rule
    assert "flex-direction:column" in rule


def test_replies_events_and_notifications_share_one_stream():
    """The owner's own answer: one feed, because a reply, an event and a
    notification are all the robot communicating."""
    html = (WEBUI / "mascot.html").read_text()
    assert "/api/mascot/stream" in html
    assert "pullStream" in html
    # System lines are visually distinct from a reply, not hidden.
    assert 'cls = mine ? "me" : (t.kind && t.kind !== "chat" ? "sys" : "bot")' in html


def test_the_widget_shows_status_without_being_asked():
    """«мне хотелось бы видеть какой-то активный статус, а не сразу разговор»."""
    html = (WEBUI / "mascot.html").read_text()
    assert "/api/robot/status" in html
    assert "аптайм" in html and "не на связи" in html


def test_media_from_the_robot_is_previewed_with_a_way_out():
    html = (WEBUI / "mascot.html").read_text()
    for kind in ("image", "video", "audio", "link"):
        assert f'"{kind}"' in html
    assert "открыть в браузере" in html


def test_a_live_feed_does_not_redraw_over_the_owner_typing():
    """A blind repaint every few seconds would take the focus out of the
    input mid-sentence."""
    html = (WEBUI / "mascot.html").read_text()
    assert "if (stream.length !== before) await renderDialog();" in html


def test_a_notification_reaches_the_stream_as_well_as_the_screen(tmp_path):
    from vibebridge.audit import AuditLog
    from vibebridge.config import Settings
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState
    from vibebridge.web import build_app

    shown = []
    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings(),
                    notify=lambda t, x: shown.append((t, x)))
    c = TestClient(app)
    c.cookies.set("vb_panel", "pt")

    from vibebridge import capabilities as caps
    caps._notifier("Вася", "чайник вскипел")

    items = c.get("/api/mascot/stream").json()["items"]
    assert any("чайник вскипел" in i["text"] for i in items)
    assert shown == [("Вася", "чайник вскипел")]      # still on screen too


def test_the_conversation_id_outlives_the_page(tmp_path):
    """The feed is server-side and survives a reload; a page-local id did
    not. The owner saw their own history while the robot answered «это новая
    сессия» — reported with a screenshot 2026-08-31."""
    from vibebridge.audit import AuditLog
    from vibebridge.config import Settings
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState
    from vibebridge.web import build_app

    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings())
    c = TestClient(app)
    c.cookies.set("vb_panel", "pt")

    first = c.get("/api/mascot/session").json()["session"]
    assert first
    assert c.get("/api/mascot/session").json()["session"] == first  # a reload

    # …and it is remembered across a restart of the bridge itself.
    reloaded = BridgeState.load(tmp_path / "state.json")
    assert reloaded.pet_session == first


def test_a_new_conversation_mints_a_new_id(tmp_path):
    from vibebridge.audit import AuditLog
    from vibebridge.config import Settings
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState
    from vibebridge.web import build_app

    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings())
    c = TestClient(app)
    c.cookies.set("vb_panel", "pt")

    before = c.get("/api/mascot/session").json()["session"]
    after = c.post("/api/mascot/session").json()["session"]
    assert after != before
    # The feed starts clean; the old turns are in the journal, not lost.
    assert c.get("/api/mascot/stream").json()["items"] == []


def test_the_session_endpoint_is_guarded(tmp_path):
    from vibebridge.audit import AuditLog
    from vibebridge.config import Settings
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState
    from vibebridge.web import build_app

    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings())
    assert TestClient(app).get("/api/mascot/session").status_code == 401


def test_quick_phrases_only_greet_an_empty_conversation():
    """Three buttons above the input on every turn stop being an offer and
    become furniture."""
    html = (WEBUI / "mascot.html").read_text()
    assert "(quick.length && !stream.length)" in html


def test_the_page_asks_the_bridge_for_the_conversation_id():
    html = (WEBUI / "mascot.html").read_text()
    assert "/api/mascot/session" in html
    assert 'session = "pet-" + Math.random' not in html
    assert "await ensureSession();" in html


def test_the_thread_is_bounded_and_dropped_with_the_session(tmp_path):
    """Enough for the brain to follow what was just said, not an archive."""
    from pathlib import Path

    from vibebridge.audit import AuditLog
    from vibebridge.config import Settings
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState
    from vibebridge.web import build_app

    web = (Path(__import__("vibebridge").__file__).parent / "web.py").read_text()
    assert "deque(maxlen=20)" in web            # bounded
    assert "chat_history.clear()" in web        # forgotten on a new session

    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings())
    c = TestClient(app)
    c.cookies.set("vb_panel", "pt")
    c.get("/api/mascot/session")
    assert c.post("/api/mascot/session").status_code == 200
