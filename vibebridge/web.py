"""One ASGI app, four doors: /mcp for the robot, / + /api + /events for the
owner's panel. The walking skeleton of vibe-bridge (spec §1, §7).

Mounting the FastMCP transport requires its session manager to run for the
process lifetime — that lives in this app's lifespan (research-notes §A).
Bearer auth for /mcp is OUR ASGI guard around the mount: with no
`token_verifier` the SDK installs no middleware of its own, so nothing
conflicts. Panel routes are gated by a cookie carrying the panel token.

Events: the walking skeleton polls the consent engine and audit tail into
snapshots and pushes them over SSE. T-CORE replaces polling with direct
engine hooks and per-request ids; the wire contract (event `state`, JSON
snapshot) is what the panel binds to and it survives that swap.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route

from .audit import AuditLog
from .capabilities import (
    Capability,
    Runner,
    build_capabilities,
    probe_availability,
)
from .consent import ConsentEngine, Decision, ToolClass
from .mascot import Mascot
from .push import PushSender, ensure_vapid_keys
from .robot import RobotClient
from .server import build_server
from .state import BridgeState


def _solid_png(size: int, rgb: tuple[int, int, int] = (0x2F, 0x6F, 0xEB)) -> bytes:
    """A solid-color PNG icon, stdlib-only (workbench accent). Good enough
    for a home-screen tile until M-PLATFORM ships real art."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    row = b"\x00" + bytes(rgb) * size
    idat = zlib.compress(row * size)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", idat) + chunk(b"IEND", b""))

#: Shown when someone opens the panel address without a token. It is a page
#: rather than `{"error":"unauthorized"}` because the reader is a person who
#: typed the address or lost the tab, and the answer they need is one
#: sentence. It carries no token: whoever sees this has proved nothing.
_DOOR_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>vibe-bridge</title><style>
body{font:15px/1.6 -apple-system,"SF Pro","Segoe UI",sans-serif;max-width:34rem;
margin:12vh auto;padding:0 1.5rem;color:#1a1f2b;background:#f7f8fa}
h1{font-size:1.25rem;margin:0 0 .75rem}
p{margin:0 0 .75rem}code{background:#e6e9ef;padding:.1em .35em;border-radius:4px}
.muted{color:#5b6472;font-size:13px}
@media(prefers-color-scheme:dark){body{background:#0f1218;color:#e8ecf3}
code{background:#232a36}.muted{color:#8a93a6}}
</style></head><body>
<h1>Панель открывается из меню-бара</h1>
<p>Мост работает, но этот адрес сам по себе ничего не открывает: панель
защищена ключом, который подставляется автоматически.</p>
<p>Нажмите значок моста в меню-баре (вверху справа) и выберите
<b>«Открыть панель»</b>.</p>
<p class="muted">Значка нет? Значит мост не запущен — откройте
<code>vibe-bridge</code> из «Программ». Ключ панели хранится в
<code>~/Library/Application&nbsp;Support/vibe-bridge/state.json</code>; он
секретный и здесь не показывается.</p>
</body></html>"""

PANEL_COOKIE = "vb_panel"
_WEBUI = Path(__file__).parent / "webui"

_DECISIONS = {
    "allow": Decision.ALLOW,
    "allow_grant": Decision.ALLOW_GRANT,
    "deny": Decision.DENY,
}


class _RefusalJournal:
    """Отказ на границе попадает в журнал — но не превращает его в access-log.

    Визия §3 обещает журналировать «каждое обращение — исполненное и
    отклонённое», и до 2026-09-01 обе границы (`PeerGuard`, `BearerGuard`)
    отказывали МОЛЧА: владелец не мог узнать, что кто-то стучался. Обратная
    крайность так же плоха: сканер из локальной сети за минуту вытеснит из
    журнала всё, ради чего журнал существует.

    Поэтому политика та же, что у автообновления: одна строка на РАЗЛИЧНЫЙ
    отказ, повтор того же — не чаще раза в минуту.
    """

    WINDOW_S = 60.0

    def __init__(self, audit) -> None:
        self._audit = audit
        self._seen: dict[str, float] = {}

    def refuse(self, kind: str, line: str, detail: str = "") -> None:
        import time as _time
        now = _time.monotonic()
        last = self._seen.get(kind)
        if last is not None and now - last < self.WINDOW_S:
            return
        self._seen[kind] = now
        try:
            self._audit.record(tool="boundary", tool_class="SYS",
                               decision="deny", ok=False,
                               line=line, detail=detail or line)
        except Exception:                   # noqa: BLE001 - журнал не граница
            pass


def pending_version(installed: str | None, running: str) -> str | None:
    """Версия, которая применится после перезапуска — или None.

    Раньше здесь стояло `installed != running`, то есть сравнение СТРОК. После
    установки нового DMG каталог payload остаётся на прошлой версии, и панель
    начинала обещать обновление, которого нет: «обновление скачано и
    применится после перезапуска» — при том что перезапуск ничего не менял,
    потому что оболочка на равенстве и на старшинстве выбирает seed. Механизм
    обновления начинал врать ровно там, где ADR-0006 держит всё доверие.

    Чистая и снаружи — чтобы три случая (новее / равно / старее) проверялись
    без HTTP-стека.
    """
    if not installed:
        return None
    from vbboot import layout as _layout
    try:
        return installed if _layout.parse(installed) > _layout.parse(running) \
            else None
    except Exception:                       # noqa: BLE001 - непарсимое молчит
        return None


class PeerGuard:
    """403 всему, что пришло НЕ из loopback и не из тейлнета.

    Взводится только когда мост слушает больше, чем loopback (режим
    standalone): если бинд и так loopback, каждый пир и есть loopback, и
    проверка была бы декорацией.

    Зачем вообще: до 2026-09-01 standalone биндил ОДИН tailnet-интерфейс, и
    это выглядело границей — но ломало приложение целиком, потому что панель и
    окна виджета обращаются к мосту по `127.0.0.1`. Бинд расширили до всех
    интерфейсов, и тогда единственной «границей» оставался allowlist по
    `Host` — заголовку, который присылает клиент. Настоящая граница — адрес
    пира, и вот она.
    """

    def __init__(self, app, armed: bool, journal=None) -> None:
        self.app, self.armed, self.journal = app, armed, journal

    async def __call__(self, scope, receive, send) -> None:
        if self.armed and scope["type"] in ("http", "websocket"):
            from .net import peer_allowed
            client = scope.get("client") or (None, None)
            if not peer_allowed(client[0]):
                if self.journal is not None:
                    self.journal.refuse(
                        f"peer:{client[0]}",
                        f"отказано соединению не из тейлнета: {client[0]}",
                        f"{scope.get('method', '?')} {scope.get('path', '?')}")
                await JSONResponse(
                    {"error": "forbidden",
                     "detail": "мост принимает соединения только из loopback "
                               "и из тейлнета владельца"},
                    status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class BearerGuard:
    """401 before the MCP transport unless the robot token rides the
    Authorization header — ТОЛЬКО в standalone-режиме (ADR-0002). В
    gateway-режиме границей служат loopback+agentgateway, и гейт обязан
    пропускать: ключеваться на «токен существует» нельзя — пейринг создаёт
    robot_token, не меняя режима, и 2026-08-29 это молча отдало роботу 401
    на его же mac_*-инструменты на ~15 часов (замечено post-rename
    проверкой цепи)."""

    def __init__(self, app, state: BridgeState, mode: str = "standalone",
                 journal=None) -> None:
        self.app, self.state, self.mode = app, state, mode
        self.journal = journal

    async def __call__(self, scope, receive, send) -> None:
        token = (self.state.robot_token
                 if self.mode == "standalone" else None)
        # standalone БЕЗ спаренного робота: раньше здесь стоял пропуск —
        # `if token and ...` — и на свежей установке /mcp отвечал вообще без
        # аутентификации. Это была единственная дверь, которую сторожил
        # host-allowlist, а он сторожить не может: `Host` приходит от клиента
        # (измерено 2026-09-01). До пейринга робота нет, значит и обслуживать
        # тут некого.
        if (self.mode == "standalone" and not token
                and scope["type"] == "http"):
            if self.journal is not None:
                self.journal.refuse(
                    "mcp:unpaired",
                    "к MCP обратились до связки с роботом — отказано",
                    "нет robot_token")
            await JSONResponse(
                {"error": "unpaired",
                 "detail": "робот ещё не связан с этим мостом — /mcp закрыт"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        if token and scope["type"] == "http":
            auth = ""
            for k, v in scope.get("headers", []):
                if k == b"authorization":
                    auth = v.decode("latin-1")
                    break
            if auth != f"Bearer {token}":
                if self.journal is not None:
                    self.journal.refuse(
                        "mcp:badtoken",
                        "к MCP обратились без верного токена робота — отказано",
                        "Authorization не совпал")
                await JSONResponse(
                    {"error": "unauthorized"}, status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _snapshot(consent: ConsentEngine, audit: AuditLog) -> dict[str, Any]:
    reqs = consent.pending_all()
    req = reqs[0] if reqs else None
    return {
        "paused": consent.paused,
        "pending": ({"id": req.id, "tool": req.tool,
                     "class": req.tool_class.value,
                     "summary": req.summary} if req else None),
        "pending_count": len(reqs),
        "grant_left_s": int(consent.grant_active(ToolClass.ACT)),
        "recent": audit.recent(20),
    }


class EventBus:
    """Snapshot poller → per-subscriber queues. Skeleton-grade by design."""

    def __init__(self, snapshot_fn, interval: float = 0.2) -> None:
        self._snapshot_fn = snapshot_fn
        self._interval = interval
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subs.add(q)
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(self._snapshot_fn())        # initial state, always
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def pump(self) -> None:
        last: dict | None = None
        while True:
            snap = self._snapshot_fn()
            if snap != last:
                last = snap
                for q in list(self._subs):
                    with contextlib.suppress(asyncio.QueueFull):
                        q.put_nowait(snap)
            await asyncio.sleep(self._interval)


def _now_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat(timespec="seconds")


def _bundle_resources() -> Path | None:
    """`Contents/Resources` when running from a signed .app, else None.

    Anchored on `vbboot`, never on this file. `vbboot` is the shell and never
    leaves the bundle; `vibebridge` is the payload and lives in Application
    Support the moment the first update lands. Anchoring here would find the
    bundle exactly once — on a fresh install — and every later update would
    refuse itself for want of a public key.

    None is what a development checkout gets, and it makes `install` refuse
    deliberately: the trust anchor is the signed bundle, so code running
    outside one has no channel it is entitled to trust.
    """
    import vbboot
    here = Path(vbboot.__file__).resolve()
    for parent in here.parents:
        if parent.name == "Resources" and parent.parent.name == "Contents":
            return parent
    return None


def _payload_source(root: Path, running: str) -> str:
    """Where the running code came from, for the settings card."""
    if _bundle_resources() is None:
        return "dev"
    return "payload" if (root / running).is_dir() else "seed"


#: Our own code carries NO-STORE, and the reason is measured rather than
#: theoretical. `FileResponse` sends only `etag` and `last-modified`; with no
#: explicit freshness a client may fall back to a heuristic, and WKWebView
#: served the widget's page from its disk cache ACROSS an app restart. The pet
#: therefore ran yesterday's JavaScript inside today's app: a message the new
#: page was supposed to post never arrived, and the bug looked like a broken
#: native handler for three rounds — until `rm -rf ~/Library/WebKit/<bundle>`
#: made the identical drag work (2026-09-01).
#:
#: A payload update replaces exactly these files. A cache that outlives the
#: update makes the update mechanism a lie, which is a worse defect than any
#: single bug it hides. Images are deliberately NOT included: the PWA's
#: service worker caches them on purpose, for offline.
_NO_STORE = {"Cache-Control": "no-store, must-revalidate"}


def _code_file(path, media: str | None = None) -> Response:
    """A page or script of ours, served so the browser cannot keep it."""
    if media is None:
        return FileResponse(path, headers=_NO_STORE)
    return FileResponse(path, media_type=media, headers=_NO_STORE)


def build_app(*, consent: ConsentEngine, audit: AuditLog, state: BridgeState,
              runner: Runner | None = None,
              capabilities: dict[str, Capability] | None = None,
              mcp_allowed_hosts: list[str] | None = None,
              robot: RobotClient | None = None,
              notify=None,
              push_sender: PushSender | None = None,
              settings=None,
              peer_guard: bool = False) -> Starlette:
    from .config import load as _load_settings
    from .net import allowed_hosts as _net_allowed_hosts

    if settings is None:
        settings = _load_settings()

    if push_sender is None:
        push_sender = PushSender(state)

    if robot is None:
        robot = RobotClient(base_url=state.robot_base_url,
                            chat_url=state.robot_chat_url,
                            # общий секрет: bridge_api робота гейтит ОБА
                            # канала одним robot_token (fallback для пар,
                            # заключённых до фикса chat_key-дефолта)
                            chat_key=(state.robot_chat_key
                                      or state.robot_token),
                            name=state.robot_name or "робот")
    notify = notify or (lambda title, text: None)
    _base_notify = notify

    def notify(title: str, text: str):
        """Everything the robot puts on this computer is one stream.

        The head says it too: a notification is the robot talking, and the
        owner asked for it to come from the character rather than only from a
        grey system banner. It still goes to the system — a toast survives a
        hidden pet.
        """
        line = f"{title}: {text}" if title else text
        try:
            robot_events.append({"ts": _now_iso(), "kind": "notify",
                                 "text": line})
            mascot.say(line, kind="notify")
        except Exception:                       # noqa: BLE001 - never fatal
            pass
        return _base_notify(title, text)
    robot_state: dict = {"configured": robot.configured, "online": False,
                         "reason": "робот не подключён к панели"
                         if not robot.configured else "ещё не проверял"}
    robot_events: deque[dict] = deque(maxlen=50)
    # The live thread, per conversation. Bounded and forgotten on a new
    # session: enough for the brain to follow what was just said, not an
    # archive (vision, «Не мессенджер»).
    chat_history: dict[str, deque] = {}
    chat_inflight: set[str] = set()      # один ход на сессию (A-6)
    missed_while_paused = {"n": 0}
    # The face. It derives pause and pending from the engine rather than
    # keeping its own copy — two sources of truth for "is the bridge paused"
    # is how a mascot ends up smiling at a stopped bridge.
    mascot = Mascot(consent=consent, robot_state=robot_state)

    # The robot's notifications go through the app's own notifier, so they
    # carry its name and icon instead of arriving unattributed.
    from .capabilities import set_notifier
    set_notifier(notify)
    caps = capabilities or build_capabilities()
    availability = probe_availability(caps)
    _refusals = _RefusalJournal(audit)

    if mcp_allowed_hosts is None:
        mcp_allowed_hosts = _net_allowed_hosts(state)
    mcp = build_server(consent=consent, audit=audit, runner=runner,
                       capabilities=caps, availability=availability,
                       allowed_hosts=mcp_allowed_hosts)

    def _full_snapshot() -> dict[str, Any]:
        snap = _snapshot(consent, audit)
        snap["robot"] = dict(robot_state)
        snap["robot_events"] = list(robot_events)[-10:]
        return snap
    # The transport keeps its own /mcp path and the inner app mounts at the
    # ROOT, after every panel route. Mounting at "/mcp" instead produces a
    # 307 → /mcp/ whose Location is built from the Host header — for the
    # gateway (Host = 100.x:4000, proxied verbatim) that redirect points AT
    # THE GATEWAY ITSELF and the robot's call never lands (measured
    # 2026-08-29). The wire contract is "/mcp exactly, no redirect" (M4).
    mcp.settings.streamable_http_path = "/mcp"
    bus = EventBus(_full_snapshot)

    def _authed(request: Request) -> bool:
        return request.cookies.get(PANEL_COOKIE) == state.panel_token

    async def index(request: Request) -> Response:
        token = request.query_params.get("token")
        if token is not None:
            if token != state.panel_token:
                return JSONResponse({"error": "forbidden"}, status_code=403)
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(PANEL_COOKIE, state.panel_token, httponly=True,
                            samesite="lax")
            return resp
        if not _authed(request):
            # A person, not a program, is reading this. The token is never in
            # the page: whoever can see it here has not proved anything yet.
            return HTMLResponse(_DOOR_HTML, status_code=401)
        return _code_file(_WEBUI / "index.html")

    async def api_state(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(_full_snapshot())

    async def consent_decide(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        decision = _DECISIONS.get(str(body.get("decision", "")))
        if decision is None:
            return JSONResponse({"error": "bad decision"}, status_code=400)
        req_id = body.get("id")
        if req_id:
            done = consent.resolve_by_id(str(req_id), decision, by="panel")
        else:
            req = consent.pending()
            done = req.resolve(decision, by="panel") if req else False
        if not done:
            return JSONResponse(
                {"error": "запрос уже решён или истёк"}, status_code=404)
        return JSONResponse({"ok": True})

    async def api_pause(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        consent.paused = bool(body.get("paused"))
        return JSONResponse({"ok": True, "paused": consent.paused})

    async def api_revoke_grants(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        consent.revoke_grants()
        return JSONResponse({"ok": True})

    async def api_capabilities(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({
            name: {"class": caps[name].tool_class.value, **info}
            for name, info in availability.items()
        })

    async def api_robot_status(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        st = await robot.status()
        robot_state.clear()
        robot_state.update(st)
        return JSONResponse(st)

    async def api_robot_chat(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        text = str(body.get("text", "")).strip()
        if not text:
            return JSONResponse({"error": "empty"}, status_code=400)
        # A session id lets "новый диалог" mean something: the brain keeps the
        # context, so a new id is how the owner starts a fresh one. The panel
        # and the pet each pass their own.
        session = str(body.get("session") or "panel")[:64]
        # Один ход на сессию за раз. Ход мозга не идемпотентен: пока он идёт,
        # он мог уже опубликовать пост, снять кадр со вспышкой и открыть
        # вкладку. Второй клик по «Отправить» (или совет «повторите позже»,
        # понятый буквально) исполнил бы это второй раз — A-6.
        if session in chat_inflight:
            return JSONResponse(
                {"ok": False, "undelivered": True,
                 "error": "ход уже идёт — дождитесь ответа"}, status_code=409)
        chat_inflight.add(session)
        thread = chat_history.setdefault(session, deque(maxlen=20))
        mascot.thinking(True)
        try:
            answer = await robot.chat(text, session=session,
                                      history=list(thread))
        finally:
            mascot.thinking(False)
            chat_inflight.discard(session)
        # The brain's own reply, spoken by the face. Nothing is composed here.
        # The key is `reply` — `RobotClient.chat` has always returned that, and
        # reading `text` here meant the mascot silently never spoke a single
        # chat answer (caught live 2026-08-31: it went to "thinking" and then
        # said nothing, while the chat itself was working).
        if answer.get("ok") and answer.get("reply"):
            thread.append({"role": "user", "content": text})
            thread.append({"role": "assistant",
                           "content": str(answer["reply"])})
            mascot.say(str(answer["reply"]), kind="chat")
        return JSONResponse(answer)

    async def api_robot_media(request: Request) -> Response:
        """Отдать странице файл робота. Имя берётся из события; проверяется
        и здесь, и у робота — обход каталога не должен зависеть от того,
        насколько две стороны доверяют друг другу."""
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        name = request.path_params["name"]
        got = await robot.media(name)
        if got is None:
            return JSONResponse({"error": "файл недоступен"}, status_code=404)
        body, kind = got
        return Response(body, media_type=kind,
                        headers={"Cache-Control": "private, max-age=3600"})

    async def api_robot_system(request: Request) -> Response:
        """Состояние системы робота — то, ради чего панель перестаёт быть
        хуже телеграм-бота: температура, нагрузка, память, диск, воздух и
        живость сервисов, из его же канонического снимка."""
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(await robot.system())

    async def api_robot_update(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(await robot.trigger_update())

    async def api_push_vapid(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        await asyncio.to_thread(ensure_vapid_keys, state)
        return JSONResponse({"key": state.vapid_public})

    async def api_push_subscribe(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        sub = body.get("subscription")
        if not isinstance(sub, dict):
            return JSONResponse({"error": "bad subscription"}, status_code=400)
        try:
            push_sender.add_subscription(sub)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True,
                             "count": len(state.push_subscriptions)})

    async def api_push_unsubscribe(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        removed = push_sender.remove_subscription(str(body.get("endpoint", "")))
        return JSONResponse({"ok": removed})

    async def api_phone(request: Request) -> Response:
        """Everything the settings card needs to guide phone setup: the
        MagicDNS name, whether `tailscale serve` already fronts the panel,
        and the exact command when it does not. The bridge never runs the
        command itself — changing serve config is the owner's move."""
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from .net import serve_active, tailnet_dns_name
        from .server import BRIDGE_PORT
        dns = await asyncio.to_thread(tailnet_dns_name)
        active = (await asyncio.to_thread(serve_active, BRIDGE_PORT)
                  if dns else False)
        https_url = f"https://{dns}/" if dns and active else None
        return JSONResponse({
            "dns_name": dns,
            "serve_active": active,
            "https_url": https_url,
            # The tokened link IS the phone onboarding: the owner sends it
            # to their own phone (AirDrop/Notes). Served only behind panel
            # auth — whoever sees this already holds the token.
            "phone_link": (f"{https_url}?token={state.panel_token}"
                           if https_url else None),
            "setup_command": f"tailscale serve --bg {BRIDGE_PORT}",
            "subscriptions": len(state.push_subscriptions),
        })

    async def pair(request: Request) -> Response:
        """The robot's door (spec §3). Auth = the one-shot pairing token —
        NOT the panel cookie: the caller is the robot's provision script.
        Burned on first success; a replay gets 403."""
        import secrets as _secrets
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "bad json"}, status_code=400)
        offered = str(body.get("token", ""))
        expected = state.pending_pair_token
        if not expected or not _secrets.compare_digest(offered, expected):
            audit.record(tool="pair", tool_class="act", decision="deny",
                         ok=False, line="попытка пейринга с неверным токеном")
            return JSONResponse({"error": "неверный или погашенный токен"},
                                status_code=403)
        state.pending_pair_token = None            # one-shot: burned now
        state.robot_token = state.robot_token or _secrets.token_urlsafe(32)
        name = str(body.get("name", "")).strip() or "робот"
        state.robot_name = name
        if body.get("base_url"):
            state.robot_base_url = str(body["base_url"])
        if body.get("chat_url"):
            state.robot_chat_url = str(body["chat_url"])
        if body.get("chat_key"):
            state.robot_chat_key = str(body["chat_key"])
        else:
            # bridge_api робота авторизует оба канала одним robot_token —
            # он и есть чат-ключ, если робот не назвал отдельный.
            state.robot_chat_key = state.robot_token
        state.save()
        robot.configure(base_url=state.robot_base_url,
                        chat_url=state.robot_chat_url,
                        chat_key=state.robot_chat_key, name=name)
        robot_state.update({"configured": robot.configured})
        # «Связан ✓» без адреса — ложь на весь остаток жизни установки:
        # панель рядом скажет «не подключён», и владелец будет искать причину
        # в сети. Токен принят — но так и говорим (A-5).
        if robot.configured:
            line = f"робот «{name}» связан с мостом"
            toast = f"Робот «{name}» связан с мостом ✓"
        else:
            line = (f"робот «{name}» предъявил токен, но не назвал свой "
                    f"адрес — панель покажет «не подключён»")
            toast = (f"Робот «{name}» принят, но адреса не назвал — "
                     f"подключения не будет")
        audit.record(tool="pair", tool_class="act", decision="allow", ok=True,
                     line=line)
        notify("vibe-bridge", toast)
        from .net import serve_active, tailnet_dns_name
        from .server import BRIDGE_PORT
        dns = await asyncio.to_thread(tailnet_dns_name)
        https_ok = (await asyncio.to_thread(serve_active, BRIDGE_PORT)
                    if dns else False)
        mcp_url = (f"https://{dns}/mcp" if https_ok
                   else f"http://127.0.0.1:{BRIDGE_PORT}/mcp")
        return JSONResponse({"robot_token": state.robot_token,
                             "mcp_url": mcp_url})

    async def api_wizard_pairing_start(request: Request) -> Response:
        """Arm pairing: mint the one-shot token and hand the wizard the
        payload it writes to the SD (or shows as a code path later)."""
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from . import wizard as wiz
        from .net import serve_active, tailnet_dns_name
        from .server import BRIDGE_PORT
        token = wiz.pairing_token()
        state.pending_pair_token = token
        state.save()
        dns = await asyncio.to_thread(tailnet_dns_name)
        https_ok = (await asyncio.to_thread(serve_active, BRIDGE_PORT)
                    if dns else False)
        bridge_url = (f"https://{dns}" if https_ok else
                      f"http://{dns or '127.0.0.1'}:{BRIDGE_PORT}")
        return JSONResponse({"token": token, "bridge_url": bridge_url})

    async def api_wizard_disks(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from . import wizard as wiz
        return JSONResponse({
            "disks": await asyncio.to_thread(wiz.list_removable_disks),
            "boot_volumes": await asyncio.to_thread(wiz.find_boot_volumes)})

    async def api_wizard_prepare(request: Request) -> Response:
        """Prepare an ALREADY-FLASHED boot partition (stock Raspberry Pi OS)
        mounted at `mount_path`: firstrun + Wi-Fi + provision unit + token.
        Full image download+write is WIZARD-b (needs elevation UX)."""
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from . import wizard as wiz
        body = await request.json()
        mount = Path(str(body.get("mount_path", "")))
        if not mount.is_dir() or not (mount / "cmdline.txt").exists():
            return JSONResponse(
                {"error": "это не boot-раздел Raspberry Pi OS — не вижу "
                          "cmdline.txt"}, status_code=400)
        for fld in ("ssid", "psk", "name"):
            if not str(body.get(fld, "")).strip():
                return JSONResponse({"error": f"нужно поле {fld}"},
                                    status_code=400)
        start = await api_wizard_pairing_start(request)
        info = json.loads(bytes(start.body))
        hostname = str(body.get("hostname") or "robot-" +
                       str(body["name"]).lower())[:32]
        try:
            written = await asyncio.to_thread(
                wiz.prepare_boot_partition, mount,
                hostname=hostname, ssid=str(body["ssid"]),
                psk=str(body["psk"]), token=info["token"],
                bridge_url=info["bridge_url"], name=str(body["name"]),
                # The panel never passed this, so the wizard always cloned
                # the hardcoded default — a fork's robot got someone else's
                # repository written onto its card.
                repo_url=settings.robot_repo)
        except OSError as exc:
            return JSONResponse(
                {"error": f"не удалось записать на карту: {exc}"},
                status_code=500)
        return JSONResponse({"ok": True, "written": written,
                             "bridge_url": info["bridge_url"]})

    async def api_version(request: Request) -> Response:
        """What code is running, where it came from, what is waiting.

        `pending` is the honest half: an installed version is NOT the running
        one until the next launch (ADR-0006), and a panel that showed only
        the newest number on disk would report an update the robot is not
        actually talking to.
        """
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from vbboot import layout as _layout

        from . import __version__
        from .autostart import status as autostart_status

        root = _layout.payload_root()
        installed = await asyncio.to_thread(_layout.active_version, root)
        pending = pending_version(installed, __version__)
        auto = await asyncio.to_thread(autostart_status)
        return JSONResponse({
            "running": __version__,
            "source": _payload_source(root, __version__),
            "pending": pending,
            "pending_note": ("обновление скачано и применится после "
                             "перезапуска моста" if pending else ""),
            # Настройка, а не константа: владелец форка меняет
            # `release.repo`, и две карточки панели показывали
            # два разных репозитория.
            "repo": settings.release_repo,
            "auto_update": bool(getattr(state, "auto_update", True)),
            "autostart": {"state": auto.state, "human": auto.human,
                          "supported": auto.supported, "detail": auto.detail},
        })

    async def api_update_check(request: Request) -> Response:
        """Ask the channel, and take what it offers. Every outcome — nothing
        newer, unreachable, refused signature, installed — leaves the bridge
        running and, when it matters, a line in the journal."""
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from vbboot import layout as _layout

        from . import __version__, update

        result = await asyncio.to_thread(update.check, current=__version__)
        if result.found is None:
            # Two different answers, and the owner is told which one it is.
            message = (result.error or
                       "обновлений нет — установлена последняя версия")
            if result.error:
                audit.record(tool="update", tool_class="SYS",
                             decision="unavailable", ok=False,
                             line=f"проверка обновлений: {result.error}",
                             detail=result.error)
            return JSONResponse({"found": False, "installed": False,
                                 "reachable": not result.error,
                                 "message": message})
        found = result.found

        from vbboot.runner import shell_version
        shell = shell_version()
        if shell is None:
            return JSONResponse({
                "found": True, "installed": False, "version": found.version,
                "message": ("мост запущен не из установленного приложения — "
                            "обновляться нечему"),
            })
        ok, why = await asyncio.to_thread(
            update.fetch_and_install, found, _layout.payload_root(),
            pubkey=update.bundled_public_key(_bundle_resources()),
            shell_version=shell)
        audit.record(tool="update", tool_class="SYS",
                     decision="auto" if ok else "unavailable", ok=ok,
                     line=(f"обновление {found.version}: {why}"), detail=why)
        return JSONResponse({"found": True, "installed": ok,
                             "version": found.version, "message": why})

    async def api_robot_attach(request: Request) -> Response:
        """Attach a robot that already exists.

        The SD-card wizard covers a NEW Raspberry Pi; a person whose robot is
        already running had no path at all — the panel's only door was
        "прошейте карту". This is the other door SCN-017 promised.
        """
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json() if await request.body() else {}
        base_url = str(body.get("base_url", "")).strip().rstrip("/")
        if not base_url:
            return JSONResponse(
                {"error": "нужен адрес робота (bridge-API), например "
                          "https://robot.tailnet.ts.net"}, status_code=400)
        if not base_url.startswith(("http://", "https://")):
            return JSONResponse(
                {"error": "адрес должен начинаться с http:// или https://"},
                status_code=400)

        key = str(body.get("key", "")).strip()
        state.robot_base_url = base_url
        state.robot_chat_url = (str(body.get("chat_url", "")).strip()
                                or state.robot_chat_url)
        # standalone gates /mcp on this token, so it must exist the moment a
        # robot is attached — not later, when someone remembers.
        import secrets as _secrets
        state.robot_token = state.robot_token or _secrets.token_urlsafe(32)
        state.robot_chat_key = key or state.robot_token
        name = str(body.get("name", "")).strip() or "робот"
        state.robot_name = name
        await asyncio.to_thread(state.save)

        robot.configure(base_url=state.robot_base_url,
                        chat_url=state.robot_chat_url,
                        chat_key=state.robot_chat_key, name=name)
        robot_state.update({"configured": robot.configured})
        audit.record(tool="pair", tool_class="act", decision="allow", ok=True,
                     line=f"робот «{name}» привязан вручную")
        notify("vibe-bridge", f"Робот «{name}» связан с мостом ✓")
        return JSONResponse({
            "ok": True, "name": name,
            # What the owner must copy into the robot's own configuration.
            "robot_token": state.robot_token,
            "bridge_url": f"http://127.0.0.1:{settings.port}",
        })

    async def api_mascot(request: Request) -> Response:
        """What the character shows right now, for both surfaces."""
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        # The skin travels with the state so a surface never has to ask twice.
        return JSONResponse({**mascot.snapshot(),
                             "skin": settings.mascot_skin})

    async def api_mascot_session(request: Request) -> Response:
        """The pet's conversation id — read it, or mint a new one.

        Server-side because the feed is: a page-local id was regenerated on
        every reload, so the owner kept their visible history while the robot
        started over. One of them had to move, and the id is the cheap one.
        """
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        import secrets as _s
        if request.method == "POST" or not state.pet_session:
            state.pet_session = "pet-" + _s.token_hex(5)
            await asyncio.to_thread(state.save)
            if request.method == "POST":
                # A new conversation starts with a clean feed; the old turns
                # stay in the journal.
                robot_events.clear()
                chat_history.clear()
        return JSONResponse({"session": state.pet_session})

    async def api_mascot_stream(request: Request) -> Response:
        """Everything the robot has said or shown lately, newest last.

        One stream: replies, events and notifications are all the robot
        communicating with its owner, and splitting them across surfaces is
        what made the widget feel like three half-features.
        """
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"items": list(robot_events)[-40:]})

    async def api_mascot_dismiss(request: Request) -> Response:
        """The owner clicked the bubble away. Better than waiting out a timer
        they did not set."""
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        mascot.dismiss()
        return JSONResponse(mascot.snapshot())

    async def mascot_page(request: Request) -> Response:
        """The floating window's page. Same auth as the panel: it answers
        consent requests, so it is the panel by another name."""
        token = request.query_params.get("token")
        if token is not None and token == state.panel_token:
            # Carry every OTHER parameter across the redirect. Dropping them
            # cost the widget its `surface` marker: both of its windows loaded
            # as the pet, so the companion page never existed, and a click on
            # the head reached a document with nothing to open. Nothing failed
            # loudly — the click simply did nothing (measured 2026-08-31).
            rest = [(k, v) for k, v in request.query_params.multi_items()
                    if k != "token"]
            target = "/mascot" + (f"?{urlencode(rest)}" if rest else "")
            resp = RedirectResponse(target, status_code=303)
            resp.set_cookie(PANEL_COOKIE, state.panel_token, httponly=True,
                            samesite="lax")
            return resp
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return _code_file(_WEBUI / "mascot.html")

    async def api_mascot_actions(request: Request) -> Response:
        """The quick phrases for the pet's menu."""
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from .config import load
        live = await asyncio.to_thread(load)
        return JSONResponse({"actions": list(live.mascot_actions)})

    async def api_onboarding(request: Request) -> Response:
        """What is still missing, as an ordered list the panel can render."""
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        attached = bool(state.robot_base_url)
        steps = [
            {"id": "robot", "title": "Подключить робота",
             "done": attached,
             "hint": ("робот на связи" if attached else
                      "новая Raspberry Pi — через карту; уже работающий "
                      "робот — вручную, по адресу и ключу")},
            {"id": "phone", "title": "Открыть панель на телефоне",
             "done": bool(state.push_subscriptions),
             "hint": "нужен Tailscale и включённый serve — см. «Телефон»"},
        ]
        return JSONResponse({"robot_attached": attached, "steps": steps,
                             "done": all(s["done"] for s in steps)})

    async def api_settings(request: Request) -> Response:
        """The settings in force — never the file's wish.

        `gateway_ok` is the honest half: in gateway mode the MCP endpoint has
        no bearer check at all, because the agentgateway on this machine is
        supposed to be the boundary. When it is not running there IS no
        boundary, and the panel has to say so rather than print the mode and
        look calm.
        """
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from .config import config_path, load
        from .net import gateway_reachable

        # In force = what this process started with. The file is read only to
        # report its problems and to say whether a restart is owed. Reporting
        # the FILE would mean the panel shows a port the bridge is not
        # listening on the moment someone edits it.
        live = settings
        on_disk = await asyncio.to_thread(load)
        pending = [
            name for name in ("port", "mode", "release_repo",
                              "update_enabled", "update_interval_s",
                              "ask_timeout_s")
            if getattr(live, name) != getattr(on_disk, name)
        ]
        body = {
            "path": str(config_path()),
            "port": live.port,
            "mode": live.mode,
            "release_repo": live.release_repo,
            "update_enabled": live.update_enabled,
            "update_interval_hours": round(live.update_interval_s / 3600, 2),
            "ask_timeout_s": live.ask_timeout_s,
            "ask_for_read": live.ask_for_read,
            "robot_repo": live.robot_repo,
            "mascot_window": live.mascot_window,
            "mascot_skin": live.mascot_skin,
            "problems": on_disk.problems,
            "pending": pending,
            "restart_required": bool(pending),
            "mcp_url": (f"http://127.0.0.1:{live.port}/mcp"
                        if live.mode == "gateway"
                        else f"http://<адрес в tailnet>:{live.port}/mcp"),
        }
        if live.mode == "gateway":
            ok = await asyncio.to_thread(gateway_reachable)
            body["gateway_ok"] = ok
            body["mcp_auth"] = "нет — границей служит agentgateway"
            if not ok:
                body["warning"] = (
                    "режим gateway, но agentgateway на этой машине не "
                    "отвечает: MCP-эндпоинт сейчас БЕЗ аутентификации. "
                    "Переключитесь на standalone или запустите шлюз.")
        else:
            body["gateway_ok"] = None
            body["mcp_auth"] = ("bearer-токен робота"
                                if state.robot_token else
                                "токен появится после связки с роботом")
        return JSONResponse(body)

    async def api_settings_save(request: Request) -> Response:
        """Change a setting from the panel. Values the reader would reject are
        refused here, so the panel never reports a success the bridge will not
        honour."""
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from .config import update as save_settings
        body = await request.json() if await request.body() else {}
        try:
            await asyncio.to_thread(save_settings, dict(body))
        except (ValueError, OSError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)},
                                status_code=400)
        audit.record(tool="settings", tool_class="SYS", decision="auto",
                     ok=True, line=f"настройки изменены: {', '.join(body)}",
                     detail="")
        return JSONResponse({"ok": True, "restart_required": True})

    async def api_autoupdate(request: Request) -> Response:
        """The owner's switch for background updating (SCN-021)."""
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json() if await request.body() else {}
        state.auto_update = bool(body.get("enabled", True))
        await asyncio.to_thread(state.save)
        audit.record(tool="update", tool_class="SYS", decision="auto", ok=True,
                     line=("автообновление включено" if state.auto_update
                           else "автообновление выключено владельцем"),
                     detail="")
        return JSONResponse({"auto_update": state.auto_update})

    async def api_autostart(request: Request) -> Response:
        """Turn launch-at-login on or off from the panel. The system switch
        in Login Items stays authoritative — this only asks."""
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from .autostart import disable, enable
        from .autostart import status as autostart_status

        body = await request.json() if await request.body() else {}
        want = bool(body.get("enabled", True))
        ok, why = await asyncio.to_thread(enable if want else disable)
        if ok and want:
            state.autostart_registered = True
            await asyncio.to_thread(state.save)
        audit.record(tool="autostart", tool_class="SYS",
                     decision="auto" if ok else "unavailable", ok=ok,
                     line=(f"автозапуск {'включён' if want else 'выключен'} "
                           f"из панели: {why}"), detail=why)
        auto = await asyncio.to_thread(autostart_status)
        return JSONResponse({"ok": ok, "message": why, "state": auto.state,
                             "human": auto.human,
                             "supported": auto.supported})

    async def api_journal(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        q = request.query_params
        try:
            offset = max(0, int(q.get("offset", 0)))
            limit = min(200, max(1, int(q.get("limit", 50))))
        except ValueError:
            return JSONResponse({"error": "bad paging"}, status_code=400)
        flt = q.get("filter", "all")
        if flt not in ("all", "refused", "act", "read"):
            return JSONResponse({"error": "bad filter"}, status_code=400)
        return JSONResponse(audit.read_entries(flt=flt, offset=offset,
                                               limit=limit))

    async def events(request: Request) -> Response:
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        async def stream():
            q = bus.subscribe()
            try:
                while True:
                    snap = await q.get()
                    yield f"event: state\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
            finally:
                bus.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    async def _consent_push_watcher() -> None:
        """Every NEW pending consent goes out as a web push (SCN-004). The
        blocking pywebpush call runs in a worker thread; a lost push costs
        nothing — the timeout default stands."""
        seen: set[str] = set()
        while True:
            reqs = consent.pending_all()
            for req in reqs:
                if req.id in seen:
                    continue
                seen.add(req.id)
                if state.push_subscriptions and not consent.paused:
                    await asyncio.to_thread(push_sender.send_to_all, {
                        "kind": "consent", "id": req.id,
                        "title": "Робот просит разрешение",
                        "summary": req.summary,
                    })
            if len(seen) > 500:
                live = {r.id for r in reqs}
                seen.intersection_update(live)
            await asyncio.sleep(0.5)

    async def _robot_poller() -> None:
        """Refresh the cached robot status; announce the pause-summary on
        the paused→active transition (SCN-010 alt)."""
        was_paused = consent.paused
        while True:
            if robot.configured:
                st = await robot.status()
                robot_state.clear()
                robot_state.update(st)
            if was_paused and not consent.paused and missed_while_paused["n"]:
                notify("vibe-bridge",
                       f"За время паузы: {missed_while_paused['n']} событий "
                       f"робота — они в ленте")
                missed_while_paused["n"] = 0
            was_paused = consent.paused
            await asyncio.sleep(10.0)

    async def _robot_event_consumer() -> None:
        """Consume the robot's proactive events; OS-notify unless paused
        (paused events collect silently — SCN-010). Reconnects calmly."""
        while True:
            if not robot.configured:
                await asyncio.sleep(30.0)
                continue
            async for ev in robot.events():
                ev = {"ts": ev.get("ts") or _now_iso(),
                      "kind": ev.get("kind", "event"),
                      "text": str(ev.get("text", ""))[:400],
                      # Optional, for when the robot starts sending media:
                      # {"url": …, "type": "image"|"audio"|"video"|"link"}.
                      "media": ev.get("media") or None}
                robot_events.append(ev)
                if consent.paused:
                    missed_while_paused["n"] += 1
                else:
                    notify(robot.name, ev["text"] or ev["kind"])
                    # The robot's own words, verbatim — the mascot composes
                    # nothing («Не второй мозг»).
                    mascot.say(ev["text"] or ev["kind"], kind="event")
            await asyncio.sleep(10.0)          # stream ended — quiet retry

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            tasks = [asyncio.create_task(bus.pump()),
                     asyncio.create_task(_robot_poller()),
                     asyncio.create_task(_robot_event_consumer()),
                     asyncio.create_task(_consent_push_watcher())]
            try:
                yield
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    # PWA shell files live at the ORIGIN ROOT (a service worker's scope
    # cannot exceed its path). No auth: they contain no secrets, and the
    # SW must load before any cookie exists on the phone.
    def _static(name: str, media: str):
        async def handler(request: Request) -> Response:
            return _code_file(_WEBUI / name, media)
        return handler

    async def icon(request: Request) -> Response:
        size = int(request.path_params["size"])
        if size not in (180, 192, 512):
            return JSONResponse({"error": "not found"}, status_code=404)
        # The real mark, generated by scripts/make_icon.py and committed.
        # The solid tile stays as a fallback so a phone that installs the PWA
        # never gets a broken image if the asset is missing from a payload.
        art = _WEBUI / f"icon-{size}.png"
        body = art.read_bytes() if art.is_file() else _solid_png(size)
        return Response(body, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/sw.js", _static("sw.js", "application/javascript")),
            Route("/manifest.webmanifest",
                  _static("manifest.webmanifest",
                          "application/manifest+json")),
            Route("/offline.html", _static("offline.html", "text/html")),
            Route("/icon-{size:int}.png", icon),
            Route("/api/state", api_state),
            Route("/api/consent/decide", consent_decide, methods=["POST"]),
            Route("/api/pause", api_pause, methods=["POST"]),
            Route("/api/grants/revoke", api_revoke_grants, methods=["POST"]),
            Route("/api/capabilities", api_capabilities),
            Route("/api/version", api_version),
            Route("/api/update/check", api_update_check, methods=["POST"]),
            Route("/api/autostart", api_autostart, methods=["POST"]),
            Route("/api/autoupdate", api_autoupdate, methods=["POST"]),
            Route("/api/robot/attach", api_robot_attach, methods=["POST"]),
            Route("/api/onboarding", api_onboarding),
            Route("/api/mascot", api_mascot),
            Route("/api/mascot/actions", api_mascot_actions),
            Route("/api/mascot/dismiss", api_mascot_dismiss,
                  methods=["POST"]),
            Route("/api/mascot/stream", api_mascot_stream),
            Route("/api/mascot/session", api_mascot_session),
            Route("/api/mascot/session", api_mascot_session,
                  methods=["POST"]),
            Route("/mascot", mascot_page),
            Route("/mascot.js", _static("mascot.js", "application/javascript")),
            Route("/tokens.css", _static("tokens.css", "text/css")),
            Route("/api/settings", api_settings),
            Route("/api/settings", api_settings_save, methods=["POST"]),
            Route("/api/journal", api_journal),
            Route("/api/robot/status", api_robot_status),
            Route("/api/robot/chat", api_robot_chat, methods=["POST"]),
            Route("/api/robot/update", api_robot_update, methods=["POST"]),
            Route("/api/robot/system", api_robot_system),
            Route("/api/robot/media/{name:str}", api_robot_media),
            Route("/api/push/vapid", api_push_vapid),
            Route("/api/push/subscribe", api_push_subscribe,
                  methods=["POST"]),
            Route("/api/push/unsubscribe", api_push_unsubscribe,
                  methods=["POST"]),
            Route("/api/phone", api_phone),
            Route("/pair", pair, methods=["POST"]),
            Route("/api/wizard/pairing/start", api_wizard_pairing_start,
                  methods=["POST"]),
            Route("/api/wizard/disks", api_wizard_disks),
            Route("/api/wizard/prepare", api_wizard_prepare,
                  methods=["POST"]),
            Route("/events", events),
            Mount("/", app=BearerGuard(mcp.streamable_http_app(), state,
                                       settings.mode, _refusals)),
        ],
        lifespan=lifespan,
    )
    # Взводится вызывающим — он один знает, какой интерфейс занят.
    return PeerGuard(app, peer_guard, _refusals) if peer_guard else app
