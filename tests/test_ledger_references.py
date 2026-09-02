"""Ссылки в леджерах должны разрешаться — механически, а не на честном слове.

Правило проекта: «claim without proof is not documentation», и имя файла в
доказательстве обязано резолвиться. Оно не было сделано механическим, и
устало ровно так, как устают такие правила: `verification.md` ссылался на
`mascot_window.py`, которого нет уже неделю, и называл тест по старому
имени (A-40).

Проверка различает три вида ссылок, потому что «файла нет» значит три
разные вещи:

  1. НАШ файл — обязан существовать. Не существует → ссылка мертва.
  2. Файл ЧУЖОГО репозитория (робот) — у нас его нет по определению, и
     требовать его наличия значит требовать монорепозитория.
  3. Файл, который создаётся в рантайме или на SD-карте — его нет ни у
     кого, пока продукт не поработал.

Второй и третий перечислены поимённо и с причиной. Список без причин — это
не гейт, а способ его выключить (тот же вывод, что в A-37 и A-38).

Номера строк НЕ проверяются на смысл — только на попадание в файл. Строка
уезжает от любой правки выше, и требовать её точности значило бы завести
правило, которое нарушается каждым коммитом и потому будет отключено.
"""
from __future__ import annotations

import re
from pathlib import Path

import vibebridge

REPO = Path(vibebridge.__file__).resolve().parents[1]
DOCS = sorted((REPO / "docs").rglob("*.md")) + [REPO / "README.md",
                                                REPO / "CLAUDE.md"]
REF = re.compile(
    r"`([A-Za-z0-9_./-]+\.(?:py|md|html|js|sh|toml|json|txt|jsonl))"
    r"(?::(\d+)(?:[-–]\d+)?)?`")
SKIP_DIRS = (".venv", "/build/", "/dist/", "/.git/")

#: Имя → почему этого файла нет в ЭТОМ репозитории.
NOT_OURS = {
    "bridge_api.py": "файл репозитория робота (rpi-ai-assistant)",
    "io.py": "обработчики периферии в репозитории робота "
             "(agent/plugins/peripherals/handlers/io.py)",
    "bridge_pair.py": "файл репозитория робота",
    "dashboard_data.py": "файл репозитория робота",
    "deploy.sh": "файл репозитория робота (его установщик на плате)",
    "pi-deploy.sh": "файл репозитория робота (пушер с дев-машины)",
    "periphery.txt": "requirements репозитория робота",
    "ci_local.sh": "файл репозитория робота",
    "test_bridge_api.py": "тест репозитория робота",
    "mac_bridge.md": "скил в репозитории робота",
    "os_list.json": "каталог образов Raspberry Pi — файл Raspberry Pi Foundation",
    "workbench.md": "стайл-пак sheleg-design, отдельный репозиторий",
    "scenario-format.md": "справочник скила super-ux, отдельный репозиторий",
    "CONTEXT.md": "документ пайплайна, которого этот проект не завёл",
    "mac-bridge.md": "страница вики (obsidian-vault), отдельное хранилище",
    "agent-sync.json": "файл координации агентов; в этом проекте его нет",
    "SCR-0N.md": "шаблон имени экрана, а не файл",
}

#: Имя → почему файла нет, пока продукт не поработал.
BORN_AT_RUNTIME = {
    "config.toml": "создаётся мостом при первом запуске в Application Support",
    "firstrun.sh": "пишется визардом на FAT-раздел карты",
    "cmdline.txt": "лежит на загрузочном разделе Raspberry Pi OS",
    "bridge_credentials.json": "создаётся роботом после пейринга",
}


def _resolve(ref: str) -> Path | None:
    exact = REPO / ref
    if exact.is_file():
        return exact
    name = Path(ref).name
    for path in REPO.rglob(name):
        if path.is_file() and not any(s in str(path) for s in SKIP_DIRS):
            return path
    return None


def _references() -> list[tuple[Path, int, str, str | None]]:
    out = []
    for doc in DOCS:
        if not doc.is_file():
            continue
        for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            for m in REF.finditer(line):
                out.append((doc, i, m.group(1), m.group(2)))
    return out


def test_every_referenced_file_of_ours_exists():
    dead = []
    for doc, lineno, ref, _ in _references():
        name = Path(ref).name
        if name in NOT_OURS or name in BORN_AT_RUNTIME:
            continue
        if _resolve(ref) is None:
            dead.append(f"{doc.relative_to(REPO)}:{lineno} → {ref}")
    assert not dead, (
        "ссылка в документе никуда не ведёт — почините её или объясните в "
        "NOT_OURS / BORN_AT_RUNTIME: " + "; ".join(dead))


def test_a_quoted_line_number_lands_inside_its_file():
    """Смысл строки не проверяем — она уезжает от любой правки выше. Но
    строка ЗА концом файла означает, что ссылка уже не про этот файл."""
    beyond = []
    for doc, lineno, ref, target in _references():
        if target is None:
            continue
        path = _resolve(ref)
        if path is None:
            continue
        total = len(path.read_text(encoding="utf-8",
                                   errors="replace").splitlines())
        if int(target) > total:
            beyond.append(f"{doc.relative_to(REPO)}:{lineno} → "
                          f"{ref}:{target} (в файле {total} строк)")
    assert not beyond, "ссылка на строку за концом файла: " + "; ".join(beyond)


def test_every_exemption_names_a_reason():
    for table in (NOT_OURS, BORN_AT_RUNTIME):
        for name, why in table.items():
            assert len(why) > 20, f"{name}: причина слишком коротка, чтобы быть ей"


def test_the_check_actually_sees_a_dead_reference(tmp_path, monkeypatch):
    """Подсадка внутри проверки: иначе «зелено» значит только, что никто не
    смотрел."""
    import tests.test_ledger_references as mod

    doc = tmp_path / "ledger.md"
    # Имя латиницей намеренно: разбор берёт `[A-Za-z0-9_./-]`, потому
    # что все настоящие пути проекта такие. Кириллица в имени файла
    # осталась бы незамеченной — граница названа, а не спрятана.
    doc.write_text("ссылка на `no_such_file.py:12`\n", encoding="utf-8")
    monkeypatch.setattr(mod, "DOCS", [doc])
    monkeypatch.setattr(mod, "REPO", tmp_path)
    refs = mod._references()
    assert refs and refs[0][2] == "no_such_file.py"
    assert refs[0][3] == "12"
    assert mod._resolve("no_such_file.py") is None
