# vibe-bridge

Пульт и руки робота на компьютере владельца: **robot-vibecoder ↔ этот компьютер**,
человек в контуре. Панель (статус/чат/журнал/согласия), PWA с пушами на телефон,
MCP-инструменты с консентом, онбординг новой Pi.

> Vision:
> [docs/ux/vision.md](docs/ux/vision.md) · scenarios: [docs/ux/scenarios.md](docs/ux/scenarios.md) ·
> architecture: [docs/spec/architecture.md](docs/spec/architecture.md)

The robot's brain (Hermes on a Raspberry Pi) reaches this Mac over Tailscale
**through the agentgateway** (role `robot`), never directly. This app is a
loopback-only MCP server plus a menu-bar UI that gates every *action* behind
your explicit consent and logs everything.

```
Robot (Pi) ──Tailscale──▶ agentgateway :4000 (role robot) ──localhost──▶ mac-bridge :48620
                                                                          ├ MCP server (10 tools)
                                                                          ├ menu bar: 🤖 pause / consent / log
                                                                          └ audit.log (~/Library/Logs/mac-bridge)
```

## Tools

| Tool | Class | |
|---|---|---|
| `mac_screenshot`, `mac_list_apps`, `mac_frontmost`, `mac_notify` | **READ** | run immediately, logged |
| `mac_open_app`, `mac_open_url`, `mac_shortcut_run`, `mac_applescript`, `mac_clipboard_read/write` | **ACT** | ask the owner (Allow / Allow 15 min / Deny); 60s no-answer = deny |

Deliberately absent: shell, arbitrary file access. AppleScript is ACT-gated
with an app blocklist (Terminal, Keychain).

## Consent model

- **READ** executes at once. **ACT** raises a menu-bar dialog. A grant lasts
  15 minutes per action-class, then asks again.
- **Kill switch**: menu → pause → every tool (READ too) returns 503-style
  refusal. A paused bridge looks like a closed laptop to the robot.
- **Audit**: every call (allowed or refused) → `~/Library/Logs/mac-bridge/audit.log`
  (0600) and the last few in the menu.

## Run (dev)

```bash
uv sync
uv run python -m vibebridge.app       # tray + panel + MCP server
```

## Packaging (for real macOS permissions)

System Events / screen capture need TCC permission, granted to a **bundled
.app**, not to a bare `python`. `scripts/build_app.sh` wraps it with py2app;
first launch prompts for Accessibility + Screen Recording. Until packaged,
READ tools that touch System Events fail fast with an honest error (the robot
says "Мак недоступен для этого действия") — by design, never a hang.

## Wire parity

`mcp==1.26.0` — the exact SDK generation Hermes 0.19 ships. Speaking the same
version on both ends removes a class of protocol-revision breakage. Bump
**together** with the robot's `HERMES_VERSION`.

## Tests

```bash
uv run python -m pytest tests/ -q     # 24 tests, no screen needed
```
