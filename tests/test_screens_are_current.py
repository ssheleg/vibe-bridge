"""`screens.md` описывает то, что ОТГРУЖЕНО (U-18).

Все девять экранов стояли «Coverage: none yet» при работающих трее, панели,
журнале, карточке согласия, настройках, визарде и PWA. Все девять ссылались
на `wireframes/SCR-0N.md` — каталога нет. Три пути к токенам и компонентам
указывали на `web/src/…`, которого не было никогда.

Линтер пака про это молчит: он предупреждает, когда покрытие нельзя ИЗМЕРИТЬ,
но не падает на честном «none yet». Поэтому правило про этот проект живёт
здесь: у нас построены все девять, и «none yet» — это не состояние, а
забытая строка.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import vibebridge

REPO = Path(vibebridge.__file__).resolve().parents[1]
SCREENS = REPO / "docs" / "ux" / "screens.md"
sys.path.insert(0, str(REPO))

#: Экран → почему он ещё не построен. Пустой словарь — нормальное состояние.
NOT_BUILT_YET: dict[str, str] = {}


def _blocks() -> dict[str, str]:
    text = SCREENS.read_text(encoding="utf-8")
    out, current, buf = {}, None, []
    for line in text.splitlines():
        found = re.match(r"^### (SCR-\d+)", line)
        if found:
            if current:
                out[current] = "\n".join(buf)
            current, buf = found.group(1), []
        elif current:
            buf.append(line)
    if current:
        out[current] = "\n".join(buf)
    return out


def test_every_screen_names_the_code_that_builds_it():
    пустые = []
    for scr, body in _blocks().items():
        cover = next((ln for ln in body.splitlines()
                      if ln.startswith("- **Coverage:**")), "")
        if "none yet" in cover and scr not in NOT_BUILT_YET:
            пустые.append(scr)
    assert not пустые, (
        "экран отгружен, а `screens.md` про это не знает: " +
        ", ".join(пустые) + ". Если экран и правда не построен, впишите его в "
        "NOT_BUILT_YET С ПРИЧИНОЙ")


def _field(body: str, name: str) -> str:
    """Значение ПОЛЯ, а не строка из документа.

    Первая версия обеих проверок ниже читала весь текст — и покраснела на
    поясняющей прозе, которая как раз говорит, что каталога `wireframes/` не
    существует. Проза, прочитанная как код, — пятый случай за эту сессию
    (A-32, A-33, дважды в архитектурном угле и здесь). Для документов ответ
    тот же, что для кода: читать структуру, а не текст.
    """
    for line in body.splitlines():
        if line.startswith(f"- **{name}:**"):
            return line.split(":**", 1)[1]
    return ""


def test_every_cited_file_exists():
    """Ссылка, которая никуда не ведёт, хуже отсутствующей: она выглядит
    проверенной. Три пути к `web/src/…` прожили так весь проект."""
    битые = []
    for scr, body in _blocks().items():
        for name in ("Coverage", "Wireframe"):
            for path in re.findall(r"`([\w.-]+(?:/[\w.-]+)+)`",
                                   _field(body, name)):
                if not (REPO / path.split(":")[0]).exists():
                    битые.append(f"{scr} → {path}")
    assert not битые, f"ссылки в screens.md никуда не ведут: {битые}"


def test_no_screen_points_at_a_wireframe_that_was_never_drawn():
    указывают = [scr for scr, body in _blocks().items()
                 if "wireframes/" in _field(body, "Wireframe")]
    assert not указывают, (
        f"экраны ссылаются на каталог `wireframes/`, которого нет: {указывают}")


def test_an_exemption_carries_a_reason():
    for scr, reason in NOT_BUILT_YET.items():
        assert len(reason) > 30, f"«{scr}» освобождён без объяснения"
