<!-- Managed with super-ux (ux-contract v4). The design map: every screen and state with its Figma frame, wireframe, code coverage, and resources. Update in the same change as any interface change; when Figma is enabled, update the frame too. -->

# vibe-bridge — Screens

## Index

| ID | Screen | Used by | Figma | Status | Coverage |
|----|--------|---------|-------|--------|----------|
| SCR-01 | Трей | FLW-02, FLW-03, FLW-04 | — | designed | none yet |
| SCR-02 | Панель · Дашборд | FLW-01, FLW-03, FLW-04, FLW-05 | — | designed | none yet |
| SCR-03 | Панель · Чат | FLW-01, FLW-03 | — | designed | none yet |
| SCR-04 | Панель · Журнал | FLW-02, FLW-03 | — | designed | none yet |
| SCR-05 | Запрос согласия | FLW-02 | — | designed | none yet |
| SCR-06 | Визард подключения | FLW-01 | — | designed | none yet |
| SCR-07 | Пейринг | FLW-01 | — | designed | none yet |
| SCR-08 | Панель · Настройки | FLW-02 | — | designed | none yet |
| SCR-09 | PWA-оболочка | FLW-03, FLW-04 | — | designed | none yet |

## Design system

- **Style pack:** workbench (SHELEG Workbench — продуктовый UI-пак;
  референс-кит `~/ds-workbench`); визуальный слой — `docs/design/ui.md`
- **Figma library:** none (Figma disabled — foundation.md, Design tooling)
- **Tokens in code:** `web/src/theme/tokens.css` (план build-этапа; канон
  значений до тех пор — docs/design/ui.md)
- **Component source:** `web/src/components/` (план build-этапа)
- **Assets:** `web/src/assets/`

## Web surfaces

- **Web surfaces:** no *(панель живёт за tailnet; публичных страниц,
  видимых краулеру или логаут-читателю, у продукта нет)*

## Screens

### SCR-01: Трей
- **Used by:** FLW-02, FLW-03, FLW-04
- **Purpose:** амбиентный статус и вход в панель; пауза одним кликом
- **Elements:** иконка состояния; меню: статус робота (строка), «Открыть
  панель» **(primary)**, переключатель паузы, индикатор гранта с остатком,
  «Отозвать разрешения», последние 3 записи журнала, «Выход»
- **States:**
  | State | Trigger | Figma frame | Behavior |
  |-------|---------|-------------|----------|
  | active | мост работает, робот в сети | — | обычная иконка |
  | paused | пауза включена | — | иконка паузы; пункт меню «Снять паузу» |
  | grant-active | активен грант класса | — | бейдж на иконке; пункт с остатком времени |
  | robot-offline | робот недоступен | — | приглушённая иконка; строка «недоступен с HH:MM» |
  | attention | ждёт решение согласия | — | акцент-иконка; клик ведёт к диалогу |
- **Wireframe:** wireframes/SCR-01.md (optional)
- **Coverage:** none yet
- **Scenarios:** SCN-003, SCN-005, SCN-006, SCN-007
- **Resources:** rumps (macOS) / pystray (Win/Linux); нативные меню ОС
- **Status:** designed

### SCR-02: Панель · Дашборд
- **Used by:** FLW-01 (шаг «не спарен»), FLW-03, FLW-04, FLW-05
- **Purpose:** один взгляд: жив ли робот, что делал, что просил
- **Elements:** карточка робота (имя, статус, версия+билд, оркестратор,
  аптайм, последний контакт, кнопка «Обновить робота»); лента событий;
  баннер паузы; вкладки (Дашборд/Чат/Журнал/Настройки); переключатель паузы
  **(primary: открыть/прочитать состояние — лента)**
- **States:**
  | State | Trigger | Figma frame | Behavior |
  |-------|---------|-------------|----------|
  | loading | открытие панели | — | скелетон карточки ≤3 с, затем честная ошибка |
  | success | робот в сети | — | карточка + живая лента |
  | empty | пейринга ещё не было | — | приветствие + CTA «Подключить робота» |
  | robot-offline | контакт потерян | — | «недоступен с HH:MM», что проверить; чат/апдейт отключены с причиной |
  | paused | мост на паузе | — | баннер паузы поверх любого статуса |
  | updating | апдейт запущен | — | прогресс «обновляется…», карточка блокирована |
- **Wireframe:** wireframes/SCR-02.md
- **Coverage:** none yet
- **Scenarios:** SCN-006, SCN-007, SCN-012, SCN-017
- **Resources:** статус-API робота (CO-4); SSE-лента моста
- **Status:** designed

### SCR-03: Панель · Чат
- **Used by:** FLW-01 (финал), FLW-03
- **Purpose:** разговор с мозгом робота без пересадки в мессенджер
- **Elements:** лента сообщений; поле ввода **(primary: отправить)**;
  индикатор «думает»; пометки доставки
- **States:**
  | State | Trigger | Figma frame | Behavior |
  |-------|---------|-------------|----------|
  | empty | нет истории сессии | — | подсказка «скажите роботу привет» |
  | thinking | ответ генерируется | — | индикатор; ввод доступен |
  | success | ответ пришёл | — | стриминг в ленту |
  | undelivered | робот недоступен при отправке | — | пометка «не доставлено» + повтор |
  | disabled | робот офлайн | — | ввод отключён с причиной |
  | slow | молчит 150 с | — | «думает дольше обычного — ответ придёт событием» + retry |
- **Wireframe:** wireframes/SCR-03.md
- **Coverage:** none yet
- **Scenarios:** SCN-008, SCN-009
- **Resources:** Hermes gateway HTTP (bearer), таймаут-контракт 150 с
- **Status:** designed

### SCR-04: Панель · Журнал
- **Used by:** FLW-02, FLW-03
- **Purpose:** доверие через видимость: каждый вызов — исполненный и отклонённый
- **Elements:** лента записей (время, строка действия, класс, решение,
  результат); фильтры «все / отказы / ACT / READ» **(primary: чтение ленты)**;
  пагинация
- **States:**
  | State | Trigger | Figma frame | Behavior |
  |-------|---------|-------------|----------|
  | empty | записей нет | — | «робот ещё ничего не просил» |
  | loading | подгрузка истории | — | скелетон строк |
  | success | записи есть | — | лента, новые сверху |
  | filtered | включён фильтр | — | сужение + счётчик, сброс в один клик |
  | error | файл журнала недоступен | — | «журнал недоступен: причина», панель живёт дальше |
- **Wireframe:** wireframes/SCR-04.md
- **Coverage:** none yet
- **Scenarios:** SCN-011, SCN-002
- **Resources:** локальный аудит-лог (audit.py), ротация по размеру
- **Status:** designed

### SCR-05: Запрос согласия
- **Used by:** FLW-02
- **Purpose:** одно решение владельца по одному действию робота — там, где владелец сейчас
- **Elements:** человеческая строка действия («Робот хочет …»); кнопки
  Allow **(primary)** / Allow 15 min / Deny; таймер-остаток; имя робота
- **States:**
  | State | Trigger | Figma frame | Behavior |
  |-------|---------|-------------|----------|
  | desktop-dialog | ACT за компьютером | — | нативный диалог поверх работы, таймер 60 с |
  | phone-page | тап по пушу | — | та же тройка кнопок в PWA; остаток таймера |
  | resolved-elsewhere | решение принято на другой поверхности | — | «решено с телефона/компьютера», кнопки скрыты |
  | expired | таймаут до решения | — | «запрос истёк — действие не выполнено», без кнопок |
- **Wireframe:** wireframes/SCR-05.md
- **Coverage:** none yet
- **Scenarios:** SCN-001, SCN-002, SCN-003, SCN-004
- **Resources:** нативные диалоги (rumps NSAlert / нативный аналог per-OS);
  Web Push (VAPID); Android — кнопки на уведомлении, iOS — тап обязателен
- **Status:** designed

### SCR-06: Визард подключения
- **Used by:** FLW-01
- **Purpose:** от «у меня Pi и карта» до «карта в роботе» без терминала
- **Elements:** шаги с прогрессом; выбор диска (системный — исключён);
  форма Wi-Fi (SSID, пароль) и имени робота; кнопка «Записать» **(primary)**;
  объяснение прав администратора; экран «вставьте карту»
- **States:**
  | State | Trigger | Figma frame | Behavior |
  |-------|---------|-------------|----------|
  | intro | вход в визард | — | путь целиком, выбор «новая Pi / уже работает» |
  | sd-select | карта вставлена/нет | — | список съёмных носителей, системные диски невыбираемы |
  | wifi-form | карта выбрана | — | валидация SSID/пароля, имя робота |
  | writing | запись идёт | — | прогресс скачивания+записи; отмена |
  | insert-card | запись завершена | — | физическая инструкция + переход к ожиданию |
  | error | сбой записи/прав | — | причина человеческим языком + «Повторить», ввод сохранён |
- **Wireframe:** wireframes/SCR-06.md
- **Coverage:** none yet
- **Scenarios:** SCN-013, SCN-014
- **Resources:** механика Imager (firstrun.sh/cmdline.txt — Bookworm;
  cloud-init — Trixie); пейринг-токен отдельным FAT-файлом с self-delete
- **Status:** designed

### SCR-07: Пейринг
- **Used by:** FLW-01
- **Purpose:** связка проверяется фактами: найден → связан → проверен → согласие работает
- **Elements:** живой прогресс ожидания; карточка «робот найден»;
  подтверждение **(primary)**; чеклист 4 пунктов; диагностика; ввод кода
  (путь «робот уже работает»)
- **States:**
  | State | Trigger | Figma frame | Behavior |
  |-------|---------|-------------|----------|
  | waiting | после записи карты | — | «робот устанавливается ~N минут», живой индикатор |
  | found | робот предъявил токен | — | имя робота + кнопка подтверждения |
  | code-entry | путь «уже работает» | — | короткий код от робота (голос/Telegram) |
  | checklist | подтверждено | — | 4 проверки бегут по очереди |
  | green | все проверки прошли | — | CTA «скажите роботу привет» → Чат |
  | diagnostic | таймаут ожидания | — | что проверить; «ждать» / «начать заново» |
  | failed | пункт чеклиста красный | — | пункт, причина, действие, перезапуск пункта |
- **Wireframe:** wireframes/SCR-07.md
- **Coverage:** none yet
- **Scenarios:** SCN-015, SCN-016
- **Resources:** mDNS/tailnet-поиск; одноразовый токен → постоянные ключи;
  тестовый ACT как последняя проверка
- **Status:** designed

### SCR-08: Панель · Настройки
- **Used by:** FLW-02
- **Purpose:** карта способностей этого компьютера и границы доступа
- **Elements:** карта способностей (доступно / требует прав / недоступно —
  с причинами); кнопка «Выдать права» **(primary в контексте ряда)**;
  уведомления (вкл/выкл + состояние системного разрешения); автозапуск;
  устройства и ключи (робот, телефоны-подписчики; отзыв); отзыв грантов
- **States:**
  | State | Trigger | Figma frame | Behavior |
  |-------|---------|-------------|----------|
  | success | открытие | — | карта способностей текущей ОС/сессии |
  | permission-needed | способность ждёт прав | — | ряд с кнопкой в системный диалог |
  | degraded | способность недоступна на платформе | — | причина человеческим языком, без кнопки |
- **Wireframe:** wireframes/SCR-08.md
- **Coverage:** none yet
- **Scenarios:** SCN-018, SCN-020
- **Resources:** probe-регистрация способностей на старте (fail-fast)
- **Status:** designed

### SCR-09: PWA-оболочка
- **Used by:** FLW-03, FLW-04
- **Purpose:** панель в кармане: тот же пульт с телефона через tailnet
- **Elements:** тот же интерфейс панели (SCR-02..04, SCR-08) + офлайн-экраны
  оболочки; подписка на пуши **(primary при первом входе)**
- **States:**
  | State | Trigger | Figma frame | Behavior |
  |-------|---------|-------------|----------|
  | online | tailnet доступен | — | рендерит панель как есть |
  | offline-no-vpn | нет связи с мостом | — | «Нет связи с домом — включён ли Tailscale?» |
  | offline-bridge-down | tailnet есть, мост не отвечает | — | «Компьютер недоступен» — отличимо от VPN |
  | push-setup | первый вход | — | объяснение и подписка на пуши (iOS: сначала «на экран Домой») |
- **Wireframe:** wireframes/SCR-09.md
- **Coverage:** none yet
- **Scenarios:** SCN-004, SCN-019
- **Resources:** service worker (офлайн-страница); Web Push; tailscale serve
  (HTTPS-сертификат tailnet-имени)
- **Status:** designed
