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

    def _headers(self) -> dict[str, str]:
        """One shared secret authorizes BOTH channels of the robot's
        bridge_api (its bearer guards every endpoint — fail-closed)."""
        return ({"Authorization": f"Bearer {self.chat_key}"}
                if self.chat_key else {})

    async def status(self) -> dict[str, Any]:
        """Last-known truth about the robot. Never raises; never spins."""
        if self.base_url is None:
            return {"configured": False, "online": False,
                    "reason": "робот не подключён к панели"}
        try:
            r = await self._http.get(f"{self.base_url}/bridge/status",
                                     headers=self._headers(),
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

    async def probe(self) -> dict[str, Any]:
        """Дозвониться до робота и сказать, ПОЧЕМУ не вышло.

        Форма ручной привязки проверяла ровно одно — что адрес начинается с
        `http://`. Дальше она объявляла «привязан ✓», и три разные беды
        выглядели одинаково: чужой адрес, неверный ключ и выключенный робот
        (A-12). Владельцу каждая из них говорит РАЗНОЕ, поэтому и различаем.
        """
        if self.base_url is None:
            return {"ok": False, "kind": "unconfigured",
                    "error": "адрес робота не задан"}
        try:
            r = await self._http.get(f"{self.base_url}/bridge/status",
                                     headers=self._headers(),
                                     timeout=STATUS_TIMEOUT_S)
        except httpx.HTTPError as exc:
            return {"ok": False, "kind": "unreachable",
                    "error": f"робот не отвечает по этому адресу: "
                             f"{_speakable(exc)}"}
        if r.status_code in (401, 403):
            return {"ok": False, "kind": "unauthorized",
                    "error": "робот ответил «не пущу»: ключ не подошёл"}
        if r.status_code >= 400:
            return {"ok": False, "kind": "unreachable",
                    "error": f"робот ответил {r.status_code}"}
        try:
            data = r.json()
        except ValueError:
            data = None
        # Робот обязан назваться. Иначе по адресу отвечает ЧТО-ТО, но не он —
        # а «привязан ✓» к чужому сайту хуже, чем честное «это не робот».
        if not isinstance(data, dict) or not (data.get("name")
                                              or data.get("version")):
            return {"ok": False, "kind": "not-a-robot",
                    "error": "по этому адресу отвечает не робот "
                             "(нет его карточки статуса)"}
        self._last_online = time.time()
        self._offline_since = None
        return {"ok": True, **data}

    # ── chat ────────────────────────────────────────────────────────────────

    async def chat(self, text: str, *, session: str = "panel",
                   history: list[dict[str, str]] | None = None
                   ) -> dict[str, Any]:
        """One turn to the brain.

        Повторяется РОВНО в одном случае: когда соединение не установилось,
        то есть робот хода не видел. Медленный ответ не повторяется никогда
        (A-6): ход мозга не идемпотентен — к 150-й секунде он мог уже
        опубликовать пост в канал, снять кадр со вспышкой и открыть вкладку,
        и второй такой же payload исполнит это второй раз. Ключа
        идемпотентности в протоколе нет ни у нас, ни в прокси робота, и
        `ReadTimeout` не отличает «не дошло» от «дошло и работает» — значит
        единственный безопасный ответ на него это не посылать повторно.

        Returns {ok, reply} | {ok: False, undelivered: bool, error}."""
        if self.chat_url is None:
            return {"ok": False, "undelivered": True,
                    "error": "чат недоступен: робот не подключён"}
        # The turns so far, then the new one. Sending only the last message
        # left the brain to reconstruct context from its own long-term memory,
        # and it answered with something the owner had said an hour earlier
        # instead of a minute ago — reported as "он не видит того, что только
        # что писал". The protocol carries the thread; we should use it.
        messages = [*(history or []), {"role": "user", "content": text}]
        payload = {
            "model": "robot",           # cosmetic: the real model is server-side
            "user": session,
            "messages": messages,
        }
        headers = self._headers()
        for attempt in (1, 2):
            try:
                r = await self._http.post(
                    f"{self.chat_url}/v1/chat/completions", json=payload,
                    headers=headers, timeout=CHAT_TIMEOUT_S)
                r.raise_for_status()
                data = r.json()
                reply = data["choices"][0]["message"]["content"]
                return {"ok": True, "reply": reply}
            except (httpx.ConnectTimeout, httpx.PoolTimeout):
                # Ход не покинул мост: соединения не случилось, побочных
                # эффектов быть не может. Единственный безопасный повтор.
                if attempt == 1:
                    continue
                return {"ok": False, "undelivered": True,
                        "error": "не доставлено: робот не отвечает на "
                                 "подключение"}
            except httpx.TimeoutException:
                # Запрос ушёл. Дошёл он или нет — отсюда не видно, а ход
                # мозга не идемпотентен: молчим и говорим правду.
                return {"ok": False, "undelivered": False,
                        "error": "Робот думает дольше обычного — ответ может "
                                 "прийти событием, либо повторите позже"}
            except (httpx.HTTPError, LookupError, ValueError) as exc:
                return {"ok": False, "undelivered": True,
                        "error": f"не доставлено: {_speakable(exc)}"}
        return {"ok": False, "undelivered": True, "error": "не доставлено"}

    # ── update ──────────────────────────────────────────────────────────────

    async def system(self) -> dict[str, Any]:
        """Телеметрия робота для панели: температура, нагрузка, память, диск,
        воздух, сервисы.

        Отдельный вызов, а не расширение `status`: статус спрашивают часто и
        он должен быть дешёвым, а снимок системы собирает робот и незачем
        платить за него на каждом опросе.
        """
        if self.base_url is None:
            return {"ok": False, "error": "робот не подключён к панели"}
        try:
            r = await self._http.get(f"{self.base_url}/bridge/system",
                                     headers=self._headers(), timeout=10.0)
            if r.status_code == 404:
                return {"ok": False,
                        "error": "робот ещё не обновился — телеметрия "
                                 "появится после его обновления"}
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {"ok": False, "error": f"телеметрия недоступна: {_speakable(exc)}"}
        if data.get("error"):
            return {"ok": False, "error": str(data["error"])}
        return {"ok": True, **data}

    async def media(self, name: str) -> tuple[bytes, str] | None:
        """Забрать файл, на который ссылается событие.

        Мост ходит к роботу своим bearer'ом и отдаёт байты странице: иначе
        страница держала бы токен робота, а публиковать фото наружу ради
        показа хозяину — плата, которой он не просил.
        """
        if self.base_url is None or not name or "/" in name or ".." in name:
            return None
        try:
            r = await self._http.get(f"{self.base_url}/bridge/media/{name}",
                                     headers=self._headers(), timeout=20.0)
            r.raise_for_status()
        except httpx.HTTPError:
            return None
        return r.content, r.headers.get("content-type",
                                        "application/octet-stream")

    async def trigger_update(self) -> dict[str, Any]:
        if self.base_url is None:
            return {"ok": False, "error": "робот не подключён"}
        try:
            r = await self._http.post(f"{self.base_url}/bridge/update",
                                      headers=self._headers(),
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
                    headers=self._headers(),
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
