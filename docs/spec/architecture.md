# vibe-bridge — архитектура v2

Статус: locked by v2 design run 2026-08-29 (стадия spec). Рецепты
контрактов — `research-notes.md` (A–E) и вики robot-vibecoder. Решения с
последствиями — `docs/adr/`. Поведение — `docs/ux/scenarios.md`; спека не
дублирует сценарии, она даёт им механизм.

## 0. Константы окружения

| Константа | Значение | Рецепт |
|---|---|---|
| Мозг робота | Hermes gateway, OpenAI-совместимый HTTP `127.0.0.1:8642`, bearer `API_SERVER_KEY` из `~/.hermes/.env`; профиль `voice` на `:8652` | вики `concepts/hermes-orchestrator` |
| Таймаут мозга | 150 с + один retry на медленный ответ | вики, R1.50 |
| Fleet-канон | обновления робота — только его GitHub-таймер; **SSH запрещён** | вики `fleet-update-policy` |
| Wire-парность | `mcp==1.26.0` ↔ Hermes 0.19; **ревизию протокола диктует пин робота, а не свежая MCP-спека** (спека 2026-07-28 уже деприкейтит то, на чём стоит SDK 1.26; бампаются оба конца вместе) | README «Wire parity»; agent-interop rule zero |
| Сеть владельца | Tailscale tailnet (у флота уже в ходу) | вики `remote-access-and-wifi` |

## 1. Компоненты — один процесс, четыре поверхности

```
vibe-bridge (Python 3.12, uv; один процесс на компьютер)
├─ core/
│   ├─ ConsentEngine v2      — классы, гранты, пауза, мультиповерхностные решения
│   ├─ AuditLog              — журнал 0600, ротация, человеческие строки
│   └─ CapabilityRegistry    — probe при старте, per-OS паки (§5)
├─ север (робот → bridge): MCP-сервер
│   └─ FastMCP, смонтированный в общий Starlette (research A)
├─ юг (bridge → робот): RobotClient
│   ├─ чат: Hermes gateway HTTP (bearer, 150 с + retry)
│   └─ статус/события/апдейт: bridge-API робота (контракт §6, CO-4)
├─ панель: Starlette-маршруты + SSE + статика PWA (одна кодовая база
│   для десктоп-браузера и телефона)
├─ push: VAPID Web Push (исходящие POST; research C)
├─ tray: TrayBackend — rumps (macOS) | pystray (Win/Linux); main thread
├─ notify: desktop-notifier; dev-fallback osascript (research D)
└─ wizard: SD-writer + firstrun-генератор + pairing-listener (§8)
```

Потоки: **main thread принадлежит трею** (обязательство обоих бэкендов);
uvicorn + asyncio — worker thread; ConsentEngine — единственная разделяемая
точка, внутренне залочена (унаследовано от M1–M4).

## 2. Сеть, бинды, TLS

Два режима, выбираются конфигом, оба поддерживаются всегда:

| Режим | Кому | MCP-эндпоинт | Панель |
|---|---|---|---|
| **standalone** (дефолт дистрибуции) | P-02 и новые установки | бинд на tailnet-IP, bearer-токен робота | `tailscale serve` → `https://<host>.tailnet.ts.net` (валидный LE-серт; нужен для PWA/пушей) |
| **gateway** (текущая машина оператора) | P-01 сегодня | loopback `:48620`, робот приходит через agentgateway | loopback + serve по желанию |

- DNS-rebinding-защита **включается обратно** с явными
  `allowed_hosts=[tailnet-имя, 100.x-адрес]` — фикс M4 «выключить защиту»
  заменяется конфигурацией (research A; transport_security.py:45–127).
- Funnel/публичная экспозиция — запрещены (vision, анти-облако).

## 3. Идентичность и пейринг

- **Клиентские токены** — статические bearer, по одному на клиента:
  `robot` (MCP-вызовы), `phone-N` (PWA-сессии), `panel` (локальная сессия).
  Хранение: state-файл 0600; в логи не попадают никогда.
- **Пейринг новой Pi**: одноразовый токен генерируется визардом → FAT-файл
  `robot-pairing.token` → провижининг-скрипт робота предъявляет его на
  `POST /pair` → мост выдаёт постоянный робот-токен и записывает identity
  робота; одноразовый токен гасится с обеих сторон (self-delete с FAT —
  research B §5).
- **Пейринг работающего робота**: короткий код, который робот называет
  голосом/в Telegram (генерация кода — контракт робота, CO-4); код вводится
  на SCR-07, дальше та же выдача постоянных ключей.
- Отзыв/ротация — SCR-08 «Устройства и ключи»; отозванный токен получает
  401 немедленно.
- Проверка MCP-auth: внешняя ASGI-middleware вокруг Mount — SDK своих
  middleware без `token_verifier` не ставит, конфликтов нет (research A).

## 4. Консент v2

Ядро M1–M4 сохраняется (классы READ/ACT, грант 15 мин на класс, таймаут
60 с = отказ, пауза бьёт всё, включая READ). Добавляется:

- **Мультиповерхностные решения**: запрос согласия имеет id; решения с
  любой поверхности идут в ConsentEngine, **первое валидное выигрывает**,
  остальные поверхности получают `resolved-elsewhere` через SSE; решение
  после таймаута отвергается (SCN-004).
- **Пуш-канал**: параллельно диалогу мост шлёт Web Push (Android — кнопки
  на уведомлении, iOS — тап на страницу решения; research C). Недоставка
  пуша ничего не ломает — дефолт остаётся «60 с → отказ».
- **Карта способностей** — третье измерение ответа: `available` /
  `needs-permission` (с deep-link в системный диалог) / `unavailable`
  (причина). Регистрируется probe'ом на старте, не в момент вызова
  (research E; SCN-018, SCN-020).
- **Пауза и события**: на паузе события робота копятся в ленте без
  уведомлений; снятие паузы даёт одно сводное уведомление (SCN-010).
- Тексты отказов — на языке владельца, произносимы голосом робота
  (унаследовано: consent.py `refusal_text`).

## 5. Способности (север): паритет v1

Имена становятся платформенно-нейтральными; на переходный период мост
регистрирует старые `mac_*`-алиасы (флот уже зовёт их — снятие алиасов
привязано к бампу Hermes, carry-over).

| Инструмент | Класс | macOS | Windows | Linux X11 | Linux Wayland |
|---|---|---|---|---|---|
| `screenshot` | READ | ship (TCC→needs-permission) | ship (mss) | ship (mss) | ship (portal, первый грант) |
| `list_apps`, `frontmost` | READ | ship | ship (PyGetWindow) | ship (xdotool) | degrade (KDE kdotool / GNOME честная ошибка) |
| `notify` | READ | ship | ship (windows-toasts) | ship (notify-send) | ship |
| `open_app`, `open_url` | ACT | ship | ship | ship | ship |
| `shortcut_run` | ACT | ship | — (unavailable) | — | — |
| `automation` | ACT | osascript + блоклист | PowerShell + блоклист | defer | defer |
| `clipboard_read/write` | ACT | ship | ship | ship (xclip) | ship (wl-clipboard) |

Инварианты: shell и произвольные файлы отсутствуют по построению;
`automation` всегда ACT + блоклист (Terminal/Keychain на macOS; аналог для
PowerShell — профили, реестр-редактирование системных ключей и запуск
консолей); fail-fast ≤8 с сохранён.

## 6. Юг: контракт bridge-API робота (реализуется в репо робота — CO-4)

Мост потребляет; робот предоставляет. Минимальный контракт v1:

```
GET  /bridge/status   → { name, version, build, orchestrator, uptime_s, profiles }
GET  /bridge/events   → SSE: проактивные события (task_done, alert, update_*)
POST /bridge/update   → триггер собственного апдейт-механизма робота
POST /bridge/pair     → выдача/подтверждение кода пейринга «уже работающего» робота
```

**Пейринг (мост-сторона реализована, T-WIZARD 2026-08-29; робот вызывает):**

```
POST {bridge_url}/pair   (bridge_url и token — из robot-pairing.json на FAT)
     {token, name, base_url?, chat_url?, chat_key?}
→ 200 {robot_token, mcp_url}     # постоянный ключ + куда ходить за MCP
→ 403                            # токен неверен или уже погашен (одноразов)
```

Робот сообщает СВОИ адреса (`base_url` bridge-API, `chat_url` Hermes) в
этом же вызове — мост перенастраивает юг на лету. Провижининг-скрипт
(генерируется визардом, `wizard.provision_script`) складывает ответ в
`/var/lib/robot-bridge-credentials.json` (0600) и шредит одноразовый токен.

- Транспорт: HTTP в tailnet (роботу нужен бинд/прокси за пределы loopback —
  решение на стороне робота, зафиксировано как требование контракта).
- Чат остаётся отдельным каналом: OpenAI-совместимый эндпоинт Hermes как
  есть (не оборачиваем — мозг остаётся у робота, vision §5.5).
- Деградация: любой недоступный эндпоинт → честные состояния SCN-007/009;
  мост никогда не выдумывает статус.

## 7. Панель и PWA

- Один UI на все поверхности: Starlette отдаёт статику + JSON/SSE.
  Self-contained (без CDN — канон машины).
- PWA: manifest (standalone — обязательное условие пушей iOS), service
  worker с офлайн-страницей, различающей «нет VPN» и «мост не отвечает»
  (SCN-019).
- Live-обновления: один SSE-канал `/events` (статус, лента, консент,
  пауза); панель не поллит.
- Доступность: полная клавиатурная проходимость панели (foundation,
  Product mechanics).

## 8. Визард (онбординг новой Pi)

Механика по research B, весь путь FAT-only (Windows-совместимо):

1. Скачивание стокового Raspberry Pi OS 64-bit (кэш + докачка).
2. Запись на SD (elevation: authopen/UAC/pkexec — объяснённая заранее).
3. FAT-раздел: `firstrun.sh` + правка `cmdline.txt` (Bookworm) ИЛИ
   cloud-init `user-data`/`network-config` (Trixie); внутри — hostname,
   аккаунт, NM-профиль Wi-Fi, установка юнита
   `robot-provision.service` (`After=network-online.target`): клон репо
   робота → инсталлер → апдейт-таймер → предъявление пейринг-токена мосту.
4. `robot-pairing.token` — отдельный FAT-файл; скрипт переносит в 0600 и
   удаляет с FAT.
5. Ожидание робота: mDNS + tailnet-поиск; порог 15 мин → диагностика
   (SCN-016); чеклист из четырёх проверок, последняя — тестовый ACT
   (SCN-015).

Опция продвинутым: собственный `os_list.json` для официального Imager 2.x
(`--repo`) — вторичный путь, визард остаётся основным.

## 9. Упаковка и дистрибуция (per research D)

| ОС | Пакет | Подпись | Автообновление |
|---|---|---|---|
| macOS | Briefcase → DMG (.app, LSUIElement, entitlements: Screen Recording/Accessibility/AppleEvents) | Developer ID + нотарификация (Briefcase умеет) | Sparkle 2 либо Ollama-паттерн |
| Windows | PyInstaller + инсталлер | MS Trusted Signing | проверка + перезапуск инсталлера |
| Linux | PyInstaller → .deb + AppImage | — | self-upgrade только для AppImage/tarball (Syncthing-правило) |

Dev-режим остаётся: `uv run python -m vibebridge.app`. Уведомления macOS —
только из подписанного бандла (desktop-notifier), dev-fallback osascript.

## 10. Отказные режимы

| Состояние | Детект | Поверхность | Сценарий |
|---|---|---|---|
| Робот офлайн | таймауты юга + отсутствие SSE | «недоступен с HH:MM», чат/апдейт disabled с причиной | SCN-007 |
| Tailnet down (телефон) | SW-fetch fail | офлайн-страница «включён ли VPN» | SCN-019 |
| Мост на паузе | локальный флаг | баннер/иконки на всех поверхностях; отказ роботу | SCN-005 |
| Нет прав (TCC/portal) | probe/вызов | needs-permission + кнопка выдачи; честный отказ роботу | SCN-018/020 |
| Пуш не доставлен | нет ack | ничего не ломается: действует таймаут-дефолт | SCN-004 |
| Порт занят | bind error | выбрать свободный, панель работает | SCN-017 |
| Ошибка записи SD | I/O | причина + повтор с сохранённым вводом | SCN-014 |
| Апдейт-молчание робота | порог после /bridge/update | текст об авто-rollback робота (R1.51) | SCN-012 |

## 11. Инварианты безопасности

1. Нет shell- и файловых инструментов; automation — только ACT + блоклист.
2. Токены и секреты не логируются; state-файлы 0600; аудит-лог 0600.
3. Панель и MCP доступны только из loopback/tailnet; Funnel запрещён.
4. Пауза сильнее всего, READ включая; полу-пауз не существует (flows FLW-04).
5. Решения согласия принимает только ConsentEngine; поверхности — вьюхи.
6. Пейринг-токены одноразовы и самоудаляемы с FAT.
7. Wire-парность: `mcp` бампается только с `HERMES_VERSION` (retro §1).
8. DNS-rebinding-защита включена с явными allowed_hosts (замена M4-обхода).
9. Мозг не в мосте: никакие LLM-вызовы из bridge не делаются (vision §5.5).

## 12. Открытое → carry-over

- CO-4: контракт §6 реализуется в репо робота —
  `~/DATA/microcontrollers/robot-vibecoder` (remote
  `ssheleg/rpi-ai-assistant`; НЕ путать с `rp-assistant` — тот под
  Pi Zero 2 W) — плюс бинд за loopback.
- Снятие `mac_*`-алиасов — вместе с бампом Hermes (новая CO-строка).
- Мульти-мост (несколько компьютеров одного владельца) — за пределами v1;
  честная заметка в настройках (SCN-006 alt).
- Класс SENSITIVE-READ (календарь и т.п.) — обсуждение при первом таком
  инструменте; в v1 таких нет.
