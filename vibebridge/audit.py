"""Append-only audit log — every tool call, its consent verdict, its outcome.

One JSON object per line under ~/Library/Logs/mac-bridge/audit.log. The menu
bar reads the tail; nothing else writes here. No secrets pass through the
bridge, but tool ARGUMENTS can carry text (clipboard, applescript), so the
log lives in the user's own Library and is chmod 600.
"""
from __future__ import annotations

import json
import os
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path


def local_hhmmss(ts: str) -> str:
    """ISO-строка журнала → часы владельца.

    Журнал пишет UTC (`datetime.now(UTC)`), и срез `ts[11:19]` печатал его
    как настенное время: панель показывала 20:45, когда на часах 22:45.
    Там это уже чинили (`localTs` в JS), а меню-бар резал строку по-прежнему
    (A-26) — одна ошибка, две реализации, и вторая пережила фикс первой.

    Мусор и пустое не выдумываются: возвращаем что дали, укоротив.
    """
    from datetime import datetime
    try:
        moment = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return str(ts)[11:19] or "--:--:--"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone().strftime("%H:%M:%S")


def recent_lines(entries: list[dict], *, limit: int = 5) -> list[str]:
    """Строки «последних действий» для меню-бара, новое сверху.

    Чистая функция — GUI в наборе не поднимается, а формат читает владелец.
    """
    out = []
    for e in reversed(entries[-limit:]):
        mark = "" if e.get("ok", True) else " ✗"
        out.append(f"{local_hhmmss(str(e.get('ts', '')))} "
                   f"{e.get('tool', '?')} · {e.get('decision', '?')}{mark}")
    return out or ["— пока пусто —"]


class AuditLog:
    def __init__(self, path: Path | None = None, *, tail: int = 50,
                 max_bytes: int = 5_000_000) -> None:
        if path is None:
            logs = Path.home() / "Library" / "Logs"
            new_dir, legacy = logs / "vibe-bridge", logs / "mac-bridge"
            if legacy.is_dir() and not new_dir.exists():
                try:
                    legacy.rename(new_dir)   # журнал переезжает вместе с именем
                except OSError:
                    new_dir = legacy
            path = new_dir / "audit.log"
        self.path = path
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._recent: deque[dict] = deque(maxlen=tail)
        #: Почему журнал не пишется, если не пишется. Раньше все три отказа
        #: молчали — мост работал БЕЗ журнала, и никто об этом не узнавал,
        #: хотя «журналирует каждое обращение» это обещание продукта (A-36).
        self.last_error: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.last_error = f"каталог журнала недоступен: {exc}"

    def record(self, *, tool: str, tool_class: str, decision: str,
               ok: bool, detail: str = "", line: str = "") -> dict:
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "tool": tool,
            "class": tool_class,
            "decision": decision,
            "ok": ok,
            "line": line[:160],       # human sentence the panel feed shows
            "detail": detail[:200],
        }
        with self._lock:
            self._recent.append(entry)
            try:
                self._rotate_locked()
                new = not self.path.exists()
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                if new:
                    os.chmod(self.path, 0o600)
            except OSError as exc:
                # Полный диск не должен ронять мост — но и молчать о том, что
                # журнала больше нет, тоже нельзя.
                self.last_error = f"журнал не пишется: {exc}"
        return entry

    def _rotate_locked(self) -> None:
        """One rotated generation (.1) at max_bytes — the journal survives
        years of calls without eating the disk, and the panel only ever
        needs the tail anyway."""
        try:
            if self.path.exists() and self.path.stat().st_size >= self._max_bytes:
                self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))
        except OSError as exc:
            self.last_error = f"журнал не ротируется: {exc}"

    def recent(self, n: int = 20) -> list[dict]:
        with self._lock:
            return list(self._recent)[-n:]

    _REFUSED = ("deny", "timeout", "paused", "unavailable")

    def read_entries(self, *, flt: str = "all", offset: int = 0,
                     limit: int = 50) -> dict:
        """Journal page from disk, newest first (SCN-011). The panel's
        source of history; `recent()` stays the in-memory tail for the
        snapshot. An unreadable file returns an honest empty page — the
        panel keeps working (SCN-011 errors & recovery)."""
        entries: list[dict] = []
        with self._lock:
            paths = [self.path.with_suffix(self.path.suffix + ".1"), self.path]
            for p in paths:
                try:
                    text = p.read_text(encoding="utf-8")
                except OSError:
                    continue
                for ln in text.splitlines():
                    try:
                        entries.append(json.loads(ln))
                    except json.JSONDecodeError:
                        continue
        entries.reverse()                      # newest first
        if flt == "refused":
            entries = [e for e in entries if e.get("decision") in self._REFUSED]
        elif flt in ("act", "read"):
            entries = [e for e in entries if e.get("class") == flt]
        total = len(entries)
        return {"total": total, "entries": entries[offset:offset + limit]}
