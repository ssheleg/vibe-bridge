# Verification ledger — vibe-bridge

Каждый выкаченный REQ получает строку: как проверен, кем, когда.
Seeded 2026-08-29 (v2 design run). Код этим раном не менялся; строки
появятся с первым build-раном.

| REQ | Shipped | Verified | How | When |
|---|---|---|---|---|
| T-WS (walking skeleton) | web.py/state.py/webui + app.py провязка | yes | 35 тестов зелёные (`pytest -q`); live: `launchctl kickstart` → MCP-клиент listed 10 tools на `127.0.0.1:48620/mcp`, gateway-пинги в `/tmp/mac-bridge.err`, панель 401→303→200 + `/api/state` | 2026-08-29 |
| T-CORE (ST-003, ST-010 ядро; SCN-002/003/005/011/018) | consent v2 (id, first-wins, resolve_by_id), probe-карта способностей + честный отказ до консента, журнал line+ротация, API pause/revoke/capabilities | yes | 49 тестов зелёных; ruff clean; ux-lint 0 err; live: `/api/capabilities` отдаёт карту (screenshot=available → TCC уже выдан этому питону), pause через API включает/выключает | 2026-08-29 |
| T-NORTH (ST-001 транспорт; ADR-0002) | нейтральные имена + 10 `mac_*`-алиасов; DNS-rebinding-защита ON с allowlist (loopback+tailnet `:*`); фикс 307-редиректа маунта (транспорт на своём /mcp, mount в корень); standalone-бинд | yes | 54 теста; live: чужой Host→421, гейтвейный Host→транспорт, e2e через agentgateway ролью robot: 20 tools, `mac_notify` исполнен (`ok:true`, уведомление показано) | 2026-08-29 |
| T-PANEL (ST-001 UI, ST-010; SCN-001/002/011 live) | панель со вкладками (дашборд/журнал/настройки, чат — честная заглушка), журнал с диска (фильтры+пагинация, `/api/journal`), карта способностей в настройках, трей-состояния, «решено с другой поверхности»; **фикс: dispatch → worker-thread (ACT замораживал event loop на время консента)** | yes | 59 тестов (+ регрессия loop-freeze); браузерная проверка Chrome: карточка согласия live по SSE → клик «Разрешить» → робот получил `opened Calculator`, Calculator запустился; SCN-002 живьём (таймаут → отказ → строка в ленте) | 2026-08-29 |
| T-SOUTH (ST-005/009 мост-сторона; SCN-007/008/009/012 mock) | RobotClient (чат Hermes 150с+retry-once; status offline-since sticky; update; events SSE), `/api/robot/*`, чат-вкладка и живая карточка робота, event-consumer с пауза-сводкой; поля робота в state | mock-verified | 71 тест (12 новых, все ветки деградации на MockTransport); live: эндпоинты честно отвечают «робот не подключён»; робот виден в tailnet (100.123.65.23 direct), Hermes :8642 loopback-only → живая активация = M-ROBOT (CO-4, как в спеке §6) | 2026-08-29 |
