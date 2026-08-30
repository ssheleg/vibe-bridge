"""Consent engine — the reason this app exists.

The robot's brain calls tools over MCP; every tool carries a class:

  * READ  — executes immediately (look, list, notify). Logged, never asked.
  * ACT   — requires the owner's explicit approval via the menu bar:
            Allow once / Allow this class for GRANT_TTL / Deny.
            No answer within ASK_TIMEOUT = denied.

The engine is pure and synchronous from the caller's side: the MCP tool
thread calls `request()` and blocks; the menu-bar main loop polls
`pending()` and answers via `resolve()`. UI and policy never share state
except through this object, and every decision lands in the audit log.

The kill switch (`paused`) beats everything: while set, every tool —
READ included — is refused. A paused bridge is indistinguishable from a
closed laptop to the robot, and that is the point.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class ToolClass(Enum):
    READ = "read"
    ACT = "act"


class Decision(Enum):
    ALLOW = "allow"
    ALLOW_GRANT = "allow_grant"   # allow + grant this class for GRANT_TTL_S
    DENY = "deny"
    TIMEOUT = "timeout"
    PAUSED = "paused"
    AUTO = "auto"                 # READ class or active grant — no dialog


ASK_TIMEOUT_S = 60.0
GRANT_TTL_S = 15 * 60.0


@dataclass
class ConsentRequest:
    tool: str
    tool_class: ToolClass
    summary: str                  # human line shown in the dialog
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: float = field(default_factory=time.monotonic)
    decided_by: str | None = None  # which surface answered (panel/dialog/phone)
    _event: threading.Event = field(default_factory=threading.Event)
    _decision: Decision | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def resolve(self, decision: Decision, by: str = "") -> bool:
        """First valid decision wins; a later one is a no-op returning False.
        Surfaces race by design (dialog vs panel vs phone) — the loser must
        learn it lost, not overwrite the owner's actual answer."""
        with self._lock:
            if self._event.is_set():
                return False
            self._decision = decision
            self.decided_by = by or None
            self._event.set()
            return True


class ConsentEngine:
    def __init__(self, *, ask_timeout_s: float = ASK_TIMEOUT_S,
                 grant_ttl_s: float = GRANT_TTL_S,
                 clock=time.monotonic) -> None:
        self._clock = clock
        self._ask_timeout_s = ask_timeout_s
        self._grant_ttl_s = grant_ttl_s
        self._lock = threading.Lock()
        self._pending: list[ConsentRequest] = []
        self._grant_until: dict[ToolClass, float] = {}
        self.paused = False

    # -- called from the MCP tool thread -------------------------------------

    def request(self, tool: str, tool_class: ToolClass, summary: str) -> Decision:
        """Block until the owner (or policy) decides. Never raises."""
        if self.paused:
            return Decision.PAUSED
        if tool_class is ToolClass.READ:
            return Decision.AUTO
        with self._lock:
            until = self._grant_until.get(tool_class, 0.0)
            if self._clock() < until:
                return Decision.AUTO
            req = ConsentRequest(tool=tool, tool_class=tool_class,
                                 summary=summary)
            self._pending.append(req)
        answered = req._event.wait(timeout=self._ask_timeout_s)
        with self._lock:
            if req in self._pending:
                self._pending.remove(req)
        if not answered:
            return Decision.TIMEOUT
        decision = req._decision or Decision.DENY
        if decision is Decision.ALLOW_GRANT:
            with self._lock:
                self._grant_until[tool_class] = self._clock() + self._grant_ttl_s
        return decision

    # -- called from the menu-bar main loop -----------------------------------

    def pending(self) -> ConsentRequest | None:
        with self._lock:
            return self._pending[0] if self._pending else None

    def pending_all(self) -> list[ConsentRequest]:
        with self._lock:
            return list(self._pending)

    def resolve_by_id(self, req_id: str, decision: Decision,
                      by: str = "") -> bool:
        """Resolve one pending request by id. False = gone or already
        decided — the caller surfaces that as 'запрос уже решён/истёк'."""
        with self._lock:
            req = next((r for r in self._pending if r.id == req_id), None)
        if req is None:
            return False
        return req.resolve(decision, by=by)

    def grant_active(self, tool_class: ToolClass) -> float:
        """Seconds of grant remaining for the class (0 if none)."""
        with self._lock:
            left = self._grant_until.get(tool_class, 0.0) - self._clock()
        return max(0.0, left)

    def revoke_grants(self) -> None:
        with self._lock:
            self._grant_until.clear()


def allowed(decision: Decision) -> bool:
    return decision in (Decision.ALLOW, Decision.ALLOW_GRANT, Decision.AUTO)


def refusal_text(decision: Decision) -> str:
    """The words the ROBOT receives — honest, and phrased for a voice reply."""
    return {
        Decision.DENY: "Владелец отклонил действие.",
        Decision.TIMEOUT: "Владелец не ответил на запрос подтверждения "
                          "(60 секунд) — действие не выполнено.",
        Decision.PAUSED: "Мост поставлен владельцем на паузу — Мак сейчас "
                         "недоступен для действий.",
    }.get(decision, "Действие не разрешено.")
