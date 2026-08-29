# Brief — vibe-bridge build run

- **Date:** 2026-08-29 · **Model:** Fable 5 · **Mode:** build по
  `docs/spec/build-plan.md`; цикл: задача из бэклога → стадии 3–8 → docs/
  verification в том же изменении → следующая.
- **Вход:** дизайн-ран 2026-08-29 (commit 736cf87) — vision/foundation/
  scenarios/flows/screens, spec, ADR-0001..0005, research-notes, build-plan.
  Грилл: веток не осталось; дизайн утверждён оператором («погнали
  реализовывать», 2026-08-29).

## Записанные операционные решения

1. **Live-проверки трогают живой мост**: LaunchAgent `me.sshlg.mac-bridge`
   рестартуется при проверке WS/модулей — краткое окно недоступности для
   робота приемлемо (машина оператора). Путь `127.0.0.1:48620/mcp` и
   gateway-режим обязаны сохраниться (WS DoD).
2. **Имя пакета `macbridge` не трогаем до M-PLATFORM** (CO-1/B-3): меньше
   диффы, переименование одной волной.
3. **Репа робота**: `~/DATA/microcontrollers/robot-vibecoder` (remote
   `ssheleg/rpi-ai-assistant`); T-ROBOT идёт отдельным пайплайн-раном в ней.

## REQ = задачи цикла (карта — build-plan.md; Implements оттуда)

| Task | Модуль | Implements | Verified by |
|---|---|---|---|
| T-WS | Walking skeleton | SCN-001 (новая поверхность), spec §1–4 | 24 старых + новые тесты web зелёные; live: gateway-цепь работает |
| T-CORE | M-CORE | ST-003/010/012 · SCN-002/003/005/011/018/020 | тесты consent v2/journal/capmap; ux-audit по SCN |
| T-NORTH | M-NORTH | ST-001 · SCN-001 · ADR-0002 | тесты auth/имена/алиасы/transport-security |
| T-PANEL | M-PANEL | ST-004/006 · SCN-006/010/017 | тесты API; браузерная проверка панели |
| T-SOUTH | M-SOUTH | ST-005/009 · SCN-007/008/009/012 | тесты с mock-роботом; live с реальным |
| T-PHONE | M-PHONE | ST-002 · SCN-004/019 | тесты push/PWA; live с телефона |
| T-WIZARD | M-WIZARD | ST-007/008 · SCN-013..016 | тесты генераторов; live-прошивка карты |
| T-PLATFORM | M-PLATFORM | ST-011 · SCN-018(W/L) · CO-1/5 | паки+упаковка; матрица по ОС |
| T-ROBOT | M-ROBOT (внешний) | серверные половины SCN-010/012/015 | пайплайн в репе робота |

Порядок: WS → CORE → NORTH → PANEL → SOUTH (⇄ ROBOT) → PHONE → WIZARD →
PLATFORM. Verification-строка на каждый shipped ST — в `verification.md`
в том же изменении.
