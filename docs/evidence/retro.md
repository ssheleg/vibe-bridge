# Retro — vibe-bridge

## Standing instructions (bind every run; max 10)

1. **Wire-парность:** `mcp` SDK бампается только вместе с `HERMES_VERSION`
   робота (README «Wire parity»); проверять на каждом build-ране.
2. **Fleet-канон робота — no SSH:** любой дизайн-ход, требующий SSH к Pi,
   сверять с fleet-update-policy робота и решать вслух у оператора
   (решено G1 2026-08-29: SD-бутстрап без SSH).

## Run stamps

| Run | Date | Commit | Outcome |
|---|---|---|---|
| v2-design | 2026-08-29 | см. коммит «v2 design: vibe-bridge…» этой даты | complete — REQ-01..10 закрыты, build не начат (по определению рана) |

## Recent log

### 2026-08-29 · v2-design · vision написан до чтения контракта формата
- **Симптом:** первый вариант `docs/ux/vision.md` — с русскими заголовками
  секций; контракт ux-contract v4 ключует линтер на английские заголовки
  (U030). Обнаружено при чтении `scenario-format.md` ПОСЛЕ написания файла,
  исправлено в том же ране.
- **Стадия проявления:** 3 (spec/UX-трек); **владелец:** стадия 1 (docs
  study — формат-контракт тоже контракт, его читают до письма).
- **Причина:** SKILL.md скила ссылается на контракт отдельным файлом;
  порядок «скил → сразу писать» пропустил referenced-контракт.
- **Фикс (grade: process):** для цепочки docs/ux читать
  `references/scenario-format.md` до первого файла.
- **Чек на будущее:** `docs/ux/lint.py` теперь в репо и ловит U030
  механически; при build-ране — включить его в pre-commit/CI.
