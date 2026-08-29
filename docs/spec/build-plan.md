# vibe-bridge — build-план (для следующего рана)

Стадия 4 v2-design run 2026-08-29. Карта модулей зафиксирована; каждый
ST/SCN живёт ровно в одном модуле; walking skeleton — первым. Build-ран
берёт этот файл как вход своего брифа; его REQ-таблица = строки Implements
отсюда (set-comparison обязана сойтись).

## Walking skeleton — WS (первым, до любого модуля)

Тончайший сквозной срез, после которого всё остальное — наращивание:

1. Starlette-приложение: Mount FastMCP (`streamable_http_app`, внешний
   lifespan `session_manager.run()`) + bearer-middleware + одна страница
   панели + SSE `/events`.
2. Один консент-запрос проходит новый путь: робот (эмулятор в тесте) →
   ACT → карточка на странице → решение → исполнение → журнал → SSE.
3. Трей-абстракция запускает это на macOS (rumps) без регрессии M1–M4.

- **Implements:** SCN-001 (новая поверхность), инварианты спеки §1–§4
- **DoD:** существующие 24 теста зелёные + тесты mount/auth/SSE; ручная
  проверка живьём с роботом (gateway-режим не сломан)

## Модули

### M-CORE — консент v2 + журнал + карта способностей
- **Scope:** id-шные консент-запросы, мультиповерхностные решения
  («первое валидное выигрывает», `resolved-elsewhere`), сводное
  уведомление после паузы, probe-регистрация способностей
  (available/needs-permission/unavailable), человеческие строки журнала,
  ротация.
- **Implements:** ST-003, ST-010, ST-012 · SCN-002, SCN-003, SCN-005,
  SCN-011, SCN-018, SCN-020
- **Контракты:** spec §4, §5, §10; consent.py сохраняет семантику M1–M4.

### M-NORTH — MCP-поверхность
- **Scope:** платформо-нейтральные имена инструментов + `mac_*`-алиасы,
  bearer-auth, TransportSecurity с allowed_hosts (возврат защиты),
  standalone/gateway режимы бинда.
- **Implements:** ST-001 (транспортная часть) · SCN-001 · ADR-0002
- **Контракты:** spec §2, §3, §5; research-notes §A.

### M-PANEL — панель + трей + уведомления
- **Scope:** SCR-01..04, SCR-08 (дашборд, чат-вью, журнал-вью, настройки,
  трей-состояния), SSE-подписка, desktop-notifier + osascript-fallback,
  консент-диалог/карточка, workbench-токены (`docs/design/ui.md`).
- **Implements:** ST-004 (вью), ST-006 (показ) · SCN-006, SCN-010 (UI-часть),
  SCN-017
- **Контракты:** spec §7; design/ui.md; screens.md.

### M-SOUTH — клиент робота
- **Scope:** чат через Hermes HTTP (bearer, 150 с + retry-once),
  статус/события/апдейт через bridge-API робота, честные состояния
  offline/undelivered, данные для дашборда.
- **Implements:** ST-005, ST-009, ST-012 (данные) · SCN-007, SCN-008,
  SCN-009, SCN-012
- **Контракты:** spec §0, §6, §10. **Зависимость:** M-ROBOT (внешний).

### M-PHONE — PWA + пуши
- **Scope:** manifest (standalone), service worker + офлайн-страницы
  (два вида недоступности), подписка и VAPID-отправка, страница решения
  консента, Android-кнопки на уведомлении, помощник настройки
  `tailscale serve` (проверка MagicDNS/HTTPS + объяснение).
- **Implements:** ST-002 · SCN-004, SCN-019
- **Контракты:** spec §2, §4, §7; research-notes §C; ADR-0004.

### M-WIZARD — онбординг новой Pi
- **Scope:** SCR-06/07: скачивание образа, запись SD (elevation per-OS),
  firstrun/cloud-init генерация, NM-Wi-Fi-профиль, provision-юнит,
  пейринг-токен (FAT, self-delete), mDNS/tailnet-ожидание, чеклист с
  тестовым ACT, диагностика таймаута, путь «робот уже работает» (код).
- **Implements:** ST-007, ST-008 · SCN-013, SCN-014, SCN-015, SCN-016
- **Контракты:** spec §8; research-notes §B; ADR-0001. **Зависимость:**
  M-ROBOT (предъявление токена, код пейринга).

### M-PLATFORM — Win/Linux паки + упаковка
- **Scope:** capability-паки Windows/Linux по таблице паритета (mss,
  PyGetWindow, windows-toasts, xdotool/kdotool, порталы, wl-clipboard,
  PowerShell-automation с блоклистом), pystray-бэкенд, упаковка
  Briefcase/PyInstaller + подписи + автообновление, переименование
  `macbridge`→`vibebridge` (CO-1).
- **Implements:** ST-011 · SCN-018 (Win/Linux ветки) · ADR-0003, ADR-0005
- **Контракты:** spec §5, §9; research-notes §D, §E.

### M-ROBOT — bridge-API робота (внешний: `rpi-ai-assistant`, CO-4)
- **Scope:** `GET /bridge/status`, `SSE /bridge/events`,
  `POST /bridge/update`, `POST /bridge/pair` + код пейринга голосом/в
  Telegram; бинд за loopback (tailnet). Blind-update-safe, default-off до
  пейринга.
- **Implements:** серверная половина SCN-012, SCN-015 (код), событий
  SCN-010
- **Контракты:** spec §6. Ведётся пайплайном в репо робота.

## Порядок и зависимости

```
WS → M-CORE → M-NORTH → M-PANEL → M-SOUTH ⇄ M-ROBOT → M-PHONE → M-WIZARD → M-PLATFORM
```

- M-ROBOT стартует параллельно сразу после WS (другой репозиторий, свой
  пайплайн); M-SOUTH мокает его контракт до готовности.
- M-PHONE раньше M-WIZARD: телефонный консент ценен оператору немедленно,
  визард нужен внешнему пользователю (P-02) — у него зависимость на
  M-PLATFORM-упаковку всё равно.
- Каждый модуль: TDD, ревью после задачи, `/ux-audit` по своим SCN на
  выходе, verification-строка на каждый shipped ST.

## Покрытие (проверка полноты карты)

- ST-001..012 — все в ровно одном модуле (ST-001 — WS+M-NORTH: WS несёт
  сквозной срез, M-NORTH — транспортный контракт; в брифе build-рана
  ST-001 числится за M-NORTH).
- SCN-001..020 — все распределены (SCN-010 разложен: показ в M-PANEL,
  источник в M-ROBOT; SCN-012/015 аналогично — панельная и роботная
  половины названы).
- Carry-over на build: CO-1 (в M-PLATFORM), CO-2 (Figma-проход перед
  M-PANEL-вёрсткой), CO-3 (brand-init + строки перед M-PANEL-финалом),
  CO-5 (в M-PLATFORM), алиасы `mac_*` — снятие при бампе Hermes.
