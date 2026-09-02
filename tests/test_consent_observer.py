"""«Что нового» знает движок, а не три поллера по-своему (F-12).

До 2026-09-02 наблюдателя не было, и ответ на вопрос «появился ли новый
запрос» вычисляли независимо: таймер меню-бара сравнивал снимок, вотчер пушей
держал множество из пятисот виденных id, маскот брал `pending()` при каждой
отрисовке. Три ответа на один вопрос расходятся не «если», а «когда» — и
расхождение здесь стоит пропущенного пуша, то есть согласия, о котором
владельцу не сказали.

Проверяется поведение движка, а не наличие метода: подписчик обязан УЗНАТЬ
о запросе, сбой подписчика не имеет права уронить согласие, а его причина
не имеет права потеряться.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibebridge.capabilities import ToolClass  # noqa: E402
from vibebridge.consent import ConsentEngine, Decision  # noqa: E402


def _engine(**kw) -> ConsentEngine:
    return ConsentEngine(ask_timeout_s=0.2, **kw)


def test_a_watcher_hears_a_new_request_without_polling():
    engine = _engine()
    heard: list[tuple[str, str]] = []
    engine.subscribe(lambda event, req: heard.append((event, req.summary)))

    thread = threading.Thread(
        target=engine.request, args=("open_url", ToolClass.ACT, "открыть ссылку"))
    thread.start()
    thread.join(timeout=2)

    assert ("asked", "открыть ссылку") in heard, heard
    assert ("closed", "открыть ссылку") in heard, (
        "закрытие запроса — тоже новость: без неё поверхность так и покажет "
        "вопрос, на который уже ответили")


def test_the_event_arrives_before_the_answer_is_known():
    """Пуш обязан уйти, пока вопрос ЖИВ. Событие после ответа бесполезно."""
    engine = _engine()
    порядок: list[str] = []
    engine.subscribe(lambda event, req: порядок.append(event))

    def ask():
        порядок.append("вернулось")

    thread = threading.Thread(
        target=lambda: (engine.request("open_url", ToolClass.ACT, "проба"), ask()))
    thread.start()
    thread.join(timeout=2)
    assert порядок[0] == "asked", порядок
    assert порядок.index("asked") < порядок.index("вернулось"), порядок


def test_a_broken_watcher_cannot_take_the_engine_down():
    """Согласие — ядро продукта. Падать из-за подписчика оно не имеет права."""
    engine = _engine()
    engine.subscribe(lambda event, req: 1 / 0)
    целый: list[str] = []
    engine.subscribe(lambda event, req: целый.append(event))

    thread = threading.Thread(
        target=engine.request, args=("open_url", ToolClass.ACT, "проба"))
    thread.start()
    thread.join(timeout=2)
    assert "asked" in целый, "сбой одного подписчика съел событие у другого"
    assert engine.last_watcher_error, (
        "причина сбоя потеряна — подписчик, который тихо не работает, хуже "
        "отсутствующего")
    assert "ZeroDivisionError" in engine.last_watcher_error


def test_unsubscribing_actually_stops_the_events():
    engine = _engine()
    heard: list[str] = []
    stop = engine.subscribe(lambda event, req: heard.append(event))
    stop()
    thread = threading.Thread(
        target=engine.request, args=("open_url", ToolClass.ACT, "проба"))
    thread.start()
    thread.join(timeout=2)
    assert not heard, heard


def test_the_watcher_is_called_outside_the_lock():
    """Подписчик, который спросит движок из колбэка, должен получить ОТВЕТ,
    а не взаимную блокировку."""
    engine = _engine()
    видел: list[object] = []

    def curious(event, req):
        if event == "asked":
            видел.append(engine.pending())     # тронуть движок изнутри колбэка

    engine.subscribe(curious)
    thread = threading.Thread(
        target=engine.request, args=("open_url", ToolClass.ACT, "проба"))
    thread.start()
    thread.join(timeout=2)
    assert thread.is_alive() is False, "движок заблокировал сам себя"
    assert видел and видел[0] is not None
    assert engine.last_watcher_error is None, engine.last_watcher_error


def test_a_paused_bridge_announces_nothing():
    """Пауза сильнее всего: вопроса нет — значит и новости нет."""
    engine = _engine()
    engine.paused = True
    heard: list[str] = []
    engine.subscribe(lambda event, req: heard.append(event))
    assert engine.request("open_url", ToolClass.ACT, "проба") is Decision.PAUSED
    assert not heard, heard
