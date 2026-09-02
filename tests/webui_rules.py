"""Чтение CSS-правил из наших страниц — для тестов, которые проверяют КОД.

Причина существования этого файла — A-32. Тест доступности проверял
`"prefers-reduced-motion" in page`, а строка есть в файле дважды: в правиле и
в поясняющем комментарии НАД ним. Подсадка это доказала: удаление настоящего
`@media`-правила оставляло тест зелёным, то есть регрессия доступности прошла
бы насквозь.

Починить один тест мало: тот же слабый ассерт жил во втором, соседнем, и
держался только на том, что в `mascot.js` строка пока встречается один раз.
Один поясняющий комментарий — и он снова ничего не проверяет. Поэтому чтение
правил здесь одно на всех, а не по копии на тест.
"""
from __future__ import annotations

import re
from pathlib import Path

import vibebridge

WEBUI = Path(vibebridge.__file__).parent / "webui"


def code_of(name: str) -> str:
    """Текст страницы БЕЗ комментариев — и блочных, и строчных.

    Комментарий, объясняющий правило, не является правилом. Именно на этом
    различии держится весь этот модуль.
    """
    text = (WEBUI / name).read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def media_blocks(name: str, feature: str) -> list[str]:
    """Тела ВСЕХ `@media`-блоков с этим признаком, комментарии вырезаны.

    Именно всех: 2026-09-02 у страницы появился второй блок
    (таймер-бар согласия), и версия, читавшая только первый, покраснела на
    файле, где ничего не сломалось.
    """
    return re.findall(
        rf"@media\s*\({re.escape(feature)}[^)]*\)\s*\{{(.*?)\}}\s*\}}",
        code_of(name), flags=re.S)


def reduced_motion_kills_animation(name: str) -> bool:
    """Гасит ли страница анимацию по просьбе системы — ПРАВИЛОМ, не словом."""
    blocks = media_blocks(name, "prefers-reduced-motion")
    return bool(blocks) and "animation:none" in "".join(blocks).replace(" ", "")


def rule_body(name: str, selector: str) -> str:
    """Тело одного правила по селектору, пробелы убраны — для точных
    проверок вроде «высота ровно 4px»."""
    code = code_of(name)
    if selector not in code:
        return ""
    return code.split(selector, 1)[1].split("}", 1)[0].replace(" ", "")
