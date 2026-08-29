# Brief — vibe-bridge v2 design run

- **Date:** 2026-08-29 · **Model:** Fable 5 (`claude-fable-5`) · **Mode:** design-only (stages 0–4 + 9–10; build is a separate run)
- **Task:** эволюция mac-bridge → **vibe-bridge**: кроссплатформенный (macOS/Windows/Linux + телефон) bridge между роботом robot-vibecoder (Hermes на Raspberry Pi) и устройством владельца. Деливерабл: vision, UX-фундамент, UX-сценарии, флоу, архитектурная спека, ADR, UI/UX-слой, build-план.

## Operator decisions (grill, 2026-08-29)

| # | Решение | Ответ |
|---|---|---|
| G1 | Бутстрап свежей Pi | **SD-образ, без SSH** — визард готовит карту (Imager + firstrun + ключ пейринга), робот самоустанавливается на первом буте; канон fleet-update-policy не нарушен |
| G2 | Телефон / вне компьютера | **Веб-панель по Tailscale + пуш** — та же панель как PWA с телефона, консент-запросы пушем; телеграм-бот робота остаётся, но bridge его не требует |
| G3 | Стек | **Python-ядро + веб-панель** — ядро (MCP, консент, аудит) остаётся, UI = локальная веб-панель из того же процесса, трей per-OS; wire-парность mcp==1.26.0 сохраняется |
| G4 | Имя | **vibe-bridge** — репо переименуется на build-этапе (carry-over CO-1) |

## Recorded resolutions (autonomy sweep)

- **Дизайн-поверхность: text-only** этим раном. Figma MCP требует интерактивный OAuth → ломает автономию (канон ~/.claude/CLAUDE.md). Figma-проход = CO-2.
- **Copywriting-трек отложен на build** (CO-3): брендпака нет; `/brand-init` перед строками панели. Отклонение записано, не умолчано.
- Доки: `docs/ux/`, `docs/spec/`, `docs/adr/`, `docs/design/`; леджеры `docs/evidence/`. Ветка: master (docs-only, один оператор). Тесты/линт: `uv run python -m pytest tests/ -q` (24), `ruff check` — прогнать в конце как proof «код не тронут». Деплой: отсутствует. Трекера у репо нет; борд = `docs/evidence/backlog.md`.
- **Граф не строим**: репо ~590 строк кода; graphify recommended-not-required. Пересмотр на build.

## Source ledger

| Источник | Взято |
|---|---|
| Код `macbridge/` (server 112, capabilities 197, consent 127, audit 53, app 96 строк) + 24 теста | текущие контракты: 10 tools READ/ACT, consent-движок (60s timeout, 15m grant, kill switch), 421/DNS-rebinding фикс, exec-генерация сигнатур |
| `README.md` | рамка M1–M4, wire-парность `mcp==1.26.0` ↔ Hermes 0.19, TCC-ограничение (нужен .app) |
| `CLAUDE.md`/`CONTEXT.md`/ADR в репо | **нет** — не существуют |
| `docs/evidence/*`, retro, board | **нет** — первый пайплайн-ран, seeded этим раном |
| `graphify-out/` | **нет** |
| Вики `projects/mac-bridge` | состояние M1–M4 подтверждено, паттерн mcp-bridge-with-consent |
| Вики `projects/robot-vibecoder` + concepts (hermes-orchestrator, proactive-telegram-comms, fleet-update-policy, onboarding-flow, remote-access-and-wifi) | **fleet-политика no-SSH / GitHub-only update**; Hermes gateway = OpenAI-совм. HTTP `127.0.0.1:8642` + bearer (`API_SERVER_KEY` в `~/.hermes/.env`); второй профиль `voice` :8652; телеграм-пульт (`/menu`,`/dashboard`, `telegram_message`, reminders); Tailscale remote access проверен; Wi-Fi-портал онбординга робота (R1.33); username-agnostic install; робот-репо `ssheleg/rpi-ai-assistant` |
| Research agents A–E (mcp SDK auth/mount; Pi no-SSH provisioning; Tailscale HTTPS+WebPush; tray+packaging; Win/Linux capability parity) | заземление контрактов спеки — отчёты вливаются в docs/spec/research-notes.md |

## REQ table (frozen; add free, remove — operator)

| REQ | Deliverable | Verified by |
|---|---|---|
| REQ-01 | `docs/ux/vision.md` — что vibe-bridge ЕСТЬ/НЕ ЕСТЬ, границы с роботом и его телеграм-ботом | файл с анти-целями и границами; alignment-правило установлено |
| REQ-02 | `docs/ux/foundation.md` — персоны, JTBD, journeys | каждый сценарий REQ-03 трассируется к JTBD |
| REQ-03 | `docs/ux/scenarios.md` — источник правды user-facing поведения: консент (за компом/телефон/таймаут), пульт, онбординг SD-визард+пейринг, апдейт робота, kill switch, деградации (робот офлайн, tailnet down, TCC не выдан, push не доставлен) | сценарии в проверяемом формате стандарта скила; деградации покрыты явно |
| REQ-04 | `docs/ux/flows.md` — mermaid-флоу + экранные состояния + текстовые wireframes: дашборд, консент (диалог/пуш), визард, настройки, журнал | каждый экран сослан на сценарии |
| REQ-05 | `docs/spec/architecture.md` — компоненты; робот→bridge (MCP+bearer) и bridge→робот (Hermes HTTP); консент v2; пейринг/токены; транспорт Tailscale; capability-паки per-OS; упаковка/дистрибуция; версии; инварианты безопасности; отказные режимы | контракты со ссылками на research-отчёты (URL/file:line) |
| REQ-06 | `docs/adr/0001–0005` — no-SSH SD-бутстрап; встроенные токены (gateway опционален); Python-ядро+веб-панель; телефон=tailnet PWA+push; имя vibe-bridge | каждый ADR: контекст/решение/последствия |
| REQ-07 | `docs/design/ui.md` + `docs/design/preview.html` — токены, темы, motion/degrade-to-calm, применённые к wireframes; превью открыт в браузере | превью открыт `open`; слой сослан на flows |
| REQ-08 | `docs/spec/build-plan.md` — модули, walking skeleton, REQ↔модуль (каждый build-REQ ровно в одном модуле) | set-comparison: brief REQs == union Implements |
| REQ-09 | Леджеры `docs/evidence/{backlog,verification,retro}.md` заведены | файлы существуют; retro застампован раном |
| REQ-10 | Вики `projects/mac-bridge` отражает v2-дизайн | страница обновлена этим раном |

## Carry-over ledger (open)

| ID | Что | Куда |
|---|---|---|
| CO-1 | Переименование репо mac-bridge → vibe-bridge (+ маркетплейс/пути) | build run |
| CO-2 | Figma-визуальный проход по wireframes (интерактивный OAuth) | build run |
| CO-3 | `/brand-init` + copywriting строк панели и визарда | build run |
| CO-4 | Robot-side задача: bridge-facing API (статус/чат/апдейт-триггер) поверх Hermes gateway — задача в репо `rpi-ai-assistant` | robot repo |
| CO-5 | Упаковка/подпись/нотаризация per-OS + автообновление | build run |
| CO-6 | `docs/ux/` пуста | закрывается REQ-01..04 этим раном |

## Close-out (2026-08-29)

Ladder walk: одна absence — в спеке §5 родился новый carry-over «снятие
`mac_*`-алиасов при бампе Hermes» → борд B-7 (заведён до таблицы).
Примечание REQ-04: контракт v4 делит деливерабл на `flows.md` + `screens.md`
— сданы оба; ASCII-wireframes (optional) не создавались, визуальный слой
несёт `docs/design/preview.html`.

| REQ | Evidence |
|---|---|
| REQ-01 | `docs/ux/vision.md` (9 секций контракта, Status: draft — утверждение оператором на build-гейте); правило в `CLAUDE.md`; ссылка из README; линтер: без U030–U033 |
| REQ-02 | `docs/ux/foundation.md`: P-01..03, JTBD-01..04 (все 5 полей — U074 чисто), JRN-01..03, ST-001..012 с Given/When/Then |
| REQ-03 | `docs/ux/scenarios.md`: SCN-001..020, все деградации (SCN-002/005/007/009/014/016/018/019/020); `python3 docs/ux/lint.py` → 0 errors / 5 warn (все U057 — ожидаемо до build) |
| REQ-04 | `docs/ux/flows.md` FLW-01..05 (mermaid, rejected shapes) + `docs/ux/screens.md` SCR-01..09 |
| REQ-05 | `docs/spec/architecture.md` §0–12; каждый контракт → `research-notes.md` A–E (file:line SDK / URL) |
| REQ-06 | `docs/adr/0001..0005` |
| REQ-07 | `docs/design/ui.md` (4 core-секции проговорены) + `preview.html`, открыт в браузере (`open`) |
| REQ-08 | `docs/spec/build-plan.md`: WS + 8 модулей; ST-001..012 и SCN-001..020 распределены без сирот |
| REQ-09 | `docs/evidence/{brief,backlog,verification,retro}.md`; retro застампован |
| REQ-10 | вики `projects/mac-bridge/mac-bridge.md` обновлена (lifecycle: active) |

Код не менялся: `pytest -q` → 24 passed; `ruff check` → clean (вендорный
`docs/ux/lint.py` исключён конфигом — upstream-owned). Carry-over → борд:
CO-1→B-3, CO-2→B-6, CO-3→B-5, CO-4→B-2, CO-5→B-4, CO-6 закрыт, новый →B-7.
