"""Consent engine — the reason this app exists.

The robot's brain calls tools over MCP; every tool carries a class:

  * READ  — executes immediately (look, list, notify). Logged, never asked.
  * ACT   — requires the owner's explicit approval via the menu bar:
            Allow once / Allow THIS TOOL for GRANT_TTL / Deny.
            No answer within ASK_TIMEOUT = denied.

Грант ключуется на ИНСТРУМЕНТ, а не на класс. До 2026-09-02 он ключевался
на класс, и «да» на «открыть ссылку» на пятнадцать минут молча включало
AppleScript, буфер обмена и Shortcuts — кнопка об этом не говорила ни
слова (A-8). Визия §1 обещает согласие ПОИМЁННОЕ; грант на класс — это
«всё или ничего» внутри класса, ровно то, от чего продукт уходит.

The engine is pure and synchronous from the caller's side: the MCP tool
thread calls `request()` and blocks; the menu-bar main loop polls
`pending()` and answers via `resolve()`. UI and policy never share state
except through this object, and every decision lands in the audit log.

Опрос СОСТОЯНИЯ законен — поверхность рисует то, что есть сейчас. А вот
«что нового» движок говорит САМ: `subscribe(fn)`, события `asked` и
`closed`. До 2026-09-02 новизну независимо вычисляли три поллера, и
обещание докстроки заменить поллинг хуками оставалось обещанием (F-12).

The kill switch (`paused`) beats everything: while set, every tool —
READ included — is refused. A paused bridge is indistinguishable from a
closed laptop to the robot, and that is the point.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum


class ToolClass(Enum):
    READ = "read"
    ACT = "act"


class Decision(Enum):
    ALLOW = "allow"
    ALLOW_GRANT = "allow_grant"   # allow + grant THIS TOOL for GRANT_TTL_S
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


@dataclass(frozen=True)
class Outcome:
    """Чем закончился запрос — для того, кто пришёл слишком поздно."""
    id: str
    decision: Decision
    by: str


class ConsentEngine:
    def __init__(self, *, ask_timeout_s: float = ASK_TIMEOUT_S,
                 grant_ttl_s: float = GRANT_TTL_S,
                 ask_for_read: bool = False,
                 clock=time.monotonic) -> None:
        self._clock = clock
        self._ask_timeout_s = ask_timeout_s
        self._grant_ttl_s = grant_ttl_s
        # Vision §9.1 answers READ with "в журнале сразу после", so this is
        # off by default. It exists because `screenshot` is a READ and some
        # owners want to be asked before their screen is read.
        self._ask_for_read = ask_for_read
        self._lock = threading.Lock()
        self._pending: list[ConsentRequest] = []
        # Чем ЗАКОНЧИЛИСЬ недавние запросы. Нужен ровно для одного ответа:
        # опоздавший клик спрашивает «а что было-то?», и «уже решён ИЛИ
        # истёк» — две разные новости в одной строке (A-10).
        self._closed: deque[Outcome] = deque(maxlen=32)
        self._grant_until: dict[str, float] = {}   # по ИНСТРУМЕНТУ (A-8)
        self.paused = False
        # Кому сказать, что появился НОВЫЙ запрос или что запрос закрылся.
        # До 2026-09-02 наблюдателя не было, и «что нового» независимо
        # вычисляли три поллера: таймер меню-бара сравнивал снимок, вотчер
        # пушей держал множество из пятисот id, маскот брал `pending()` при
        # каждой отрисовке. Три ответа на один вопрос расходятся не «если», а
        # «когда» (F-12).
        self._watchers: list = []
        #: Последняя ошибка наблюдателя. Движок из-за чужого сбоя не падает —
        #: но и не молчит: подписчик, который тихо не работает, хуже
        #: отсутствующего.
        self.last_watcher_error: str | None = None

    def subscribe(self, fn) -> "callable":
        """Слушать события согласия. Возвращает функцию отписки.

        `fn(event, req)` где event — `"asked"` или `"closed"`. Зовётся ВНЕ
        замка: подписчик, который тронет движок из колбэка, иначе получил бы
        взаимную блокировку, а не сообщение об ошибке.

        Поток — тот, в котором событие случилось (для `"asked"` это поток
        инструмента MCP). Подписчику из asyncio-петли полагается сделать
        `loop.call_soon_threadsafe` самому: движок не знает про петли.
        """
        self._watchers.append(fn)
        return lambda: self._watchers.remove(fn) if fn in self._watchers else None

    def _announce(self, event: str, req) -> None:
        """Сказать всем. Сбой одного не мешает остальным и не роняет движок."""
        for fn in list(self._watchers):
            try:
                fn(event, req)
            except Exception as exc:                    # noqa: BLE001
                # НЕ молчим: сохраняем причину. Согласие — ядро продукта, и
                # падать из-за подписчика оно не имеет права; но подписчик,
                # который тихо не работает, хуже отсутствующего.
                self.last_watcher_error = f"{type(exc).__name__}: {exc}"

    # -- called from the MCP tool thread -------------------------------------

    def request(self, tool: str, tool_class: ToolClass, summary: str) -> Decision:
        """Block until the owner (or policy) decides. Never raises."""
        if self.paused:
            return Decision.PAUSED
        if tool_class is ToolClass.READ and not self._ask_for_read:
            return Decision.AUTO
        with self._lock:
            until = self._grant_until.get(tool, 0.0)
            if self._clock() < until:
                return Decision.AUTO
            req = ConsentRequest(tool=tool, tool_class=tool_class,
                                 summary=summary, created=self._clock())
            self._pending.append(req)
        self._announce("asked", req)          # вне замка — см. `subscribe`
        req._event.wait(timeout=self._ask_timeout_s)
        # Запрос ЗАКРЫВАЕТСЯ, а не просто убирается из очереди. Раньше по
        # таймауту он исчезал из `_pending` с невыставленным событием — и
        # `resolve()` секундой позже возвращал True: панель показывала успех,
        # системный диалог молчал, действие не исполнялось. Владелец нажимал
        # «Разрешить» и не узнавал, что не разрешил ничего (A-10).
        #
        # Гонка в зазоре решается тем же first-wins: если поверхность успела
        # ответить, `resolve` вернёт False и её решение останется в силе —
        # владелец нажал ДО срока, и его ответ старше нашего вывода.
        req.resolve(Decision.TIMEOUT, by="timeout")
        with self._lock:
            if req in self._pending:
                self._pending.remove(req)
        decision = req._decision or Decision.DENY
        with self._lock:
            self._closed.append(Outcome(id=req.id, decision=decision,
                                        by=req.decided_by or "timeout"))
        if decision is Decision.ALLOW_GRANT:
            with self._lock:
                self._grant_until[tool] = self._clock() + self._grant_ttl_s
        self._announce("closed", req)
        return decision

    # -- called from the menu-bar main loop -----------------------------------

    @property
    def ask_timeout_s(self) -> float:
        """Сколько живёт вопрос. Поверхности показывают ЭТО число, а не
        зашитую константу: молчание — тоже решение, и владелец должен
        видеть, сколько у него осталось (A-9)."""
        return self._ask_timeout_s

    @property
    def grant_ttl_s(self) -> float:
        """Сколько живёт грант. Поверхности показывают ЭТО, а не «15 мин»."""
        return self._grant_ttl_s

    def grant_label(self) -> str:
        """«Такие — 15 мин» жило зашитым на ЧЕТЫРЁХ поверхностях, при том что
        `grant_ttl_s` — настройка: поменяв её, владелец получал кнопку,
        обещающую не то, что произойдёт (U-13).

        Слово, а не число: «на 15 мин» и «на 1 ч» читаются по-разному, а
        «на 900 с» не читается вовсе.
        """
        seconds = int(self._grant_ttl_s)
        if seconds % 3600 == 0 and seconds >= 3600:
            return f"{seconds // 3600} ч"
        if seconds % 60 == 0 and seconds >= 60:
            return f"{seconds // 60} мин"
        return f"{seconds} с"

    def remaining(self, req: ConsentRequest | None) -> float:
        """Сколько секунд у запроса осталось (0 — уже нисколько)."""
        if req is None:
            return 0.0
        return max(0.0, req.created + self._ask_timeout_s - self._clock())

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

    def outcome(self, req_id: str) -> Outcome | None:
        """Чем закончился запрос, если он уже закрыт (иначе None)."""
        with self._lock:
            return next((o for o in reversed(self._closed)
                         if o.id == req_id), None)

    def grants(self) -> dict[str, float]:
        """Живые гранты: инструмент → сколько секунд ещё проходит без
        вопроса. Поверхности показывают ИМЕНА — счётчик минут сам по себе
        не отвечает на вопрос «что мне сейчас разрешено»."""
        with self._lock:
            now = self._clock()
            return {tool: until - now
                    for tool, until in self._grant_until.items()
                    if until > now}

    def revoke_grants(self) -> None:
        with self._lock:
            self._grant_until.clear()


def allowed(decision: Decision) -> bool:
    return decision in (Decision.ALLOW, Decision.ALLOW_GRANT, Decision.AUTO)


def refusal_text(decision: Decision, timeout_s: float | None = None) -> str:
    """The words the ROBOT receives — honest, and phrased for a voice reply.

    Окно ожидания называется НАСТОЯЩЕЕ: строка говорила «(60 секунд)»
    независимо от `ask_timeout_s`, и робот повторял владельцу число,
    которого в этой установке не было (A-16).
    """
    window = f" ({int(timeout_s)} секунд)" if timeout_s else ""
    return {
        Decision.DENY: "Владелец отклонил действие.",
        Decision.TIMEOUT: "Владелец не ответил на запрос подтверждения"
                          f"{window} — действие не выполнено.",
        Decision.PAUSED: "Мост поставлен владельцем на паузу — Мак сейчас "
                         "недоступен для действий.",
    }.get(decision, "Действие не разрешено.")
