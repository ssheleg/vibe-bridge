"""A-19: лента робота переживает перезапуск и не двоится.

История не хранилась НИГДЕ, и обе стороны ссылались друг на друга: робот
стартует хвост с EOF («историю панель берёт из своего журнала»), мост держал
`deque` в памяти («старые ходы остаются в журнале»). Журнал — это аудит
решений по инструментам, событий робота в нём нет вовсе.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibebridge.feed import EventFeed


def _ev(ts: str, text: str, kind: str = "task_done") -> dict:
    return {"ts": ts, "kind": kind, "text": text}


def test_the_feed_survives_a_restart(tmp_path):
    path = tmp_path / "feed.jsonl"
    feed = EventFeed(path)
    feed.add(_ev("2026-09-02T10:00:00", "полил цветы"))
    feed.add(_ev("2026-09-02T10:05:00", "камера ослепла"))

    again = EventFeed(path)
    assert [e["text"] for e in again] == ["полил цветы", "камера ослепла"]


def test_a_resent_event_does_not_appear_twice(tmp_path):
    """Робот досылает хвост при переподключении — без дедупликации владелец
    увидел бы всё сказанное второй раз."""
    feed = EventFeed(tmp_path / "feed.jsonl")
    assert feed.add(_ev("2026-09-02T10:00:00", "полил цветы")) is True
    assert feed.add(_ev("2026-09-02T10:00:00", "полил цветы")) is False
    assert len(feed) == 1

    # ...а другое событие в ту же секунду — это другое событие
    assert feed.add(_ev("2026-09-02T10:00:00", "и закрыл окно")) is True
    assert len(feed) == 2


def test_dedup_survives_a_restart_too(tmp_path):
    """Иначе первый же реконнект после перезапуска моста удвоил бы ленту."""
    path = tmp_path / "feed.jsonl"
    EventFeed(path).add(_ev("2026-09-02T10:00:00", "полил цветы"))
    again = EventFeed(path)
    assert again.add(_ev("2026-09-02T10:00:00", "полил цветы")) is False
    assert len(again) == 1


def test_the_file_is_capped_and_the_recent_half_survives(tmp_path):
    path = tmp_path / "feed.jsonl"
    feed = EventFeed(path, tail=10, max_bytes=400)
    for i in range(40):
        feed.add(_ev(f"2026-09-02T10:00:{i:02d}", f"событие {i}"))
    assert path.stat().st_size <= 400 * 3, "ротация не сработала"
    survived = [e["text"] for e in EventFeed(path, tail=10)]
    assert survived, "ротация вынесла ВСЁ"
    assert survived[-1] == "событие 39", "потерян самый свежий"


def test_a_broken_line_does_not_cost_the_rest_of_the_feed(tmp_path):
    path = tmp_path / "feed.jsonl"
    path.write_text('{"ts":"1","kind":"a","text":"первое"}\n'
                    'не json\n'
                    '{"ts":"2","kind":"a","text":"второе"}\n', encoding="utf-8")
    assert [e["text"] for e in EventFeed(path)] == ["первое", "второе"]


def test_a_disk_that_refuses_is_reported_not_swallowed(tmp_path):
    """Проглоченная ошибка — самый частый класс дефектов проекта. Лента
    продолжает жить в памяти, но вызывающий узнаёт, что она больше не
    переживёт перезапуск."""
    blocked = tmp_path / "нет-такого-каталога"
    blocked.write_text("я файл, а не каталог", encoding="utf-8")
    feed = EventFeed(blocked / "feed.jsonl")
    assert feed.add(_ev("1", "сказал")) is True
    assert len(feed) == 1                       # в памяти всё есть
    assert feed.last_error and "не пишется" in feed.last_error


def test_clearing_forgets_the_file_too(tmp_path):
    path = tmp_path / "feed.jsonl"
    feed = EventFeed(path)
    feed.add(_ev("1", "сказал"))
    feed.clear()
    assert len(feed) == 0
    assert EventFeed(path).tail(10) == []
