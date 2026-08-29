# Verification ledger — vibe-bridge

Каждый выкаченный REQ получает строку: как проверен, кем, когда.
Seeded 2026-08-29 (v2 design run). Код этим раном не менялся; строки
появятся с первым build-раном.

| REQ | Shipped | Verified | How | When |
|---|---|---|---|---|
| T-WS (walking skeleton) | web.py/state.py/webui + app.py провязка | yes | 35 тестов зелёные (`pytest -q`); live: `launchctl kickstart` → MCP-клиент listed 10 tools на `127.0.0.1:48620/mcp`, gateway-пинги в `/tmp/mac-bridge.err`, панель 401→303→200 + `/api/state` | 2026-08-29 |
| T-CORE (ST-003, ST-010 ядро; SCN-002/003/005/011/018) | consent v2 (id, first-wins, resolve_by_id), probe-карта способностей + честный отказ до консента, журнал line+ротация, API pause/revoke/capabilities | yes | 49 тестов зелёных; ruff clean; ux-lint 0 err; live: `/api/capabilities` отдаёт карту (screenshot=available → TCC уже выдан этому питону), pause через API включает/выключает | 2026-08-29 |
