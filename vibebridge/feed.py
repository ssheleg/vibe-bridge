"""Лента робота — то, что он сказал и показал, и что переживает перезапуск.

До 2026-09-02 её не держал НИКТО, и обе стороны ссылались друг на друга.
Робот стартует хвост событий с EOF, объясняя это тем, что «историю панель
берёт из своего журнала». Мост держал `deque(maxlen=50)` только в памяти —
и чистил её на новой сессии питомца, объясняя это тем, что «старые ходы
остаются в журнале». Журнал же — это аудит РЕШЕНИЙ по инструментам, и
события робота в него не попадают вовсе. Всё сказанное роботом, пока мост
был выключен, исчезало молча (A-19).

Здесь лента становится настоящей: append-only JSONL с потолком по байтам,
как у аудита рядом, плюс дедупликация по (ts, kind, text) — робот теперь
досылает хвост при переподключении, и без неё владелец увидел бы события
дважды.

Отказ диска не стоит владельцу события: писать не вышло — лента живёт в
памяти, а провал уходит в журнал вызывающего, а не в тишину.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path


class EventFeed:
    """Лента с памятью. Читатели видят `list(feed)`; писатель — `add`."""

    #: Сколько строк держим в памяти для поверхностей.
    TAIL = 200
    #: Потолок файла. Меньше аудита: событие — это одна фраза робота, а не
    #: запись решения, и разбирать по нему инциденты никто не будет.
    MAX_BYTES = 1_000_000

    def __init__(self, path: Path | None = None, *, tail: int = TAIL,
                 max_bytes: int = MAX_BYTES) -> None:
        self.path = path
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._items: deque[dict] = deque(maxlen=tail)
        self._seen: deque[tuple] = deque(maxlen=tail)
        self._seen_set: set[tuple] = set()
        self.last_error: str | None = None
        self._load()

    # ── чтение ──────────────────────────────────────────────────────────────

    def __iter__(self):
        with self._lock:
            return iter(list(self._items))

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def tail(self, n: int) -> list[dict]:
        with self._lock:
            return list(self._items)[-n:]

    # ── запись ──────────────────────────────────────────────────────────────

    def add(self, event: dict) -> bool:
        """Добавить событие. False — такое уже было (робот дослал хвост)."""
        key = (str(event.get("ts", "")), str(event.get("kind", "")),
               str(event.get("text", "")))
        with self._lock:
            if key in self._seen_set:
                return False
            if len(self._seen) == self._seen.maxlen and self._seen:
                self._seen_set.discard(self._seen[0])
            self._seen.append(key)
            self._seen_set.add(key)
            self._items.append(event)
        self._append_to_disk(event)
        return True

    def clear(self) -> None:
        """Забыть всё — и в памяти, и на диске. Владелец попросил."""
        with self._lock:
            self._items.clear()
            self._seen.clear()
            self._seen_set.clear()
        if self.path is not None:
            try:
                self.path.write_text("", encoding="utf-8")
            except OSError as exc:
                self.last_error = f"лента не очищена: {exc}"

    # ── диск ────────────────────────────────────────────────────────────────

    def _append_to_disk(self, event: dict) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if (self.path.exists()
                    and self.path.stat().st_size > self._max_bytes):
                # Truncate-ротация, как у события на роботе: лента — это
                # «что было недавно», а не архив на годы.
                keep = self.tail(self.TAIL // 2)
                with self.path.open("w", encoding="utf-8") as fh:
                    for item in keep:
                        fh.write(json.dumps(item, ensure_ascii=False) + "\n")
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.last_error = None
        except (OSError, TypeError, ValueError) as exc:
            # Ошибка НЕ проглатывается: лента продолжает жить в памяти, но
            # вызывающий узнаёт, что она больше не переживёт перезапуск.
            self.last_error = f"лента не пишется на диск: {exc}"

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            self.last_error = f"лента не прочитана: {exc}"
            return
        for line in lines[-self._items.maxlen:]:
            try:
                item = json.loads(line)
            except ValueError:
                continue            # битая строка — не повод терять остальные
            if not isinstance(item, dict):
                continue
            key = (str(item.get("ts", "")), str(item.get("kind", "")),
                   str(item.get("text", "")))
            self._items.append(item)
            self._seen.append(key)
            self._seen_set.add(key)
