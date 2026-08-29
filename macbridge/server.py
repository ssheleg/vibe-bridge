"""MCP server — the wire the robot's Hermes speaks to.

Streamable HTTP on 127.0.0.1:BRIDGE_PORT (loopback only — the tailnet reach
and the auth are the agentgateway's job, never this app's). Each registered
tool routes through the consent engine before its handler runs, and every
call — allowed or refused — lands in the audit log.

The server runs in a worker thread so the rumps menu-bar loop owns the main
thread (macOS requires the UI event loop there). The consent engine is the
only shared state, and it is internally locked.
"""
from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from .audit import AuditLog
from .capabilities import (
    ALIASES,
    Capability,
    CapabilityError,
    Runner,
    build_capabilities,
)
from .consent import ConsentEngine, allowed, refusal_text

log = logging.getLogger("mac-bridge.server")

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 48620


def build_server(
    *,
    consent: ConsentEngine,
    audit: AuditLog,
    runner: Runner | None = None,
    capabilities: dict[str, Capability] | None = None,
    availability: dict[str, dict] | None = None,
    allowed_hosts: list[str] | None = None,
) -> FastMCP:
    from .capabilities import probe_availability

    runner = runner or Runner()
    caps = capabilities or build_capabilities()
    if availability is None:
        availability = probe_availability(caps)
    if allowed_hosts is None:
        allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    # DNS-rebinding protection is ON with an explicit allowlist (spec §2).
    # The agentgateway proxies the robot's request verbatim, so the Host
    # arrives as the gateway's own tailnet address (e.g. 100.x:4000, measured
    # 2026-08-28) — net.allowed_hosts() includes this machine's tailscale
    # addresses with a `:*` port wildcard, which is exactly that case. The
    # M4 off-switch is retired: a foreign Host now gets 421 again.
    from mcp.server.transport_security import TransportSecuritySettings
    mcp = FastMCP(
        "mac-bridge", host=BRIDGE_HOST, port=BRIDGE_PORT,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
        ),
    )

    for cap in caps.values():
        _register(mcp, cap, consent=consent, audit=audit, runner=runner,
                  availability=availability)
    for alias, target in ALIASES.items():
        if target in caps:
            _register(mcp, caps[target], consent=consent, audit=audit,
                      runner=runner, availability=availability, alias=alias)

    return mcp


def dispatch(cap: Capability, args: dict, *, consent: ConsentEngine,
             audit: AuditLog, runner: Runner,
             availability: dict[str, dict] | None = None) -> dict[str, Any]:
    """Pure-ish core: availability → consent → handler → audit.

    Availability is checked BEFORE consent: an impossible action must not
    cost the owner a dialog, and must answer the robot instantly with a
    speakable reason (SCN-018/020). Tested directly (test_server.py,
    test_core_v2.py) without standing up the HTTP layer.
    """
    line = cap.summary(args)
    info = (availability or {}).get(cap.name)
    if info is not None and info.get("status") != "available":
        reason = info.get("reason") or "недоступно на этой системе"
        audit.record(tool=cap.name, tool_class=cap.tool_class.value,
                     decision="unavailable", ok=False, line=line,
                     detail=reason)
        return {"ok": False, "unavailable": True,
                "error": f"На этом компьютере действие недоступно: {reason}"}
    decision = consent.request(cap.name, cap.tool_class, line)
    if not allowed(decision):
        audit.record(tool=cap.name, tool_class=cap.tool_class.value,
                     decision=decision.value, ok=False, line=line)
        return {"ok": False, "refused": True,
                "reason": refusal_text(decision)}
    try:
        out = cap.handler(runner, args)
    except CapabilityError as exc:
        audit.record(tool=cap.name, tool_class=cap.tool_class.value,
                     decision=decision.value, ok=False, line=line,
                     detail=str(exc))
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        log.exception("tool %s crashed", cap.name)
        audit.record(tool=cap.name, tool_class=cap.tool_class.value,
                     decision=decision.value, ok=False, line=line,
                     detail=repr(exc))
        return {"ok": False, "error": f"internal error: {exc}"}
    audit.record(tool=cap.name, tool_class=cap.tool_class.value,
                 decision=decision.value, ok=True, line=line,
                 detail=(out[:60] if isinstance(out, str) else ""))
    return {"ok": True, "result": out}


def _register(mcp: FastMCP, cap: Capability, *, consent, audit, runner,
              availability=None, alias: str | None = None) -> None:
    # FastMCP derives the tool schema from the function SIGNATURE, so every
    # capability is generated as a real async def with its declared args as
    # explicit `str = ''` parameters (a no-arg tool gets a zero-parameter
    # signature — a `**kwargs` entry makes FastMCP demand a `kwargs` argument
    # and every call fails validation, measured 2026-08-28). The body forwards
    # to dispatch(), which owns consent + audit.
    cls = cap.tool_class.value.upper()
    doc = (
        f"[{cls}] {cap.summary_template}. "
        f"{'Требует подтверждения владельца.' if cls == 'ACT' else 'Выполняется сразу.'}"
    )
    fn_name = alias or cap.name
    if alias:
        doc = f"[deprecated alias → {cap.name}] {doc}"
    params = ", ".join(f"{k}: str = ''" for k in cap.input_schema)
    forward = ", ".join(f"{k!r}: {k}" for k in cap.input_schema)
    import functools

    import anyio

    ns: dict = {"_dispatch": dispatch, "cap": cap, "consent": consent,
                "audit": audit, "runner": runner, "availability": availability,
                "_to_thread": anyio.to_thread.run_sync,
                "_partial": functools.partial}
    # dispatch() BLOCKS (consent waits up to 60s, handlers shell out) — it
    # must run in a worker thread. Awaiting it inline froze the whole event
    # loop for the length of a consent dialog: panel, SSE and every other
    # MCP session went dark until the owner answered (measured 2026-08-29,
    # caught by the live browser check).
    src = (
        f"async def {fn_name}({params}) -> dict:\n"
        f"    return await _to_thread(_partial(_dispatch, cap, {{{forward}}}, "
        f"consent=consent, audit=audit, runner=runner, "
        f"availability=availability))\n"
    )
    exec(src, ns)  # noqa: S102 - names are our own capability keys, not input
    fn = ns[fn_name]
    fn.__doc__ = doc
    mcp.tool()(fn)
