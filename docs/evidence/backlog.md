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
| B-4 | P2 | ~~macOS-нога~~ **ЗАКРЫТА 2026-08-30**: подписанный .app, оболочка+payload, автообновление и автозапуск — живьём. Осталась Windows/Linux-нога: нужны машины и сертификаты | brief-2026-08-29 CO-5 | macOS done, Win/Linux pending |
| B-5 | P3 | `/brand-init` + copywriting панели/визарда | brief-2026-08-29 CO-3 | open |
| B-6 | P3 | Figma-визуальный проход по wireframes | brief-2026-08-29 CO-2 | open |
| B-7 | P3 | Снять `mac_*`-алиасы инструментов — вместе с бампом Hermes/`mcp` | spec §5, close-out 2026-08-29 | open |
| B-10 | P2 | Приватный Ed25519-ключ релизов существует в одном экземпляре (keychain этой машины) — нужна процедура бэкапа и ротации; потеря = невозможность выпускать обновления | brief-2026-08-30 CO-4 | open |
| B-11 | P1 | ~~Нотаризация macOS-сборки~~ **ЗАКРЫТА 2026-08-30**: профиль заведён оператором, `--notarize` прошёл (Accepted), staple на .app и .dmg, `spctl` → accepted; DMG в релизе `shell-v0.1.0` | brief-2026-08-30 REQ-10 | done |
| B-12 | P3 | Лицензия репозитория: сейчас `LicenseRef-Proprietary` (все права сохранены) — решить, открывать ли под MIT, как остальная семья | brief-2026-08-30 | open |
| B-13 | P3 | Иконка приложения — сплошная заливка, сгенерированная stdlib'ом; нужен настоящий значок (`/sheleg-design`) | brief-2026-08-30 | open |
| B-14 | P2 | Побочный приватный репозиторий `sshlg/vibe-bridge` создан `gh repo create` по ошибке; удалить (у токена нет scope `delete_repo` — шаг оператора) | brief-2026-08-30 | human step |
| B-15 | P2 | App-specific password для notarytool засветился в транскрипте сессии — отозвать на appleid.apple.com и, если понадобится, завести профиль заново (сам профиль в keychain останется рабочим до отзыва) | build-3 close-out | human step |
