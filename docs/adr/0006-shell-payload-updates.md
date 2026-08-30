# ADR-0006: macOS-дистрибуция — подписанная оболочка + обновляемый payload

- **Статус:** accepted (оператор, грилл G-2/G-3/G-4, 2026-08-30)
- **Заменяет:** «Автообновление» в `docs/spec/packaging.md` (там были записаны
  два кандидата без выбора).

## Контекст

Мост должен стать обычным приложением владельца: `.app`, подписанный Developer
ID, с автозапуском и автообновлением. Способности моста —
`screencapture`/`osascript`/`pbcopy` (`capabilities.py:100-182`), то есть
TCC-права (Screen Recording, Accessibility, Apple Events) выдаются
**бандлу**. Цена ошибки в дизайне обновления — владелец перевыдаёт права
руками после каждого апдейта.

**Факт, который снял ложную посылку.** Первая формулировка выбора звучала как
«Sparkle сбрасывает TCC-грант». Это не подтвердилось: TCC записывает
*designated requirement* приложения — `identifier <bundle-id>` + цепочка
Developer ID + Team ID, **без cdhash**
([TN3127](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements)).
Полная замена бандла с тем же bundle ID и Team ID грант сохраняет — TN3127
разбирает ровно этот сценарий (v1.2 → v1.3). Решение осталось прежним, но
основание теперь другое и честное.

## Решение

**Оболочка и payload разделены по признаку «Mach-O или нет».**

| | Живёт | Содержит | Обновляется |
|---|---|---|---|
| Оболочка | `/Applications/vibe-bridge.app` (подписана, нотаризована) | Python-рантайм, ВСЕ сторонние зависимости (`mcp`, `pywebpush`, `cryptography`, `rumps`, PyObjC), бутстрап, публичный Ed25519-ключ, seed-копия payload | редко: релиз DMG + переподпись + нотаризация |
| Payload | `~/Library/Application Support/vibe-bridge/payload/<версия>/` | только `vibebridge/*.py` — наш код, чистый Python | часто: скачивание + проверка подписи, без переподписи |

Граница проведена так не для красоты: Hardened Runtime включает library
validation, которая «prevents a program from loading frameworks, plug-ins, or
libraries unless they're either signed by Apple or signed with the same Team
ID»
([disable-library-validation](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.cs.disable-library-validation)).
`.py`-файлы — не Mach-O, их загрузка под это не подпадает; внешние `.so`
(`pydantic_core`, PyObjC, `cffi` — 8+ штук в этом venv) подпадают и потребовали
бы ослабляющего entitlement, после которого «Gatekeeper runs extra security
checks». **Держа все `.so` внутри подписанного бандла, мы обходимся без него.**

**Следствие, принятое сознательно:** бамп зависимости (в том числе `mcp` —
wire-парность с роботом, standing instruction №1) требует релиза оболочки, не
payload-апдейта. Это редкое и ADR-гейтимое событие; наш собственный код
обновляется свободно.

**Проверка payload — Ed25519 до распаковки.** Payload исполняется с TCC-правами
оболочки, поэтому канал доставки не может быть единственной гарантией: sha256,
лежащий рядом с артефактом, подделывается тем же, кто подделал артефакт.
Приватный ключ — в keychain машины релиза, публичный — внутри подписанного
бандла. `cryptography` 50.0.1 уже есть транзитивно (`pywebpush` → `http-ece`),
новой зависимости не вводим.

**Применение — на следующем запуске, не на лету.** Скачанная версия становится
активной при старте: вырывать код из-под работающего действия робота нельзя.
Панель говорит правду о том, что версия скачана и ждёт перезапуска.

**Откат — свойство раскладки, а не отдельный механизм.** Версии лежат
каталогами рядом; бутстрап перед запуском новой версии ставит маркер и снимает
его после успешного старта. Маркер, доживший до следующего старта, означает
«эта версия не поднялась» → откат на предыдущую с записью в журнал.

**Автозапуск — `SMAppService` (macOS 13+)**, `loginItem`: владелец видит и
выключает мост в System Settings → General → Login Items
([SMAppService](https://developer.apple.com/documentation/servicemanagement/smappservice)).
Dev-LaunchAgent остаётся ручным путём разработки и получает гвард от второго
экземпляра на порту 48620.

## Инструмент сборки

Briefcase строит оболочку (stub-бинарь, Python.framework, зависимости) —
переписывать это руками значит воспроизводить чужую отлаженную работу.
**Но два его дефолта отключаются явно:** Briefcase выдаёт каждому macOS-приложению
`com.apple.security.cs.allow-unsigned-executable-memory` и
`com.apple.security.cs.disable-library-validation`
([briefcase macOS docs](https://briefcase.beeware.org/en/stable/reference/platforms/macOS/app.html)).
Нам не нужен ни один: весь исполняемый код — внутри бандла и подписан нами.
Оставить их — значит отдать Gatekeeper-усиление и получить «extra security
checks» без причины.

## Последствия

- (+) Обновление нашего кода не трогает подпись, нотаризацию и TCC-гранты.
- (+) Обновление занимает секунды, а не раунд нотаризации у Apple.
- (+) Откат мгновенный и не требует сети.
- (−) Две дорожки релиза (оболочка / payload) — обе описаны в
  `docs/spec/packaging.md` и обе скриптованы.
- (−) Приватный Ed25519-ключ существует в одном экземпляре — процедура
  бэкапа/ротации заведена строкой в бэклоге.
