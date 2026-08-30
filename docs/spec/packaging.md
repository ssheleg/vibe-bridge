# Упаковка и дистрибуция — runbook (research-notes §D)

Статус: **не выполнено на этой машине** — требует самих ОС и подписных
сертификатов (board B-4). Код к упаковке готов: единое ядро (Python),
трей-абстракция (`vibebridge/tray.py`: rumps на macOS, pystray на
Win/Linux), extras `[macos]`/`[windows]`/`[linux]`. Ниже — воспроизводимая
последовательность на каждый OS, чтобы шаг был исполним без повторного
исследования.

## macOS — Briefcase → подписанный+нотаризованный .app/DMG

```bash
uv pip install briefcase
briefcase create macOS && briefcase build macOS
# entitlements в pyproject [tool.briefcase]: LSUIElement=true (трей без дока),
# Screen Recording / Accessibility / AppleEvents для TCC-READ
briefcase package macOS --identity "Developer ID Application: <TEAM>"
# нотарификация — Briefcase гоняет notarytool; нужен App-Specific Password
```

Первый запуск подписанного .app поднимает системные диалоги TCC — после
выдачи `screenshot`/`list_apps` (System Events) переходят из
`needs-permission` в `available` без правок кода (probe перечитывает при
старте). desktop-notifier требует подписанный бандл для
UNUserNotificationCenter — до подписи работает osascript-fallback.

## Windows — PyInstaller + инсталлер, подпись MS Trusted Signing

```powershell
uv pip install pyinstaller "vibe-bridge[windows]"
pyinstaller --onedir --windowed --name vibe-bridge -m vibebridge.app
# инсталлер: Inno Setup (autostart в реестре Run как приложение ПОЛЬЗОВАТЕЛЯ,
#   не служба — Session-0 не видит десктоп, скриншот вернёт honest error)
# подпись: azuresigntool через MS Trusted Signing (Basic 5000/мес)
```

## Linux — PyInstaller → .deb + AppImage

```bash
uv pip install pyinstaller "vibe-bridge[linux]"
pyinstaller --onedir --name vibe-bridge -m vibebridge.app
# .deb: systemd --user unit (не system — трей нужна сессия пользователя)
# AppImage: self-upgrade включён; для .deb — выключен (правило Syncthing)
# GNOME Wayland: трей требует расширение AppIndicator (extension 615) —
#   инсталлер печатает подсказку, не тянет молча
```

## Автообновление

- macOS: Sparkle 2 (EdDSA) поверх подписанного бандла, ЛИБО
  Ollama-паттерн — стабильная подписанная оболочка + самообновляемый
  Python-payload под ней (не трогает TCC-грант).
- Win: проверка версии + перезапуск инсталлера.
- Linux: self-upgrade только для AppImage/tarball, выключен для .deb.

## Что проверить на живой ОС (закрывает B-1/B-4)

1. `python -m vibebridge.app` поднимает трей (pystray) и панель.
2. Карта способностей отдаёт правильные статусы: Windows — все `available`
   при установленных extras; GNOME Wayland — `list_apps`/`frontmost`
   `unavailable` с причиной, скриншот `unavailable` без grim.
3. Робот через мост зовёт `screenshot`/`clipboard_write` → исполняется или
   отвечает честной причиной (не виснет).
4. Подпись/нотарификация проходят; первый запуск на чистой машине не
   ловит Gatekeeper/SmartScreen-блок.
