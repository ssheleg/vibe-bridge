# Board — vibe-bridge

Seeded 2026-08-29 by the v2 design run (first pipeline run of this repo).
Priorities re-derived at each run's close-out.

| ID | Priority | Title | Source | Status |
|---|---|---|---|---|
| B-1 | P1 | Build: T-WS/CORE/NORTH/PANEL/SOUTH/PHONE/WIZARD-a/PLATFORM-паки **shipped**; live-проверка Win/Linux паков и трея — нужны машины | brief-2026-08-29-build | packs done, live pending |
| B-9 | P2 | WIZARD-b: скачивание+запись образа с элевацией (authopen/UAC/pkexec) + степпер SCR-06 целиком; live с физической картой | build-plan M-WIZARD | open |
| B-8 | P1 | Human: ~~Serve включён, HTTPS live~~ → остался телефон: открыть phone_link из настроек панели, iPhone «На экран Домой», «Включить пуши» | T-PHONE 2026-08-29 | half-done |
| B-2 | P1 | ~~Robot-side bridge-API~~ **ЗАКРЫТО 2026-08-29**: смержено в fleet-ветку робота (a2348d9), после merge проверено живьём (v1.1.0+181 build 108, чат отвечает) | brief-2026-08-29 CO-4 | done |
| B-3 | P2 | ~~Переименование → vibe-bridge~~ **ЗАКРЫТО 2026-08-30**: пакет/репо/машина/плист, миграция state+логов (пейринг цел) | brief-2026-08-29 CO-1 | done |
| B-4 | P2 | Упаковка/подпись per-OS + автообновление — runbook готов (docs/spec/packaging.md), исполнение требует ОС+сертификатов | brief-2026-08-29 CO-5 | runbook ready |
| B-5 | P3 | `/brand-init` + copywriting панели/визарда | brief-2026-08-29 CO-3 | open |
| B-6 | P3 | Figma-визуальный проход по wireframes | brief-2026-08-29 CO-2 | open |
| B-7 | P3 | Снять `mac_*`-алиасы инструментов — вместе с бампом Hermes/`mcp` | spec §5, close-out 2026-08-29 | open |
