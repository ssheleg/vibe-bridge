"""Bridge state — the identities this process answers to.

One small JSON file, 0600, in the user's config dir. It holds tokens, not
preferences: `panel_token` gates the owner's own panel (generated once at
first load), `robot_token` gates the MCP endpoint (None in gateway mode —
then the agentgateway on this machine is the auth boundary and the bridge
stays loopback-only, exactly the M1–M4 shape). Secrets never reach logs.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def _config_base(name: str) -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / name
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        return Path(os.environ.get("APPDATA", Path.home())) / name
    return Path(os.environ.get("XDG_CONFIG_HOME",  # pragma: no cover - Linux
                               Path.home() / ".config")) / name


def default_state_path() -> Path:
    base = _config_base("vibe-bridge")
    legacy = _config_base("mac-bridge")
    # Волна переименования (ADR-0005): один атомарный перенос каталога —
    # пейринг-креды и VAPID-ключи обязаны пережить рестарт под новым именем.
    if legacy.is_dir() and not base.exists():
        try:
            legacy.rename(base)
        except OSError:  # pragma: no cover - разные тома и т.п.
            base = legacy          # честно живём на старом пути
    return base / "state.json"


@dataclass
class BridgeState:
    path: Path
    panel_token: str
    robot_token: str | None = None
    mode: str = "gateway"          # gateway (loopback, no MCP auth) | standalone
    # South side (spec §6): where THIS bridge finds its robot. None until
    # pairing (M-WIZARD) or the owner fills them in.
    robot_base_url: str | None = None    # bridge-API робота
    robot_chat_url: str | None = None    # Hermes gateway (OpenAI-совм.)
    robot_chat_key: str | None = None    # API_SERVER_KEY
    robot_name: str | None = None
    # Phone push (ADR-0004): VAPID pair + browser subscriptions.
    vapid_private: str | None = None
    vapid_public: str | None = None
    push_subscriptions: list = field(default_factory=list)
    # One-shot pairing token (spec §3): set when the wizard arms pairing,
    # burned on first successful /pair.
    pending_pair_token: str | None = None
    # Когда он выдан. Без этого токен жил вечно и переживал рестарты,
    # а его копия остаётся на FAT-разделе карты (A-22).
    pending_pair_token_at: float | None = None
    # Launch at login (SCN-022). Set once, the first time the app registers
    # itself as a Login Item. It exists so the bridge asks the system ONCE:
    # without it, an owner who switches autostart off in System Settings
    # would find it back on after the next launch, and a switch the app keeps
    # undoing is not a switch.
    autostart_registered: bool = False
    # Background self-update (SCN-021). On by default — a bridge holding the
    # owner's screen should not sit on a known-fixed version — but the switch
    # is theirs, and the panel shows which way it is set.
    auto_update: bool = True
    # The pet's conversation id. It lives here, not in the page: the feed is
    # server-side and survives a reload, so a page-local id meant the owner
    # saw their own history while the robot answered "это новая сессия".
    pet_session: str | None = None
    #: Where the owner dragged the pet, in Cocoa screen coordinates (x, y of
    #: its bottom-left corner). Restored with clamping to the CURRENT screen:
    #: an origin saved on a second display is off-canvas once that display is
    #: unplugged, and a pet nobody can see is a pet nobody can drag back.
    pet_pos: list | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> BridgeState:
        path = path or default_state_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(path=path, **cls._known(data))
        state = cls(path=path, panel_token=secrets.token_urlsafe(32))
        state.save()
        return state

    @classmethod
    def _known(cls, data: dict) -> dict:
        """Поля класса, взятые из файла. Список НЕ повторяется здесь.

        `save()` пишет `asdict(self)` — то есть все поля, — а `load` до
        2026-09-02 перечисляла четырнадцать штук руками. Пятнадцатое поле
        писалось на диск и молча терялось при каждом рестарте: файл
        правильный, состояние пустое, и никакой ошибки (F-10).

        Умолчания берутся у самого dataclass, а не пишутся вторыми копиями
        рядом — иначе `mode` умел бы отличаться в объявлении и здесь.

        Ключи, которых у класса нет, игнорируются намеренно: файл, написанный
        БОЛЕЕ НОВОЙ версией моста, не должен ронять старую — это откат, и он
        обязан работать (ADR-0006).
        """
        names = {f.name for f in fields(cls)} - {"path"}
        return {k: v for k, v in data.items() if k in names}

    def save(self) -> None:
        data = asdict(self)
        data.pop("path")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
