# Research notes — v2 design run (2026-08-29)

Конденсат пяти исследовательских отчётов, на которые ссылается
`architecture.md`. Каждое утверждение несёт рецепт (file:line установленного
SDK или URL). Полные отчёты — в транскрипте рана; сюда вынесено то, что
спека залочила.

## A. MCP SDK 1.26.0 — монтирование, auth, клиент

- `FastMCP.streamable_http_app()` возвращает Starlette-приложение; для
  монтирования во внешний ASGI обязателен внешний lifespan
  `session_manager.run()` (иначе `RuntimeError` — streamable_http_manager.py:144).
  Официальный паттерн — README SDK (METADATA:1428–1470).
- Путь эндпоинта — `settings.streamable_http_path`, по умолчанию `/mcp`
  (server.py:166).
- **Static-bearer поверх Mount — поддерживаемый путь**: без
  `token_verifier` SDK не ставит своих middleware (server.py:970,1017–1024),
  внешняя ASGI-проверка `Authorization: Bearer` ни с чем не конфликтует.
  Встроенный OAuth-путь (`TokenVerifier` + `AuthSettings`) требует
  issuer/resource URL (auth/settings.py:15–29) — для статического токена
  избыточен.
- `TransportSecuritySettings(enable_dns_rebinding_protection=True,
  allowed_hosts=["<ip>:<port>", "host.tailnet.ts.net:<port>", "100.x:*"])` —
  корректная защита при бинде на tailnet (transport_security.py:19–34,
  45–127; 421 при чужом Host). Автовключение защиты — только для
  loopback-хостов (server.py:177–183).
- Клиент: `streamable_http_client(url, http_client=httpx.AsyncClient(
  headers={"Authorization": "Bearer …"}))` (client/streamable_http.py:600–621);
  старый `streamablehttp_client` — deprecated.

## B. Raspberry Pi — провижининг без SSH

- Bookworm-образы кастомизируются механизмом **`firstrun.sh` +
  `systemd.run=` в `cmdline.txt`** (rpi-imager downloadthread.cpp ~2620);
  скрипт самоудаляется и вычищает cmdline (customization_generator.cpp ~531).
- **cloud-init появился только в Trixie** (образ 2025-11-24,
  `init_format: cloudinit-rpi`, NoCloud `user-data`/`network-config` на FAT):
  raspberrypi.com/news/cloud-init-on-raspberry-pi-os. Imager 1.x молча
  портит кастомизацию Trixie; корректен Imager 2.x
  (rpi-imager/doc/os_customisation_formats.md).
- `wpa_supplicant.conf` на boot-разделе **мёртв на Bookworm** (NetworkManager);
  Wi-Fi — NM-профиль, который пишет firstrun.
- `firstrun.sh` бежит ДО сети (`kernel-command-line.target`) → паттерн:
  firstrun ставит в rootfs unit `After=network-online.target`
  (провижининг-скрипт: клон репо робота, инсталлер, апдейт-таймер) и
  ребутится. FAT-раздел пишется со всех трёх настольных ОС (Windows не
  пишет ext4 — вся кастомизация только через FAT).
- Секрет пейринга — отдельный FAT-файл (напр. `robot-pairing.token`);
  провижининг-скрипт переносит его в 0600 и удаляет с FAT (прецедент:
  firstrun.sh/custom.toml самоудаляются — raspberrypi-sys-mods/firstboot:189–223).
  В cloud-init `user-data` токен не класть — тот не самоудаляется.
- Опция для продвинутых: свой `os_list.json` для официального Imager
  (`rpi-imager --repo`, схема os-list-schema.json; прецедент — uConsole).

## C. Tailscale HTTPS + пуш на телефон

- `tailscale cert`/`serve` выдают Let's Encrypt-сертификат для
  `device.tailnet.ts.net` (нужны MagicDNS + HTTPS-toggle тейлнета; имя
  попадает в CT-лог) — tailscale.com/kb/1153; `serve` терминирует TLS сам
  (kb/1242). Телефон в tailnet получает валидный сертификат без варнингов.
- **Web Push из приватного origin работает**: наш сервер шлёт OUTBOUND
  POST на push-endpoint подписки (инфраструктура APNs/FCM доставляет);
  входящая достижимость нашего сервера не нужна
  (web.dev/articles/push-notifications-web-push-protocol).
- iOS: пуши только для **Home-Screen PWA** (standalone/fullscreen),
  разрешение — по жесту, с iOS 16.4 (webkit.org/blog/13878). Android
  Chrome: любой HTTPS-таб, install не обязателен.
- **Кнопки действий на уведомлении** (Allow/Deny без открытия страницы):
  Android Chrome — да, iOS — нет (MDN Notification/actions «Limited
  availability»; web.dev display-a-notification). iOS-путь: тап → страница
  решения.
- Fallback-канал: Telegram-бот через `getUpdates` long-poll (публичный IP
  не нужен — core.telegram.org/bots/faq), inline-кнопки → `callback_query`.
  ntfy на iOS всё равно ходит через ntfy.sh→APNs (docs.ntfy.sh/config) —
  Telegram чище как второй канал.
- Tailscale iOS ≥1.48: VPN On Demand — туннель поднимается сам; пуш
  доставляется мимо туннеля (APNs), тап открывает tailnet-страницу.

## D. Трей, упаковка, уведомления

- **pystray 0.19.5** (2023, дремлет, но работает): Win32 / AppKit /
  AppIndicator+GTK/X11; `run()` блокирует и на macOS обязан жить в main
  thread; на GNOME нужен extension 615 (AppIndicator support). Радио-кнопок
  и default-action на macOS/AppIndicator нет.
- macOS остаётся за **rumps 0.4.0** (NSAlert-диалоги, LSUIElement);
  абстракция `TrayBackend.run()` — один контракт, два бэкенда. Uvicorn —
  в worker-thread, main thread всегда у трея.
- Упаковка: macOS — **Briefcase** (DMG/PKG, автоматическая нотарификация,
  entitlements в pyproject) или PyInstaller+notarytool; Windows —
  PyInstaller + инсталлер, подпись через MS Trusted Signing (Basic
  5000 подписей/мес); Linux — PyInstaller .deb/AppImage; Flatpak — только с
  `--talk-name=org.kde.StatusNotifierWatcher`.
- Автообновление: Sparkle 2 (macOS) / Ollama-паттерн «стабильная
  подписанная оболочка + самообновляемый payload»; Syncthing-правило —
  self-upgrade выключен для дистро-пакетов.
- Уведомления: **desktop-notifier** (UNUserNotificationCenter/WinRT/DBus,
  asyncio); на macOS работает только из ПОДПИСАННОГО бандла — dev-fallback
  `osascript`.

## E. Паритет способностей Win/Linux (v1)

| Способность | Windows | Linux X11 | Linux Wayland |
|---|---|---|---|
| screenshot | ship — `mss` (не из сервиса: Session-0 isolation) | ship — `mss` | ship через portal `org.freedesktop.portal.Screenshot` (первый раз может спросить); grim — только wlroots; gnome-screenshot сломан на GNOME 49 |
| apps/frontmost | ship — PyGetWindow | ship — xdotool/wmctrl | degrade: KDE — kdotool; GNOME — честная ошибка (без расширения списка окон нет) |
| notify | ship — windows-toasts | ship — notify-send | ship — notify-send |
| open app/url | ship — startfile/Start-Process | ship — xdg-open/gtk-launch | ship |
| automation | ship gated — PowerShell (аналог osascript, тот же ACT+блоклист) | defer | defer (ydotool требует демона+root) |
| clipboard | ship — Get/Set-Clipboard | ship — xclip | ship — wl-clipboard |

Правило: **probe при регистрации, не при вызове** — session type,
`DISPLAY`/`WAYLAND_DISPLAY`, session bus, бинарники, интерактивная сессия
Windows; недоступное регистрируется как unavailable-with-reason и отвечает
мгновенно.
