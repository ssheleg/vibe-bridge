"""The robot's face on this computer — and the rules that keep it a face.

`docs/ux/vision.md` decides almost everything in here by refusing things:

* **«Не второй мозг».** Every line the mascot speaks was said by the robot or
  done by the bridge. There is no generator, no greeting, no idle chatter and
  no template that fires on a timer — which is why `says` is None most of the
  time, and why that is the correct behaviour rather than a gap.
* **Принцип 3 — пауза выглядит как отсутствие.** Paused, it is silent. Not
  drowsy, not "back soon": nothing. The agent must find a computer that looks
  switched off, and the owner must not be invited to answer a request the
  bridge has already refused.
* **«Не мессенджер».** One current line, and it expires — это про ПУЗЫРЬ
  персонажа, и остаётся правдой: маскот с транскриптом это чат-приложение с
  мультиком поверх.

  Рядом с ним живёт лента виджета, и она НЕ пузырь: сессия на 200 записей и
  1 МБ с ротацией, без загрузки файлов владельцем и с кнопкой «Новый»,
  которая обрывает нить, а не архивирует её. Граница названа в визии §6 и
  проверяется `tests/test_not_a_messenger.py` — доктрина в этом файле
  говорила обратное отгруженному продукту (U-8).

The state is derived, never stored: pause and pending come from the consent
engine, online from the robot's own status. Two sources of truth for "is the
bridge paused" is how a face ends up smiling at a stopped bridge.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class _Line:
    text: str
    kind: str          # event | chat | system
    at: float


class Mascot:
    """What the character shows right now. Cheap to build, safe to poll."""

    #: Floor for how long a line stays up: long enough to notice and read a
    #: short sentence.
    SAY_TTL_S = 25.0
    #: Ceiling. Past this the bubble has stopped being a bubble.
    SAY_TTL_MAX_S = 120.0
    #: Reading speed, characters per second, used to scale the two above. A
    #: fixed 25 s made a long answer vanish mid-sentence — reported as the
    #: widget "appearing and disappearing" (2026-08-31).
    READ_CPS = 14.0
    #: The bubble scrolls now, so a real answer fits instead of being cut at
    #: 220 characters with an ellipsis and nowhere to read the rest: the chat
    #: tab keeps its history only in the open page, so a reply sent from the
    #: pet had no second home.
    SAY_MAX_CHARS = 1200

    def __init__(self, *, consent, robot_state: dict, clock=time.time) -> None:
        self._consent = consent
        self._robot = robot_state
        self._clock = clock
        self._line: _Line | None = None
        self._thinking = False

    # ----------------------------------------------------------------- input

    def say(self, text: str, *, kind: str = "event") -> None:
        """Record something that actually happened. Callers pass the robot's
        own words or the bridge's own action — never a composed sentence."""
        text = (text or "").strip()
        if not text:
            return
        if getattr(self._consent, "paused", False):
            # Dropped, not queued. Holding it would make lifting the pause
            # replay a backlog — a messenger's behaviour, and it would also
            # break «пауза выглядит как отсутствие» a minute late instead of
            # never. The event is in the journal either way.
            return
        if len(text) > self.SAY_MAX_CHARS:
            text = text[: self.SAY_MAX_CHARS - 1].rstrip() + "…"
        now = self._clock()
        if (self._line is not None and self._line.text == text
                and now - self._line.at >= self._ttl(self._line.text)):
            # A status that repeats must not make its own bubble immortal.
            return
        self._line = _Line(text=text, kind=kind, at=now)

    def thinking(self, active: bool) -> None:
        self._thinking = bool(active)

    # ---------------------------------------------------------------- output

    def snapshot(self) -> dict:
        paused = bool(getattr(self._consent, "paused", False))
        pending = None if paused else self._consent.pending()
        online = bool(self._robot.get("online"))

        if paused:
            state = "paused"
        elif pending is not None:
            state = "asking"
        elif self._thinking:
            state = "thinking"
        elif not online:
            state = "offline"
        else:
            state = "idle"

        says = self._says(state, pending, online)
        return {
            "state": state,
            "says": says,
            # How long this line has left, so the surface can show that it is
            # going rather than have it vanish under the reader.
            "says_left_s": self._left(says, pending),
            "actionable": pending is not None,
            # Сколько осталось у ВОПРОСА (не у реплики): у питомца те же три
            # кнопки, и молчание у них значит отказ (A-9).
            "asks_left_s": (round(self._consent.remaining(pending), 1)
                            if pending is not None else None),
            "ask_timeout_s": (self._consent.ask_timeout_s
                              if pending is not None else None),
            "request_id": pending.id if pending is not None else None,
            "tool": pending.tool if pending is not None else None,
        }

    # --------------------------------------------------------------- private

    def _ttl(self, text: str) -> float:
        """Reading time for this line, floored and capped."""
        return min(self.SAY_TTL_MAX_S,
                   max(self.SAY_TTL_S, len(text) / self.READ_CPS))

    def _left(self, says: str | None, pending) -> float | None:
        if says is None or pending is not None or self._line is None:
            return None
        return max(0.0, self._ttl(self._line.text)
                   - (self._clock() - self._line.at))

    def dismiss(self) -> None:
        """Drop the current line now — the owner clicked it away."""
        self._line = None

    def _says(self, state: str, pending, online: bool) -> str | None:
        if state == "paused":
            return None                      # принцип 3
        if pending is not None:
            return pending.summary
        # A fresh line outranks the offline reason: news is what just
        # happened, a status is what we say when there is nothing to say.
        # The other way round hid a notification the moment the poller
        # marked the robot offline.
        line = self._line
        if line is not None and self._clock() - line.at < self._ttl(line.text):
            return line.text
        if state == "offline":
            # The robot's own reason, not our sympathy for it.
            reason = str(self._robot.get("reason") or "").strip()
            return reason or None
        return None
