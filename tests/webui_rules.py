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


def keyframes(name: str) -> dict[str, str]:
    """Все `@keyframes` файла ЦЕЛИКОМ: имя → тело со всеми шагами.

    Прежний разбор резал блок по первой `}` — то есть видел только ПЕРВЫЙ
    шаг. Доказано подсадкой (A-33): `width` в шаге `50%` проходил банлист
    моторной доктрины насквозь, и анимация раскладки уезжала бы в релиз.

    Считаем скобки, а не ищем первую: у `@keyframes` вложенность ровно на
    один уровень глубже, чем предполагал `split`.
    """
    out: dict[str, str] = {}
    code = code_of(name)
    for match in re.finditer(r"@keyframes\s+([\w-]+)\s*\{", code):
        depth, i = 1, match.end()
        while i < len(code) and depth:
            if code[i] == "{":
                depth += 1
            elif code[i] == "}":
                depth -= 1
            i += 1
        out[match.group(1)] = code[match.end():i - 1]
    return out


def attribute_interpolations(name: str) -> list[tuple[int, str]]:
    """Все места вида `атрибут="${…}"` — то есть подстановки В ЗНАЧЕНИЕ
    атрибута, где кавычка закрывает его.

    Строка ищется по исходнику С комментариями намеренно: комментарий,
    показывающий пример, — тоже строка, и лучше объяснить его один раз в
    исключениях, чем пропустить настоящую подстановку.
    """
    out: list[tuple[int, str]] = []
    for i, line in enumerate((WEBUI / name).read_text(encoding="utf-8")
                             .splitlines(), start=1):
        for m in re.finditer(r'[\w-]+="\$\{([^}]*)\}', line):
            out.append((i, m.group(1).strip()))
    return out


def declarations(body: str) -> list[tuple[str, str]]:
    """(свойство, значение) для тела правила — с ТОЧНЫМ именем свойства.

    Существует потому, что подстрочный поиск по телу правила ошибался трижды
    за одну сессию, и всегда одинаково: `color:var(--danger)` содержится в
    `border-color:var(--danger)`, `left` — в `padding-left`, `width` — в
    `max-width`. Проверка «свойство X красит Y» обязана сравнивать ИМЯ
    свойства целиком, а не искать строку.
    """
    out = []
    for chunk in body.split(";"):
        name, sep, value = chunk.partition(":")
        if sep:
            out.append((name.strip().lower(), value.strip()))
    return out


def declared(body: str, prop: str) -> str | None:
    """Значение свойства `prop`, если оно объявлено. Иначе None."""
    for name, value in declarations(body):
        if name == prop:
            return value
    return None
