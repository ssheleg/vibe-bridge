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
from dataclasses import asdict, dataclass
from pathlib import Path


def default_state_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "mac-bridge"
    elif os.name == "nt":  # pragma: no cover - exercised on Windows only
        base = Path(os.environ.get("APPDATA", Path.home())) / "mac-bridge"
    else:  # pragma: no cover - exercised on Linux only
        base = Path(os.environ.get("XDG_CONFIG_HOME",
                                   Path.home() / ".config")) / "mac-bridge"
    return base / "state.json"


@dataclass
class BridgeState:
    path: Path
    panel_token: str
    robot_token: str | None = None
    mode: str = "gateway"          # gateway (loopback, no MCP auth) | standalone

    @classmethod
    def load(cls, path: Path | None = None) -> BridgeState:
        path = path or default_state_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(path=path,
                       panel_token=data["panel_token"],
                       robot_token=data.get("robot_token"),
                       mode=data.get("mode", "gateway"))
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
