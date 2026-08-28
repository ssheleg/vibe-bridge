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
from .capabilities import Capability, CapabilityError, Runner, build_capabilities
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
) -> FastMCP:
    runner = runner or Runner()
    caps = capabilities or build_capabilities()
    # DNS-rebinding protection validates the Host header; the agentgateway
    # proxies the robot's request verbatim, so the Host arrives as the
    # gateway's own tailnet address (e.g. 100.x:4000), not our loopback —
    # which the default allowlist rejects with 421 Misdirected Request
    # (measured 2026-08-28). The real security boundary is elsewhere: only
    # the gateway on this Mac can reach loopback :48620, and consent gates
    # every ACT. So we disable Host validation here rather than pin a
    # tailnet IP that changes — reachability is already limited to loopback.
    from mcp.server.transport_security import TransportSecuritySettings
    mcp = FastMCP(
        "mac-bridge", host=BRIDGE_HOST, port=BRIDGE_PORT,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False),
    )

    for cap in caps.values():
        _register(mcp, cap, consent=consent, audit=audit, runner=runner)

    return mcp


def dispatch(cap: Capability, args: dict, *, consent: ConsentEngine,
             audit: AuditLog, runner: Runner) -> dict[str, Any]:
    """Pure-ish core: consent → handler → audit. Returns a result dict.

    Tested directly (test_server.py) without standing up the HTTP layer.
    """
    decision = consent.request(cap.name, cap.tool_class, cap.summary(args))
    if not allowed(decision):
        audit.record(tool=cap.name, tool_class=cap.tool_class.value,
                     decision=decision.value, ok=False)
        return {"ok": False, "refused": True,
                "reason": refusal_text(decision)}
    try:
        out = cap.handler(runner, args)
    except CapabilityError as exc:
        audit.record(tool=cap.name, tool_class=cap.tool_class.value,
                     decision=decision.value, ok=False, detail=str(exc))
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        log.exception("tool %s crashed", cap.name)
        audit.record(tool=cap.name, tool_class=cap.tool_class.value,
                     decision=decision.value, ok=False, detail=repr(exc))
        return {"ok": False, "error": f"internal error: {exc}"}
    audit.record(tool=cap.name, tool_class=cap.tool_class.value,
                 decision=decision.value, ok=True,
                 detail=(out[:60] if isinstance(out, str) else ""))
    return {"ok": True, "result": out}


def _register(mcp: FastMCP, cap: Capability, *, consent, audit, runner) -> None:
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
    params = ", ".join(f"{k}: str = ''" for k in cap.input_schema)
    forward = ", ".join(f"{k!r}: {k}" for k in cap.input_schema)
    ns: dict = {"_dispatch": dispatch, "cap": cap, "consent": consent,
                "audit": audit, "runner": runner}
    src = (
        f"async def {cap.name}({params}) -> dict:\n"
        f"    return _dispatch(cap, {{{forward}}}, "
        f"consent=consent, audit=audit, runner=runner)\n"
    )
    exec(src, ns)  # noqa: S102 - names are our own capability keys, not input
    fn = ns[cap.name]
    fn.__doc__ = doc
    mcp.tool()(fn)
