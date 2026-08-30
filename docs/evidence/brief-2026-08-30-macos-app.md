# Brief — vibe-bridge · B-4 macOS-нога (.app, подпись, автообновление)

- **Date:** 2026-08-30 · **Model:** Fable 5 · **Run:** build (сегмент 3)
- **Запрос оператора:** «а для макбука когда приложение увижу и как оно
  обновляться будет?» → B-4, macOS-половина.
- **Разблокировано этим раном:** у оператора есть Apple Developer Program и
  сертификаты; `Developer ID Application: Sergei Viktorovich Sheleg
  (KJ35UYYL22)` импортирован в login.keychain (`security find-identity -v -p
  codesigning` → 1 valid identity, годен до 2027-02-01).

## Source ledger (harvest до грилла)

| Источник | Что дал |
|---|---|
| `docs/spec/packaging.md` | runbook macOS: Briefcase → sign → notarize; два кандидата автообновления (Sparkle 2 / Ollama-паттерн) |
| `docs/adr/0002`, `0005` | режимы standalone/gateway; имя vibe-bridge |
| `docs/ux/scenarios.md` (ST-011, SCN-018) | платформенная стори — мост как обычное приложение владельца |
| `docs/evidence/retro.md` | 2 standing instructions: wire-парность (`mcp` этим раном НЕ бампается — соблюдено), no-SSH (не затрагивается) |
| `docs/evidence/backlog.md` | 7 открытых строк; B-4 = «runbook ready» |
| `docs/evidence/verification.md` | 8 строк, 0 в `never` |
| `vibebridge/app.py`, `capabilities.py`, `launchd/*.plist` | трей владеет main thread; способности = subprocess-обёртки над `screencapture`/`osascript`/`pbcopy` → TCC нужен родителю-.app; автостарт сейчас = dev-LaunchAgent на `scripts/run.sh` |
| obsidian-wiki | страница проекта лежит под СТАРЫМ именем `projects/mac-bridge` → консолидация на стадии 9 |
| graphify | граф не построен (бинарь есть) — harvest шёл grep'ом |
| Машина | notarytool-креды отсутствуют (`security find-generic-password -s com.apple.gke.notary.tool` → нет профиля); **`git remote -v` пуст — репо только на этом маке** |

**Базовая линия рана:** 103 теста зелёные, `ruff check` clean (2026-08-30, до правок).

## Записанные решения грилла

| # | Решение | Основание |
|---|---|---|
| G-1 | **Канал релизов — публичный GitHub `ssheleg/vibe-bridge` + Releases.** Создание репо и пуш авторизованы оператором явно | приватный канал заставил бы апдейтер носить GitHub-токен на каждой машине; заодно закрывает «репо без бэкапа» |
| G-2 | **Автообновление — оболочка+payload (Ollama-паттерн)**, не Sparkle | TCC-грант ключуется на неизменной подписанной оболочке; payload обновляется без переподписи и без потери прав |
| G-3 | **Payload проверяется Ed25519-подписью релиза**; приватный ключ — в keychain этой машины (НЕ в репо), публичный вшит в оболочку | payload исполняется с TCC-правами оболочки; sha256 рядом с артефактом не защищает от компрометации канала |
| G-4 | **Автостарт — `SMAppService` Login Item от .app**; dev-LaunchAgent выгружается и остаётся ручным dev-путём с гвардом от двойного запуска (занятый порт → честная ошибка и выход) | два экземпляра дерутся за 48620; штатный тумблер в System Settings → Login Items |
| G-5 | **Поверхность обновления — тихо + строка в настройках панели** («Версия X · проверить сейчас») + строка в журнале на каждое обновление | журнал моста обещает «всё, что мост делает, — видно» |
| G-6 | **Нотаризация — в конце рана**, креды оператор даёт одной `!`-командой (`xcrun notarytool store-credentials`) | всё остальное проверяемо без них |

**Autonomy sweep:** тесты `uv run python -m pytest tests/ -q`; линт `ruff check`;
ветка `master` (пуш в новый remote авторизован G-1); трекер —
`docs/evidence/backlog.md`; логи — `/tmp/vibe-bridge.{err,out}`, журнал моста в
`~/Library/Application Support/vibe-bridge/`. UX-трек: поверхность одна и
текстовая (строка версии) — super-ux применяется точечно (SCN на обновление),
sheleg-design не арминуется (новых визуальных поверхностей нет). Ран
`ungated` — agent-sync в проекте не включён (`.claude/agent-sync.json` нет).

## REQ

| REQ | Требование | Verified by |
|---|---|---|
| REQ-01 | `.app` собирается воспроизводимо одной командой из репо; трей без иконки в доке (`LSUIElement`), Info.plist несёт usage-строки для TCC | скрипт сборки в репо; `plutil -p` показывает ключи; `.app` стартует |
| REQ-02 | Бандл подписан Developer ID с hardened runtime + `--timestamp`, нужные entitlements | `codesign --verify --strict --verbose=2` ok; `codesign -d --entitlements` показывает набор |
| REQ-03 | Код моста живёт ВНЕ бандла (payload-каталог версий), оболочка его находит и запускает; откат = удаление каталога версии | тест резолвера версий; live-запуск .app с payload |
| REQ-04 | Апдейтер: проверка фида GitHub Releases, скачивание, **Ed25519-проверка до распаковки**, атомарная установка версии, откат при сбое | юнит-тесты (валидная/битая/чужая подпись, обрыв, откат); `never-raises` на сетевом слое |
| REQ-05 | TCC-грант переживает обновление payload (подпись оболочки не меняется) | live: выдать Screen Recording → обновить payload → `screenshot` работает без повторного запроса |
| REQ-06 | Автостарт через `SMAppService`; dev-LaunchAgent выгружен; двойной запуск невозможен молча | live: тумблер виден в Login Items; тест гварда порта |
| REQ-07 | Панель: строка версии + «проверить сейчас», строка журнала на обновление; сценарий в `docs/ux/scenarios.md` в том же изменении | тест API; браузерная проверка (chrome-devtools) |
| REQ-08 | DMG собирается и открывается; `spctl -a -vvv -t exec` даёт honest-вердикт до и после нотаризации | вывод `spctl` в verification-строке |
| REQ-09 | Репо запушен в `ssheleg/vibe-bridge`; релиз несёт DMG + payload + подпись; сборка релиза — скрипт, не ручные шаги | `gh release view`; скачивание артефакта апдейтером живьём |
| REQ-10 | Нотаризация + staple пройдены; чистый Gatekeeper-вердикт | `notarytool submit --wait` accepted; `stapler validate`; `spctl` → accepted |
| REQ-11 | `docs/spec/packaging.md` приведён в правду (что исполнено, чем заменён Briefcase-шаг, если заменён); README — как ставить и как обновляется | диффы доков в том же изменении |

Заморожено: добавлять можно, убирать — только оператору.

## Carry-over

| # | Строка | Статус |
|---|---|---|
| CO-1 | Windows/Linux упаковка и подпись (вторая половина B-4) | остаётся в B-4 |
| CO-2 | Wiki-страница под старым именем `projects/mac-bridge` → консолидировать в `projects/vibe-bridge` | стадия 9 |
| CO-3 | Код-граф не построен — `/graphify .` | стадия 9 |
| CO-4 | Приватный Ed25519-ключ релиза существует в одном экземпляре (keychain этой машины) — нужна процедура бэкапа/ротации | open |
