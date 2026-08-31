"""The mascot — a face for things that already happened.

Three lines from `docs/ux/vision.md` shape every test here, and they are the
reason this module has no cleverness in it at all:

* **«Не второй мозг».** The mascot renders what the robot said or what the
  bridge did. It never composes a line of its own. The moment it greets you
  because nothing happened, the bridge has started thinking, and thinking is
  the robot's job — so `says` is None far more often than not.
* **Принцип 3 — пауза выглядит как отсутствие.** Paused, it goes quiet. Not
  "sleeping", not "снова тут через минуту": silent.
* **«Не мессенджер».** It holds the current line and lets it expire. History
  is the journal's job; a mascot that accumulates its own transcript has
  become the thing the vision refuses.
"""
from __future__ import annotations

import pytest

from vibebridge.consent import ConsentEngine, ToolClass
from vibebridge.mascot import Mascot


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock():
    return _Clock()


def _mascot(clock, *, consent=None, robot=None):
    return Mascot(consent=consent or ConsentEngine(),
                  robot_state=robot if robot is not None else {"online": True},
                  clock=clock)


# ------------------------------------------------------------------ silence

def test_it_says_nothing_when_nothing_has_happened(clock):
    snap = _mascot(clock).snapshot()
    assert snap["says"] is None
    assert snap["state"] == "idle"


def test_it_never_composes_a_line_of_its_own(clock):
    """Twenty ticks with no events must produce twenty silences. A greeting
    here would be the bridge thinking — see «Не второй мозг»."""
    m = _mascot(clock)
    for _ in range(20):
        clock.advance(30)
        assert m.snapshot()["says"] is None


# ------------------------------------------------------------------- asking

def test_a_pending_consent_is_what_it_speaks(clock):
    consent = ConsentEngine(ask_timeout_s=30)
    m = _mascot(clock, consent=consent)
    import threading
    threading.Thread(
        target=lambda: consent.request("open_app", ToolClass.ACT,
                                       "открыть Калькулятор"),
        daemon=True).start()
    for _ in range(200):
        if consent.pending() is not None:
            break
        clock.advance(0.01)

    snap = m.snapshot()
    assert snap["state"] == "asking"
    assert "Калькулятор" in snap["says"]
    assert snap["actionable"] is True          # the card can be answered here
    assert snap["request_id"]


def test_asking_outranks_everything_else(clock):
    """A waiting owner decision is the one thing that must not be buried
    under a status line."""
    consent = ConsentEngine(ask_timeout_s=30)
    m = _mascot(clock, consent=consent, robot={"online": False,
                                               "reason": "робот офлайн"})
    import threading
    threading.Thread(
        target=lambda: consent.request("open_app", ToolClass.ACT, "открыть"),
        daemon=True).start()
    for _ in range(200):
        if consent.pending() is not None:
            break
        clock.advance(0.01)
    assert m.snapshot()["state"] == "asking"


# -------------------------------------------------------------------- pause

def test_paused_is_silent_not_sleepy(clock):
    consent = ConsentEngine()
    consent.paused = True
    m = _mascot(clock, consent=consent)
    m.say("робот прислал сообщение", kind="event")

    snap = m.snapshot()
    assert snap["state"] == "paused"
    assert snap["says"] is None                # принцип 3: тишина, не «дремлю»


def test_pause_hides_even_a_pending_request(clock):
    """On pause the bridge refuses everything anyway; showing a card would
    invite the owner to answer something already decided."""
    consent = ConsentEngine()
    consent.paused = True
    m = _mascot(clock, consent=consent)
    assert m.snapshot()["actionable"] is False


def test_leaving_pause_does_not_replay_what_was_missed(clock):
    """The journal kept it. Replaying a queue would make the mascot a
    messenger with a backlog."""
    consent = ConsentEngine()
    consent.paused = True
    m = _mascot(clock, consent=consent)
    m.say("событие во время паузы", kind="event")
    consent.paused = False
    assert m.snapshot()["says"] is None


# ------------------------------------------------------------------ robot

def test_an_offline_robot_is_shown_with_its_reason(clock):
    m = _mascot(clock, robot={"online": False, "reason": "нет связи с домом"})
    snap = m.snapshot()
    assert snap["state"] == "offline"
    assert "нет связи" in snap["says"]


def test_a_robot_event_is_spoken_verbatim(clock):
    m = _mascot(clock)
    m.say("пришло сообщение в телеграм", kind="event")
    assert m.snapshot()["says"] == "пришло сообщение в телеграм"


def test_the_robot_thinking_has_its_own_state(clock):
    m = _mascot(clock)
    m.thinking(True)
    assert m.snapshot()["state"] == "thinking"
    m.thinking(False)
    assert m.snapshot()["state"] == "idle"


# ----------------------------------------------------------------- expiry

def test_a_line_expires_instead_of_becoming_history(clock):
    m = _mascot(clock)
    m.say("робот обновился", kind="event")
    assert m.snapshot()["says"] is not None
    clock.advance(Mascot.SAY_TTL_S + 1)
    assert m.snapshot()["says"] is None


def test_a_newer_line_replaces_the_previous_one(clock):
    m = _mascot(clock)
    m.say("первое", kind="event")
    m.say("второе", kind="event")
    assert m.snapshot()["says"] == "второе"


def test_it_keeps_no_transcript(clock):
    m = _mascot(clock)
    for i in range(50):
        m.say(f"строка {i}", kind="event")
    assert not hasattr(m, "history")
    assert m.snapshot()["says"] == "строка 49"


def test_the_same_line_twice_does_not_restart_a_finished_one(clock):
    """Otherwise a repeating status makes the bubble immortal."""
    m = _mascot(clock)
    m.say("робот в сети", kind="event")
    clock.advance(Mascot.SAY_TTL_S + 1)
    m.say("робот в сети", kind="event")
    assert m.snapshot()["says"] is None


# ------------------------------------------------------------------ safety

def test_a_very_long_line_is_trimmed_for_the_bubble(clock):
    m = _mascot(clock)
    m.say("щ" * 5000, kind="chat")
    says = m.snapshot()["says"]
    assert len(says) <= Mascot.SAY_MAX_CHARS


def test_speech_is_data_not_markup(clock):
    """The bubble renders this. A robot that says `<img onerror=…>` must not
    become script in the owner's panel."""
    m = _mascot(clock)
    m.say("<script>alert(1)</script>", kind="chat")
    assert "<script>" in m.snapshot()["says"]   # stored verbatim…
    # …and the surface is responsible for escaping; see test_mascot_page.


# ---------------------------------------------------- how long a line stays

def test_a_long_answer_gets_time_to_be_read(clock):
    """A fixed 25 s made a paragraph vanish mid-sentence, which is what the
    widget "appearing and disappearing" actually was."""
    m = _mascot(clock)
    long = "слово " * 100                       # ~600 characters
    m.say(long.strip(), kind="chat")
    clock.advance(30)
    assert m.snapshot()["says"] is not None      # still readable
    clock.advance(60)
    assert m.snapshot()["says"] is None


def test_a_short_line_still_goes_away_promptly(clock):
    m = _mascot(clock)
    m.say("готово", kind="event")
    clock.advance(Mascot.SAY_TTL_S + 1)
    assert m.snapshot()["says"] is None


def test_no_line_outlives_the_ceiling(clock):
    m = _mascot(clock)
    m.say("щ" * Mascot.SAY_MAX_CHARS, kind="chat")
    clock.advance(Mascot.SAY_TTL_MAX_S + 1)
    assert m.snapshot()["says"] is None


def test_the_surface_is_told_how_much_time_is_left(clock):
    m = _mascot(clock)
    m.say("привет", kind="event")
    first = m.snapshot()["says_left_s"]
    clock.advance(5)
    assert 0 < m.snapshot()["says_left_s"] < first


def test_a_pending_request_has_no_countdown(clock):
    """It waits for the owner, not for a timer of ours."""
    m = _mascot(clock)
    assert m.snapshot()["says_left_s"] is None


def test_the_owner_can_dismiss_a_line(clock):
    m = _mascot(clock)
    m.say("длинный ответ робота", kind="chat")
    m.dismiss()
    assert m.snapshot()["says"] is None


def test_a_real_answer_is_not_cut_at_two_hundred_characters(clock):
    m = _mascot(clock)
    answer = "Сейчас — тишина и порядок. " * 20      # ~540 chars
    m.say(answer.strip(), kind="chat")
    assert len(m.snapshot()["says"]) > 500


def test_fresh_news_outranks_the_offline_status(clock):
    """A status is what the mascot says when it has nothing to say. The other
    order hid a just-arrived notification the moment the poller marked the
    robot offline."""
    m = _mascot(clock, robot={"online": False, "reason": "нет связи с домом"})
    m.say("чайник вскипел", kind="notify")
    assert m.snapshot()["says"] == "чайник вскипел"

    clock.advance(Mascot.SAY_TTL_S + 1)
    # …and once it has expired, the status is what is left.
    assert m.snapshot()["says"] == "нет связи с домом"
