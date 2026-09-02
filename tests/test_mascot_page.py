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
from tests.webui_rules import (
    code_of,
    keyframes,
    reduced_motion_kills_animation,
)
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
    for shape in ("calm", "scan", "wide", "closed"):
        assert f"{shape}:" in js
    # Ветка reduced-motion проверяется ПРАВИЛОМ, а не строкой: `"…" in js`
    # держался лишь на том, что слово пока встречается в файле один раз —
    # один поясняющий комментарий, и он снова ничего не проверяет (A-32).
    assert reduced_motion_kills_animation("mascot.js")


def test_the_window_is_wide_enough_for_all_three_answers():
    """At 300px «Отклонить» was clipped off the right edge (seen on screen
    2026-08-31). A refusal button you cannot reach is the worst one to lose.

    The answers moved to the companion window when the widget was split in
    two, so the width that has to hold them is that window's."""
    from vibebridge import desktop as mw
    assert mw.SIDE_START[0] >= 340


def test_the_bubble_cannot_outgrow_the_window():
    """It grew into a half-screen column of two-word lines and the whole
    window jumped as it appeared and expired (seen 2026-08-31)."""
    import re

    from vibebridge import desktop as mw

    html = (WEBUI / "mascot.html").read_text()
    rule = html.split(".bubble{", 1)[1].split("}", 1)[0].replace(" ", "")
    # A-43: было `"width:300px" in rule` — текущее значение вместо свойства.
    # Свойство в докстроке: пузырь ОГРАНИЧЕН и не перерастает окно.
    width = int(re.search(r"(?<!max-)width:(\d+)px", rule).group(1))
    height = int(re.search(r"max-height:(\d+)px", rule).group(1))
    assert 0 < width <= mw.SIDE_START[0], (
        f"пузырь шире окна-компаньона: {width} против {mw.SIDE_START[0]}")
    assert 0 < height <= 400, f"пузырь высотой {height}px — это колонка"
    assert "overflow-y:auto" in rule       # и он скроллится, а не растёт


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

    c.post("/api/robot/chat", json={"text": "привет"})   # что-то было сказано
    before = c.get("/api/mascot/session").json()["session"]
    after = c.post("/api/mascot/session").json()["session"]
    assert after != before

    # Новый РАЗГОВОР — чистый контекст мозга, а не амнезия. Прежняя версия
    # требовала пустую ленту и объясняла это тем, что «старые ходы остаются в
    # журнале»: журнал — это аудит решений по инструментам, событий робота в
    # нём нет вовсе, и сказанное исчезало насовсем (A-19).
    items = c.get("/api/mascot/stream").json()["items"]
    assert items, "лента стёрлась — сказанное роботом потеряно"
    assert items[-1]["kind"] == "session", "граница разговора не отмечена"


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


def test_the_head_says_a_notification_too(tmp_path):
    """«лучше чтобы голова робота говорила это» — a notification is the robot
    talking, not only a grey system banner."""
    from vibebridge.audit import AuditLog
    from vibebridge.config import Settings
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState
    from vibebridge.web import build_app

    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings(),
                    notify=lambda t, x: (True, ""))
    c = TestClient(app)
    c.cookies.set("vb_panel", "pt")

    from vibebridge import capabilities as caps
    caps._notifier("Вася", "чайник вскипел")

    assert "чайник вскипел" in (c.get("/api/mascot").json()["says"] or "")


# ── skins and the motion doctrine ─────────────────────────────────────────

def test_a_skin_can_be_swapped_without_touching_the_states():
    """A skin decides how a state LOOKS; the bridge decides what it MEANS.
    That line is what separates a skin from a fork of the mascot."""
    js = (WEBUI / "mascot.js").read_text()
    assert "registerMascotSkin(\"vasya\"" in js
    assert "registerMascotSkin(\"dot\"" in js
    # A skin system with one skin is an assertion, not a contract.
    assert js.count("registerMascotSkin(") >= 3        # helper + two skins


def test_pause_and_offline_carry_no_motion_at_all():
    """«Пауза выглядит как отсутствие» — a breathing figure over a stopped
    bridge says the opposite of what pause means."""
    js = (WEBUI / "mascot.js").read_text()
    rule = js.split(".vb-paused, .vb-offline{", 1)[1].split("}", 1)[0]
    assert "animation:none" in rule.replace(" ", "")
    assert ".vb-paused *, .vb-offline *{animation:none}" in js


def test_only_compositor_safe_properties_are_animated():
    """The doctrine bans animating layout. The previous version animated SVG
    geometry attributes (`r`, `cx`), which is exactly that.

    A-33: разбор резал блок по первой `}` и видел только ПЕРВЫЙ шаг.
    Доказано подсадкой — `width` в шаге `50%` проходил банлист насквозь. И
    сканировался один `mascot.js`, тогда как анимации есть и на странице
    виджета: доктрина проверяла половину поверхностей.
    """
    banned = ("width", "height", "top:", "left:", "margin", "padding",
              "font-size")
    seen = 0
    for page in ("mascot.js", "mascot.html", "index.html"):
        for name, frame in keyframes(page).items():
            seen += 1
            for prop in banned:
                assert prop not in frame, f"{page}: {name} анимирует {prop}"
            assert "transform" in frame or "opacity" in frame, \
                f"{page}: {name} не двигает ни transform, ни opacity"
        assert "attributeName" not in code_of(page)   # no SMIL geometry
    # Канарейка на сам разбор: молчаливый ноль — самый вероятный способ
    # для этой проверки снова стать бесполезной, и он выглядит как
    # успех. Число — пол, а не точная опись.
    assert seen >= 4, f"доктрина увидела всего {seen} анимаций"


def test_motion_stays_inside_the_doctrine_ceiling():
    """UI motion stays at or under 300 ms, and `ease-in` is banned.

    A-43: раньше здесь стояло `assert "VB_DUR = 220" in js` — то есть текущее
    ЗНАЧЕНИЕ вместо свойства из докстроки. Такой ассерт ломается от
    переформатирования (`VB_DUR=220`) и молчит при `VB_DUR = 900`, которое
    доктрину и нарушает. Число теперь ВЫЧИСЛЯЕТСЯ движком и сравнивается с
    потолком.
    """
    from tests.js_runner import run

    js = (WEBUI / "mascot.js").read_text()
    line = next(ln for ln in js.splitlines() if ln.startswith("const VB_DUR"))
    dur = run([line], "console.log(JSON.stringify(VB_DUR))")
    assert isinstance(dur, int | float) and 0 < dur <= 300, (
        f"длительность {dur} мс выходит за потолок доктрины (300 мс)")
    assert "cubic-bezier(0.23, 1, 0.32, 1)" in js      # the doctrine's ease-out
    assert "ease-in;" not in js and "ease-in," not in js


def test_an_unknown_skin_is_refused_at_write_time(tmp_path, monkeypatch):
    """Otherwise a typo silently draws the default and the owner wonders why
    nothing changed."""
    import pytest

    from vibebridge import config as cfg

    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.toml")
    cfg.load(create=True)
    with pytest.raises(ValueError):
        cfg.update({"mascot_skin": "дракон"})
    cfg.update({"mascot_skin": "dot"})
    assert cfg.load().mascot_skin == "dot"


def test_the_state_carries_its_skin(tmp_path):
    """Otherwise every surface has to ask twice and they can disagree."""
    from vibebridge.audit import AuditLog
    from vibebridge.config import Settings
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState
    from vibebridge.web import build_app

    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings(mascot_skin="dot"))
    c = TestClient(app)
    c.cookies.set("vb_panel", "pt")
    assert c.get("/api/mascot").json()["skin"] == "dot"


def test_the_token_redirect_keeps_the_surface_marker(tmp_path):
    """The widget is two windows and the URL is what tells each which it is.

    The redirect that trades the token for a cookie used to rebuild the target
    as a bare "/mascot", dropping every other parameter — so BOTH windows
    loaded as the pet, the companion page never existed, and clicking the head
    silently did nothing. Measured 2026-08-31 from the journal: two `hello`
    messages, both saying `surface = pet`.
    """
    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings())
    c = TestClient(app, follow_redirects=False)
    r = c.get("/mascot?token=pt&surface=side")
    assert r.status_code == 303
    assert r.headers["location"] == "/mascot?surface=side"
    # …and the token itself never survives into the next URL.
    assert "token" not in r.headers["location"]


def test_the_redirect_target_is_clean_when_there_is_nothing_to_keep(tmp_path):
    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings())
    c = TestClient(app, follow_redirects=False)
    r = c.get("/mascot?token=pt")
    assert r.status_code == 303 and r.headers["location"] == "/mascot"


def test_each_window_announces_itself_once(tmp_path):
    """The line that would have ended a four-round hunt in one round."""
    from vibebridge import desktop as mw

    said = []
    mw._Bridge(None, report=lambda line, ok=False: said.append((line, ok))) \
        .handle({"type": "hello", "surface": "side"})
    assert said == [("виджет: окно «side» открылось", True)]


def test_the_state_remembers_where_the_pet_was_left(tmp_path):
    """It came back to the bottom-right corner after every restart, however
    far the owner had dragged it (reported 2026-08-31 — they moved it to the
    top of the screen and a relaunch undid that)."""
    from vibebridge.state import BridgeState

    st = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    assert st.pet_pos is None                 # nothing remembered yet
    st.pet_pos = [1420.0, 880.0]
    st.save()
    again = BridgeState.load(tmp_path / "state.json")
    assert again.pet_pos == [1420.0, 880.0]


def test_an_older_state_file_without_the_field_still_loads(tmp_path):
    """A state file written by 0.15.0 has no `pet_pos`; the field is optional
    and its absence must not be an upgrade that fails to start."""
    import json

    from vibebridge.state import BridgeState

    path = tmp_path / "state.json"
    path.write_text(json.dumps({"panel_token": "pt", "mode": "gateway"}))
    assert BridgeState.load(path).pet_pos is None


def test_our_code_is_never_stored_by_the_browser(client):
    """WKWebView kept the widget's page across an app restart, so the pet ran
    yesterday's JavaScript inside today's app: a message the new page was
    supposed to post never arrived, and the bug read as a broken native
    handler for three rounds. Diagnosed 2026-09-01 by deleting
    `~/Library/WebKit/<bundle-id>` and watching the same drag work.

    A payload update replaces exactly these files, so a cache that outlives
    the update makes the update mechanism a lie.
    """
    for path in ("/", "/mascot", "/mascot.js", "/tokens.css", "/sw.js",
                 "/offline.html"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "no-store" in r.headers.get("cache-control", ""), path


def test_images_are_still_cacheable_because_the_pwa_needs_them(client):
    """The service worker caches icons on purpose, for offline. `no-store`
    there would fight the feature it is meant to protect."""
    r = client.get("/icon-192.png")
    assert r.status_code == 200
    assert "no-store" not in r.headers.get("cache-control", "")


def test_the_widget_actually_applies_the_character_motion_layer():
    """Найдено проверкой НА ЭКРАНЕ 2026-09-01, а не тестами.

    `mascotStyles()` — единственное место, где живут дыхание, мигание, пульс
    запроса, замирание на паузе (`.vb-paused{animation:none}`) и ветка
    reduced-motion. Оно вызывается внутри `renderMascot()`, которым пользуется
    ПАНЕЛЬ; виджет строит разметку сам и стилей не получал вовсе. Единственной
    анимацией головы было локальное правило `.mascot-body svg`, ничего не
    знавшее о состоянии, — поэтому на паузе питомец продолжал дышать.

    Замерено кадрами: до фикса 1 уникальный кадр из 5 в покое (не дышит) и 1
    из 5 на паузе; после — 5 из 5 в покое и 1 из 5 на паузе.
    """
    html = (WEBUI / "mascot.html").read_text()
    # Вызов на верхнем уровне страницы, а не внутри функции, которую страница
    # не использует.
    assert "\nmascotStyles();" in html, "виджет не подключает моушен-слой"
    # …и локальной копии, которая перебивала правило паузы, больше нет.
    import re
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert "@keyframes vb-breathe" not in css, "вернулась вторая копия дыхания"
    assert ".mascot-body svg{animation" not in css.replace(" ", ""), (
        "локальное правило снова перебивает состояние")


def test_pause_has_no_motion_in_the_shared_stylesheet():
    """Принцип 3 визии: «пауза выглядит как отсутствие». Правило существует в
    `mascot.js`, и именно поэтому важно, что виджет его подключает."""
    js = (WEBUI / "mascot.js").read_text()
    rules = js.split("mascotStyles", 1)[1].replace(" ", "")
    assert ".vb-paused,.vb-offline{animation:none}" in rules
    assert ".vb-paused*,.vb-offline*{animation:none}" in rules



def test_the_pet_also_shows_the_deadline():
    """У питомца те же три кнопки — значит и то же умолчание: молчание есть
    отказ. Поверхность, которая об этом молчит, обманывает владельца ровно
    так же, как панель до A-9."""
    import re
    from pathlib import Path

    import vibebridge

    webui = Path(vibebridge.__file__).parent / "webui"
    for name in ("mascot.html", "mascot.js"):
        text = (webui / name).read_text()
        code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
        assert "asks_left_s" in code, f"{name}: срок вопроса не читается"
        assert "ask_timeout_s" in code, f"{name}: окно ожидания не читается"
        assert "истекло" in code, f"{name}: истёкший вопрос не назван словом"


def test_the_pet_says_when_a_click_arrived_too_late():
    """Ветка отправки решения у питомца была `catch(e){}`: он молча съедал и
    «запрос истёк», и «мост не ответил». Владелец нажимал «Разрешить» и не
    узнавал ничего (A-10)."""
    import re
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui" / "mascot.html").read_text()
    code = re.sub(r"/\*.*?\*/", "", page, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    fn = code.split("async function decide(", 1)[1].split("\n}", 1)[0]
    assert "r.ok" in fn, "ответ моста не проверяется"
    assert "lateNote" in fn, "опоздавший клик остаётся без слов"
    # Свойство не «нет пустых catch», а «сеть, которая не ответила, названа
    # словом». Пустой catch вокруг разбора JSON законен — запасная строка
    # там уже стоит, и глотать ему нечего.
    assert "Мост не ответил" in fn, "провал отправки снова беззвучен"


def test_the_pet_window_says_a_word_about_its_state():
    """Поправка пака дословно: «он не заменяет собой текст: рядом с ним
    всегда слово о состоянии».

    Окно питомца строило разметку само, минуя `renderMascot()`, и теряло даже
    `title`. По той же доктрине на паузе и офлайне персонаж НЕПОДВИЖЕН — то
    есть ровно в двух состояниях из пяти окно не сообщало ничего вообще: ни
    движением, ни словом (V-6).
    """
    window = code_of("mascot.html")
    # Веток `IS_PET` в файле несколько (одна из них — про toggle), и разбор
    # «по первой» брал не ту: та же ошибка, что резала кадры по первой `}`
    # в A-33. Нужна ИМЕННО та, что рисует фигурку.
    drawing = [chunk.split("return;", 1)[0]
               for chunk in window.split("if (IS_PET){")[1:]
               if "mascotSvg" in chunk.split("return;", 1)[0]]
    assert len(drawing) == 1, (
        f"веток окна питомца, которые рисуют фигурку: {len(drawing)}")
    body = drawing[0]
    assert "MASCOT_STATES" in body, (
        "окно питомца не берёт состояние из общего словаря — значит слово "
        "будет своё, и разойдётся с панелью")
    # `s.label` встречается в ветке ДВАЖДЫ — в видимом слове и в `title`, —
    # и первая версия этого ассерта проверяла просто его наличие. Подсадка
    # показала: сношу видимое слово, `title` остаётся, гейт зелёный. Ровно
    # класс A-32, найденный собственной подсадкой на собственном гейте.
    # Различие несёт помощник экранирования: `vbEsc` — для текста,
    # `vbEscAttr` — для атрибута.
    assert "pet-word" in body, "в окне питомца нет ВИДИМОГО слова о состоянии"
    assert "vbEsc(s.label)" in body, (
        "слово о состоянии не вставляется как текст — значит его нет или "
        "оно не экранировано")
    assert "vbEscAttr(s.label)" in body, "у фигурки нет даже подсказки"


def test_the_word_cannot_stretch_the_pet_window():
    """Окно питомца по сценарию не меняет размер (`desktop.PET_SIZE` 104px,
    фигурка 72). Длинная подпись обязана обрезаться, а не растянуть окно."""
    css = code_of("mascot.html")
    rule = css.split(".pet-word{", 1)
    assert len(rule) == 2, "подпись состояния не одета"
    body = rule[1].split("}", 1)[0].replace(" ", "").replace("\n", "")
    assert "max-width:100%" in body and "text-overflow:ellipsis" in body, body
    assert "pointer-events:none" in body, (
        "подпись перехватывает перетаскивание фигурки")


def test_every_state_has_a_word_to_say():
    """Слово берётся из словаря — значит словарь обязан покрывать все пять
    состояний, а не четыре."""
    js = code_of("mascot.js")
    states = js.split("const MASCOT_STATES = {", 1)[1].split("\n};", 1)[0]
    for state in ("idle", "thinking", "asking", "paused", "offline"):
        chunk = states.split(f"{state}:", 1)
        assert len(chunk) == 2, f"нет состояния {state}"
        assert "label:" in chunk[1].split("},", 1)[0], (
            f"у состояния «{state}» нет слова")
