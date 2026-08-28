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
from datetime import datetime, timezone
from pathlib import Path


class AuditLog:
    def __init__(self, path: Path | None = None, *, tail: int = 50) -> None:
        self.path = path or (
            Path.home() / "Library" / "Logs" / "mac-bridge" / "audit.log")
        self._lock = threading.Lock()
        self._recent: deque[dict] = deque(maxlen=tail)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def record(self, *, tool: str, tool_class: str, decision: str,
               ok: bool, detail: str = "") -> dict:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": tool,
            "class": tool_class,
            "decision": decision,
            "ok": ok,
            "detail": detail[:200],
        }
        with self._lock:
            self._recent.append(entry)
            try:
                new = not self.path.exists()
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                if new:
                    os.chmod(self.path, 0o600)
            except OSError:
                pass   # a full disk must not break the bridge
        return entry

    def recent(self, n: int = 20) -> list[dict]:
        with self._lock:
            return list(self._recent)[-n:]
