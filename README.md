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
Robot (Pi) ──Tailscale──▶ agentgateway :4000 (role robot) ──localhost──▶ vibe-bridge :48620
                                                                          ├ MCP server (10 tools)
                                                                          ├ menu bar: 🤖 pause / consent / log
                                                                          └ audit.log (~/Library/Logs/vibe-bridge)
```

## Установка (macOS)

Скачайте DMG из [релизов](https://github.com/ssheleg/vibe-bridge/releases),
перетащите `vibe-bridge.app` в «Программы» и запустите. Приложение живёт в
меню-баре (без иконки в доке), само регистрируется в System Settings → General
→ Login Items и **обновляется само**: скачивает только собственный код,
проверяет Ed25519-подпись до распаковки и применяет версию при следующем
запуске. Права, выданные приложению (запись экрана, автоматизация), при
обновлении не сбрасываются — подпись бандла не меняется (ADR-0006).

Сломанная версия не может оставить вас без моста: она помечается карантином, и
мост поднимается на предыдущем коде в том же запуске. Текущая версия, источник
и автозапуск видны в панели → Настройки → «Приложение».

Сборка из исходников — `scripts/build_app.sh` (см. `docs/spec/packaging.md`).

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
- **Audit**: every call (allowed or refused) → `~/Library/Logs/vibe-bridge/audit.log`
  (0600) and the last few in the menu.

## Настройки

Всё, что можно поменять, лежит в `~/Library/Application Support/vibe-bridge/config.toml`
(создаётся при первом запуске, с комментариями): порт, режим сети, канал
обновлений, интервал проверки, таймаут согласия. Приоритет: переменная
окружения → файл → умолчание; `VIBE_BRIDGE_PORT` и `VIBE_BRIDGE_MODE`
переопределяют файл для одного запуска.

Неверное значение **не останавливает мост**: он берёт умолчание и показывает
причину в панели (Настройки → Доступ и настройки). Правки применяются после
перезапуска — панель говорит, что они ждут.

Два режима сети:

| Режим | Как робот доходит | Защита `/mcp` |
|---|---|---|
| `standalone` (умолчание) | по адресу в tailnet | bearer-токен робота |
| `gateway` | только loopback, через agentgateway на этой же машине | **никакой** — границей служит шлюз |

Режим `gateway` без запущенного agentgateway оставляет MCP-эндпоинт без
аутентификации. Мост это проверяет и говорит прямо в панели.

## Подключить робота

Два пути, оба в «Настройках»:

- **Новая Raspberry Pi** — визард пишет на SD-карту Wi-Fi, имя и одноразовый
  ключ связки; карта едет в робота, он сам приходит на `/pair`.
- **Уже работающий робот** — форма: адрес его bridge-API и ключ. Мост выдаёт
  токен, который вы прописываете роботу.

## Запуск из исходников (разработка)

```bash
uv sync
uv run python -m vibebridge.app       # трей + панель + MCP
```

У этого пути **нет автообновления**: обновление проверяется подписью, а ключ
лежит в подписанном бандле, которого здесь нет. Панель честно пишет «запущен
из исходников». Автостарт для разработки — `launchd/me.sshlg.vibe-bridge.dev.plist`
(инструкция в самом файле); одновременно с установленным `.app` не включайте —
они подерутся за порт.

TCC-права (запись экрана, System Events) выдаются **бандлу**, а не голому
`python`: до упаковки такие инструменты честно отказывают, а не висят.

## Сборка своей версии

`scripts/build_app.sh` собирает, подписывает и (с `--notarize`) нотаризует
`.app`. Подпись берётся из `VIBE_SIGN_IDENTITY`, профиль нотаризации — из
`VIBE_NOTARY_PROFILE`. Свой форк = свой канал: поменяйте `release.repo` в
`config.toml`, иначе мост будет качать чужие payload'ы и отвергать каждый по
несовпадению подписи. Подробности — `docs/spec/packaging.md`.


## Wire parity

`mcp==1.26.0` — the exact SDK generation Hermes 0.19 ships. Speaking the same
version on both ends removes a class of protocol-revision breakage. Bump
**together** with the robot's `HERMES_VERSION`.

## Tests

```bash
uv run python -m pytest tests/ -q     # 252 теста, экран не нужен
```
