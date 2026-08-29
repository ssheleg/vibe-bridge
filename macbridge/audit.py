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


class AuditLog:
    def __init__(self, path: Path | None = None, *, tail: int = 50,
                 max_bytes: int = 5_000_000) -> None:
        self.path = path or (
            Path.home() / "Library" / "Logs" / "mac-bridge" / "audit.log")
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._recent: deque[dict] = deque(maxlen=tail)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

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
            except OSError:
                pass   # a full disk must not break the bridge
        return entry

    def _rotate_locked(self) -> None:
        """One rotated generation (.1) at max_bytes — the journal survives
        years of calls without eating the disk, and the panel only ever
        needs the tail anyway."""
        try:
            if self.path.exists() and self.path.stat().st_size >= self._max_bytes:
                self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))
        except OSError:
            pass

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
