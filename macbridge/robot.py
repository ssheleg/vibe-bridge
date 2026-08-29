"""South side — the bridge talking TO the robot (spec §6).

Two independent channels, degraded honestly and separately (SCN-007/009):

* chat        — Hermes gateway, OpenAI-compatible POST /v1/chat/completions,
                bearer key; the 150 s + retry-once timeout contract is the
                robot's own (wiki R1.50) and is honored here.
* bridge API  — GET /bridge/status · POST /bridge/update · SSE /bridge/events;
                the contract this module consumes and the robot repo
                implements (M-ROBOT, CO-4). Until the robot ships it, every
                call resolves to an honest "robot unconfigured/unreachable" —
                the panel renders that state, never a spinner.

The client never invents data: a failed probe returns offline-with-since,
an undelivered chat message says so, and nothing here retries forever.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

CHAT_TIMEOUT_S = 150.0        # Hermes thinks long — the robot's own contract
STATUS_TIMEOUT_S = 5.0
UPDATE_TIMEOUT_S = 20.0


class RobotUnconfigured(Exception):
    """No robot is paired with this bridge yet."""


class RobotClient:
    """Async client over both channels. `http` is injectable for tests
    (httpx.AsyncClient with MockTransport)."""

    def __init__(self, *, base_url: str | None = None,
                 chat_url: str | None = None, chat_key: str | None = None,
                 name: str = "робот",
                 http: httpx.AsyncClient | None = None) -> None:
        self.base_url = (base_url or "").rstrip("/") or None
        self.chat_url = (chat_url or "").rstrip("/") or None
        self.chat_key = chat_key
        self.name = name
        self._http = http or httpx.AsyncClient()
        self._last_online: float | None = None
        self._offline_since: float | None = None

    @property
    def configured(self) -> bool:
        return self.base_url is not None or self.chat_url is not None

    def configure(self, *, base_url: str | None = None,
                  chat_url: str | None = None, chat_key: str | None = None,
                  name: str | None = None) -> None:
        """Re-point the client after pairing — the running panel picks the
        robot up without a restart."""
        if base_url is not None:
            self.base_url = base_url.rstrip("/") or None
        if chat_url is not None:
            self.chat_url = chat_url.rstrip("/") or None
        if chat_key is not None:
            self.chat_key = chat_key or None
        if name:
            self.name = name
        self._offline_since = None

    # ── status ──────────────────────────────────────────────────────────────

    async def status(self) -> dict[str, Any]:
        """Last-known truth about the robot. Never raises; never spins."""
        if self.base_url is None:
            return {"configured": False, "online": False,
                    "reason": "робот не подключён к панели"}
        try:
            r = await self._http.get(f"{self.base_url}/bridge/status",
                                     timeout=STATUS_TIMEOUT_S)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            if self._offline_since is None:
                self._offline_since = time.time()
            return {"configured": True, "online": False,
                    "offline_since": self._offline_since,
                    "reason": _speakable(exc)}
        self._offline_since = None
        self._last_online = time.time()
        return {"configured": True, "online": True, "name": self.name, **data}

    # ── chat ────────────────────────────────────────────────────────────────

    async def chat(self, text: str, *, session: str = "panel") -> dict[str, Any]:
        """One turn to the brain. Retries ONCE on a slow first answer
        (R1.50 contract), then answers honestly. Returns
        {ok, reply} | {ok: False, undelivered: True, error}."""
        if self.chat_url is None:
            return {"ok": False, "undelivered": True,
                    "error": "чат недоступен: робот не подключён"}
        payload = {
            "model": "robot",           # cosmetic: the real model is server-side
            "user": session,
            "messages": [{"role": "user", "content": text}],
        }
        headers = ({"Authorization": f"Bearer {self.chat_key}"}
                   if self.chat_key else {})
        for attempt in (1, 2):
            try:
                r = await self._http.post(
                    f"{self.chat_url}/v1/chat/completions", json=payload,
                    headers=headers, timeout=CHAT_TIMEOUT_S)
                r.raise_for_status()
                data = r.json()
                reply = data["choices"][0]["message"]["content"]
                return {"ok": True, "reply": reply}
            except httpx.TimeoutException:
                if attempt == 1:
                    continue            # the single retry the contract allows
                return {"ok": False, "undelivered": False,
                        "error": "Робот думает дольше обычного — ответ может "
                                 "прийти событием, либо повторите позже"}
            except (httpx.HTTPError, LookupError, ValueError) as exc:
                return {"ok": False, "undelivered": True,
                        "error": f"не доставлено: {_speakable(exc)}"}
        return {"ok": False, "undelivered": True, "error": "не доставлено"}

    # ── update ──────────────────────────────────────────────────────────────

    async def trigger_update(self) -> dict[str, Any]:
        if self.base_url is None:
            return {"ok": False, "error": "робот не подключён"}
        try:
            r = await self._http.post(f"{self.base_url}/bridge/update",
                                      timeout=UPDATE_TIMEOUT_S)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            return {"ok": False, "error": _speakable(exc)}
        return {"ok": True}

    # ── events ──────────────────────────────────────────────────────────────

    async def events(self):
        """Async iterator over the robot's proactive events (SSE). Yields
        dicts; ends on any transport error — the caller owns reconnect
        cadence, this method never loops on its own."""
        if self.base_url is None:
            return
        import json as _json
        try:
            async with self._http.stream(
                    "GET", f"{self.base_url}/bridge/events",
                    timeout=httpx.Timeout(10.0, read=None)) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            yield _json.loads(line[5:])
                        except ValueError:
                            continue
        except httpx.HTTPError:
            return

    async def aclose(self) -> None:
        await self._http.aclose()


def _speakable(exc: Exception) -> str:
    """An error the robot's owner (or the robot's voice) can actually say."""
    if isinstance(exc, httpx.ConnectError):
        return "робот недоступен по сети"
    if isinstance(exc, httpx.TimeoutException):
        return "робот не ответил вовремя"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"робот ответил ошибкой {exc.response.status_code}"
    return "не удалось связаться с роботом"
