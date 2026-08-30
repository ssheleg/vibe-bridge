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
from pathlib import Path

from .state import BridgeState

_TAILSCALE_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def tailscale_ips() -> list[str]:
    exe = shutil.which("tailscale") or (
        _TAILSCALE_APP if Path(_TAILSCALE_APP).exists() else None)
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "ip"], capture_output=True, text=True,
                             timeout=3.0)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


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


def tailnet_dns_name() -> str | None:
    """This machine's MagicDNS name (no trailing dot) — the PWA/push origin
    once `tailscale serve` fronts the panel (ADR-0004). Fail-open None."""
    exe = shutil.which("tailscale") or (
        _TAILSCALE_APP if Path(_TAILSCALE_APP).exists() else None)
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True,
                             text=True, timeout=3.0)
        import json
        name = json.loads(out.stdout).get("Self", {}).get("DNSName", "")
        return name.rstrip(".") or None
    except Exception:
        return None


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
    """standalone mode binds the tailnet interface when one is up; without
    it we fall back to all interfaces — the bearer token and the host
    allowlist stay as the guard (spec §2, recorded deviation)."""
    for ip in tailscale_ips():
        if ":" not in ip:          # first IPv4
            return ip
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
