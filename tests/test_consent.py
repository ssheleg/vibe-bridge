"""Consent engine — the safety core. If this is wrong, the robot acts without
the owner, so every branch is pinned.

Runs with a fake clock and driven Events, no UI and no sleeping.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibebridge.consent import (
    ConsentEngine,
    ConsentRequest,
    Decision,
    ToolClass,
    allowed,
    refusal_text,
)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _answer_async(engine, decision, delay=0.02):
    """Resolve the first pending request after a short real delay."""
    def run():
        for _ in range(200):
            req = engine.pending()
            if req is not None:
                req.resolve(decision)
                return
            time.sleep(0.005)
    threading.Thread(target=run, daemon=True).start()


def test_read_is_auto_never_asks():
    eng = ConsentEngine()
    d = eng.request("mac_screenshot", ToolClass.READ, "смотрю экран")
    assert d is Decision.AUTO
    assert allowed(d)


def test_act_allow_once():
    eng = ConsentEngine(ask_timeout_s=2.0)
    _answer_async(eng, Decision.ALLOW)
    d = eng.request("mac_open_app", ToolClass.ACT, "открыть Safari")
    assert d is Decision.ALLOW
    assert allowed(d)


def test_act_deny():
    eng = ConsentEngine(ask_timeout_s=2.0)
    _answer_async(eng, Decision.DENY)
    d = eng.request("mac_open_app", ToolClass.ACT, "открыть Safari")
    assert d is Decision.DENY
    assert not allowed(d)
    assert "отклонил" in refusal_text(d)


def test_act_timeout_denies():
    eng = ConsentEngine(ask_timeout_s=0.05)
    d = eng.request("mac_open_app", ToolClass.ACT, "открыть Safari")
    assert d is Decision.TIMEOUT
    assert not allowed(d)
    assert "не ответил" in refusal_text(d)
    # request must not linger in the queue after timeout
    assert eng.pending() is None


def test_grant_suppresses_the_second_call_of_the_SAME_tool():
    clk = FakeClock()
    eng = ConsentEngine(ask_timeout_s=2.0, grant_ttl_s=900.0, clock=clk)
    _answer_async(eng, Decision.ALLOW_GRANT)
    d1 = eng.request("mac_open_app", ToolClass.ACT, "открыть Safari")
    assert d1 is Decision.ALLOW_GRANT
    assert eng.grant_active("mac_open_app") > 0
    # тот же инструмент в окне гранта — без диалога
    d2 = eng.request("mac_open_app", ToolClass.ACT, "открыть Почту")
    assert d2 is Decision.AUTO


def test_a_grant_does_not_leak_to_the_neighbouring_tools():
    """A-8. Грант ключевался на КЛАСС: «да» на «открыть ссылку» на 15 минут
    молча включал AppleScript, буфер обмена и Shortcuts — три способности,
    о которых кнопка не сказала ни слова. Визия §1 обещает согласие
    ПОИМЁННОЕ."""
    clk = FakeClock()
    eng = ConsentEngine(ask_timeout_s=0.05, grant_ttl_s=900.0, clock=clk)
    _answer_async(eng, Decision.ALLOW_GRANT)
    assert eng.request("open_url", ToolClass.ACT, "открыть ссылку") is \
        Decision.ALLOW_GRANT
    # сосед по классу обязан спросить заново — здесь некому ответить,
    # поэтому истекает, а не проходит молча
    assert eng.request("automation", ToolClass.ACT, "AppleScript") is \
        Decision.TIMEOUT
    assert eng.grant_active("automation") == 0
    assert eng.grant_active("open_url") > 0


def test_grants_are_listed_by_name_so_a_surface_can_show_them():
    """Владелец должен видеть, ЧТО именно сейчас проходит без вопроса —
    иначе «видимое согласие» из визии видимо только как счётчик минут."""
    clk = FakeClock()
    eng = ConsentEngine(ask_timeout_s=0.05, grant_ttl_s=900.0, clock=clk)
    assert eng.grants() == {}
    _answer_async(eng, Decision.ALLOW_GRANT)
    eng.request("open_url", ToolClass.ACT, "открыть ссылку")
    _answer_async(eng, Decision.ALLOW_GRANT)
    eng.request("clipboard_read", ToolClass.ACT, "прочитать буфер")
    assert set(eng.grants()) == {"open_url", "clipboard_read"}
    assert all(v > 0 for v in eng.grants().values())
    clk.advance(901.0)
    assert eng.grants() == {}          # истёкшие не перечисляются


def test_grant_expires():
    clk = FakeClock()
    eng = ConsentEngine(ask_timeout_s=0.05, grant_ttl_s=900.0, clock=clk)
    _answer_async(eng, Decision.ALLOW_GRANT)
    eng.request("mac_open_app", ToolClass.ACT, "x")
    clk.advance(901.0)
    assert eng.grant_active("mac_open_app") == 0
    # next ACT asks again → times out (no answerer)
    d = eng.request("mac_open_app", ToolClass.ACT, "x")
    assert d is Decision.TIMEOUT


def test_pause_refuses_everything_including_read():
    eng = ConsentEngine()
    eng.paused = True
    assert eng.request("mac_screenshot", ToolClass.READ, "x") is Decision.PAUSED
    assert eng.request("mac_open_app", ToolClass.ACT, "x") is Decision.PAUSED
    assert "паузу" in refusal_text(Decision.PAUSED)


def test_revoke_grants():
    clk = FakeClock()
    eng = ConsentEngine(ask_timeout_s=2.0, grant_ttl_s=900.0, clock=clk)
    _answer_async(eng, Decision.ALLOW_GRANT)
    eng.request("mac_open_app", ToolClass.ACT, "x")
    assert eng.grant_active("mac_open_app") > 0
    eng.revoke_grants()
    assert eng.grant_active("mac_open_app") == 0
    assert eng.grants() == {}


# ── A-9: запрос истекает, и об этом надо сказать ───────────────────────────

def test_a_pending_request_reports_how_long_it_has_left():
    """Отказ по молчанию — политика по умолчанию, и ни одна поверхность об
    этом не говорила: владелец видел три кнопки без единого признака, что
    бездействие тоже решение."""
    clk = FakeClock()
    eng = ConsentEngine(ask_timeout_s=60.0, clock=clk)
    t = threading.Thread(target=lambda:
                         eng.request("open_url", ToolClass.ACT, "ссылка"),
                         daemon=True)
    t.start()
    for _ in range(200):
        if eng.pending() is not None:
            break
        threading.Event().wait(0.01)
    req = eng.pending()
    assert eng.ask_timeout_s == 60.0
    assert eng.remaining(req) == 60.0
    clk.advance(45.0)
    assert eng.remaining(req) == 15.0
    clk.advance(100.0)
    assert eng.remaining(req) == 0.0          # не уходит в минус
    eng.resolve_by_id(req.id, Decision.DENY)
    t.join(timeout=5)


def test_the_refusal_names_the_real_timeout_not_a_hardcoded_sixty():
    """Строка отказа говорила «(60 секунд)» независимо от настройки —
    робот повторял владельцу число, которого не было (A-16, половина)."""
    assert "60 секунд" in refusal_text(Decision.TIMEOUT, timeout_s=60.0)
    assert "20 секунд" in refusal_text(Decision.TIMEOUT, timeout_s=20.0)
    assert "секунд" not in refusal_text(Decision.DENY, timeout_s=20.0)
    # без аргумента — по-прежнему говорит правду, но без числа
    assert "не ответил" in refusal_text(Decision.TIMEOUT)


def test_the_system_dialog_names_the_deadline_in_words():
    """Модальный лист не умеет тикать — значит обязан сказать словом.
    Проверяем ИСХОДНИК ветки диалога: GUI в наборе не поднимается, но
    отсутствие срока в единственной строке, которую видит владелец у
    экрана, — та же дыра A-9, что и в панели."""
    import re
    from pathlib import Path

    import vibebridge

    src = (Path(vibebridge.__file__).parent / "app.py").read_text()
    src = re.sub(r"^\s*#.*$", "", src, flags=re.M)      # без комментариев
    call = src.split("rumps.alert(", 1)[1].split(")", 1)[0]
    assert "message=" in call and "отказ" in call, \
        "системный диалог не говорит, что молчание — это отказ"
    assert "consent.remaining(req)" in src or "remaining(req)" in src


# ── A-10: истёкший запрос закрыт, а не просто убран из очереди ─────────────

def test_answering_after_the_timeout_is_refused_not_silently_accepted():
    """A-10. По таймауту запрос удалялся из очереди, но `_event` не
    выставлялся — и `resolve()` секундой позже возвращал True. Панель
    показывала успех, системный диалог молчал, действие не исполнялось:
    владелец нажал «Разрешить» и не узнал, что не разрешил ничего."""
    eng = ConsentEngine(ask_timeout_s=0.1)
    result: list[Decision] = []
    t = threading.Thread(target=lambda: result.append(
        eng.request("open_url", ToolClass.ACT, "ссылка")), daemon=True)
    t.start()
    for _ in range(200):
        if eng.pending() is not None:
            break
        threading.Event().wait(0.01)
    req = eng.pending()
    t.join(timeout=5)
    assert result == [Decision.TIMEOUT]
    # ...и запоздавшее «да» честно проигрывает
    assert req.resolve(Decision.ALLOW, by="dialog") is False
    assert eng.resolve_by_id(req.id, Decision.ALLOW, by="panel") is False
    assert req.decided_by == "timeout"      # поверхность может сказать ПОЧЕМУ


def test_a_surface_that_answers_in_the_gap_still_wins():
    """Зазор между «wait истёк» и «закрываем запрос» — настоящая гонка.
    Ответ, попавший в него, обязан устоять: владелец нажал ДО срока."""
    req = ConsentRequest(tool="open_url", tool_class=ToolClass.ACT,
                         summary="ссылка")
    assert req.resolve(Decision.ALLOW, by="panel") is True
    # закрытие по таймауту приходит вторым и проигрывает
    assert req.resolve(Decision.TIMEOUT, by="timeout") is False
    assert req._decision is Decision.ALLOW and req.decided_by == "panel"


def test_a_late_click_can_learn_what_actually_happened():
    """«Запрос уже решён или истёк» — две разные новости в одной строке.
    Истёк — значит робот получил отказ по молчанию; решён — значит владелец
    ответил с другой поверхности. Поверхность должна мочь их различить."""
    eng = ConsentEngine(ask_timeout_s=0.1)
    t = threading.Thread(target=lambda:
                         eng.request("open_url", ToolClass.ACT, "ссылка"),
                         daemon=True)
    t.start()
    for _ in range(200):
        if eng.pending() is not None:
            break
        threading.Event().wait(0.01)
    rid = eng.pending().id
    t.join(timeout=5)
    got = eng.outcome(rid)
    assert got is not None
    assert got.decision is Decision.TIMEOUT and got.by == "timeout"
    assert eng.outcome("нет такого") is None
