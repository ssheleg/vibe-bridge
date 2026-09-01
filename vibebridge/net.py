"""Network identity helpers — which hosts this bridge answers to.

The DNS-rebinding protection that M4 switched off comes back here as an
explicit allowlist (spec §2): loopback always, plus this machine's tailnet
addresses — the agentgateway proxies the robot's request verbatim, so the
Host header arrives as the gateway's own tailnet address (measured
2026-08-28), and that address IS one of this machine's tailscale IPs.
Detection is fail-open: no tailscale CLI → loopback-only list, gateway mode
still works because the gateway targets loopback.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from .state import BridgeState

_TAILSCALE_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


#: Both lookups below shell out to the Tailscale CLI (~1.3 s each here) and
#: both are called on every `build_app`. In production that is once per launch;
#: in the test suite it was a subprocess pair per web test and turned a
#: ten-second run into four minutes — which is how a suite stops being run at
#: all. The answers change when Tailscale reconnects, so they are held for a
#: minute rather than forever.
_TAILSCALE_TTL_S = 60.0
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, produce, *, force: bool = False):
    now = time.monotonic()
    hit = _cache.get(key)
    if not force and hit is not None and now - hit[0] < _TAILSCALE_TTL_S:
        return hit[1]
    value = produce()
    _cache[key] = (now, value)
    return value


def _tailscale_exe() -> str | None:
    return shutil.which("tailscale") or (
        _TAILSCALE_APP if Path(_TAILSCALE_APP).exists() else None)


def tailscale_ips(*, force: bool = False) -> list[str]:
    def produce() -> list[str]:
        exe = _tailscale_exe()
        if not exe:
            return []
        try:
            out = subprocess.run([exe, "ip"], capture_output=True, text=True,
                                 timeout=3.0)
        except (OSError, subprocess.TimeoutExpired):
            return []
        if out.returncode != 0:
            return []
        return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]

    # A copy: a caller mutating the result must not poison the next one.
    return list(_cached("ips", produce, force=force))


def allowed_hosts(state: BridgeState,
                  tailnet_ips: list[str] | None = None,
                  dns_name: str | None = None) -> list[str]:
    """Host-header allowlist for the MCP transport, any port (`host:*`).
    Includes the MagicDNS name so a tailnet-HTTPS client (`tailscale
    serve`) reaching /mcp is not 421'd for its Host."""
    ips = tailscale_ips() if tailnet_ips is None else tailnet_ips
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    for ip in ips:
        hosts.append(f"[{ip}]:*" if ":" in ip else f"{ip}:*")
    name = tailnet_dns_name() if dns_name is None else dns_name
    if name:
        hosts += [name, f"{name}:*"]
    return hosts


def tailnet_dns_name(*, force: bool = False) -> str | None:
    """This machine's MagicDNS name (no trailing dot) — the PWA/push origin
    once `tailscale serve` fronts the panel (ADR-0004). Fail-open None."""
    def produce() -> str | None:
        exe = _tailscale_exe()
        if not exe:
            return None
        try:
            out = subprocess.run([exe, "status", "--json"],
                                 capture_output=True, text=True, timeout=3.0)
            import json
            name = json.loads(out.stdout).get("Self", {}).get("DNSName", "")
            return name.rstrip(".") or None
        except Exception:
            return None

    return _cached("dns", produce, force=force)


def serve_active(port: int) -> bool:
    """True when `tailscale serve` already fronts the given local port."""
    exe = shutil.which("tailscale") or (
        _TAILSCALE_APP if Path(_TAILSCALE_APP).exists() else None)
    if not exe:
        return False
    try:
        out = subprocess.run([exe, "serve", "status"], capture_output=True,
                             text=True, timeout=3.0)
        return f"127.0.0.1:{port}" in out.stdout or f":{port}" in out.stdout
    except Exception:
        return False


def standalone_bind_host() -> str:
    """standalone binds ALL interfaces, and the guard is the host allowlist.

    It used to bind the tailnet interface ALONE when one was up. That looked
    like hardening and was in fact a broken application: the panel, the app's
    own window and both widget windows all address the bridge as
    `BRIDGE_HOST` — `127.0.0.1` — and loopback was not bound at all.

    Measured on this machine 2026-09-01: `lsof -iTCP:48620` showed LISTEN on
    the tailnet IPv4 only, a connect to `127.0.0.1:48620` was refused, and the
    pet was rendering WKWebView's "cannot connect" page where its head should
    be. The default mode for a new install is standalone, so this was every
    new owner with Tailscale running.

    The boundary was never the bind. standalone requires a bearer on `/mcp`,
    and the host allowlist answers 421 to any Host outside loopback and the
    tailnet — a request arriving on a LAN interface is refused BY NAME, not by
    the absence of a socket. What binding one interface bought was that a LAN
    scanner saw a closed port; what it cost was the application.

    The two alternatives, recorded because they are real: stay in `gateway`
    mode (loopback only) when the robot never needs to reach the bridge
    directly, or move remote reach to `tailscale serve` — already the
    documented path for the phone — and bind loopback alone. Both change how
    pairing works, so neither is taken here without the owner.
    """
    return "0.0.0.0"  # noqa: S104 - guarded by bearer + host allowlist


def gateway_reachable(port: int = 4000, host: str = "127.0.0.1",
                      timeout: float = 1.5) -> bool:
    """Is an agentgateway actually listening on this machine?

    In `gateway` mode the bridge does NOT check a bearer token on /mcp — the
    gateway is the authentication boundary (ADR-0002). If nothing is there,
    the boundary the mode assumes does not exist and the endpoint is open to
    every local process. Answering this honestly is what lets the panel say so
    instead of printing the mode and looking calm.
    """
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

#: Сети, из которых мост согласен принимать соединения, когда слушает не
#: только loopback. Tailscale выдаёт узлам адреса из CGNAT-диапазона
#: 100.64.0.0/10 (IPv4) и fd7a:115c:a1e0::/48 (IPv6) — попасть в источник
#: такого пакета из локальной сети нельзя, потому что TCP-рукопожатие требует
#: обратного маршрута.
_TRUSTED_PEER_NETS = ("127.0.0.0/8", "::1/128",
                      "100.64.0.0/10", "fd7a:115c:a1e0::/48")


def peer_allowed(host: str | None) -> bool:
    """Можно ли принять соединение ОТ ЭТОГО адреса.

    Настоящая граница сети — адрес пира, а не заголовок `Host`. Allowlist по
    `Host` существует против DNS-rebinding, где браузер жертвы подставляет
    чужое имя; он НЕ защищает от того, кто соединяется напрямую, потому что
    `Host` приходит от клиента: достаточно назвать `127.0.0.1` и проверка
    пройдена (измерено 2026-09-01 на этой машине).

    Неразбираемый или отсутствующий адрес — ОТКАЗ, а не «наверное свои».
    Fail-open в границе безопасности — это отсутствие границы.
    """
    if not host:
        return False
    import ipaddress
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(net, strict=False)
               for net in _TRUSTED_PEER_NETS
               if addr.version == ipaddress.ip_network(net).version)

