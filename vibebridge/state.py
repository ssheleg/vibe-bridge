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
from dataclasses import asdict, dataclass, field
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

    @classmethod
    def load(cls, path: Path | None = None) -> BridgeState:
        path = path or default_state_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(path=path,
                       panel_token=data["panel_token"],
                       robot_token=data.get("robot_token"),
                       mode=data.get("mode", "gateway"),
                       robot_base_url=data.get("robot_base_url"),
                       robot_chat_url=data.get("robot_chat_url"),
                       robot_chat_key=data.get("robot_chat_key"),
                       robot_name=data.get("robot_name"),
                       vapid_private=data.get("vapid_private"),
                       vapid_public=data.get("vapid_public"),
                       push_subscriptions=data.get("push_subscriptions", []),
                       pending_pair_token=data.get("pending_pair_token"))
        state = cls(path=path, panel_token=secrets.token_urlsafe(32))
        state.save()
        return state

    def save(self) -> None:
        data = asdict(self)
        data.pop("path")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
