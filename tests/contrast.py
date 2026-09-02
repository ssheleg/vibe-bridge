"""Контраст по WCAG — ВЫЧИСЛЯЕТСЯ из `tokens.css`, а не запоминается.

Число в тесте — это снимок палитры на день, когда тест писали: поправят
токен, и тест останется зелёным, рассказывая про старый цвет. Поэтому здесь
берутся значения из самого файла и считается формула.
"""
from __future__ import annotations

import re
from pathlib import Path

import vibebridge

TOKENS = Path(vibebridge.__file__).parent / "webui" / "tokens.css"


def palette() -> tuple[dict[str, str], dict[str, str]]:
    """(светлая, тёмная). Тёмная — это светлая, перекрытая блоком `@media`."""
    css = TOKENS.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    head, _, tail = css.partition("prefers-color-scheme: dark")

    def grab(text: str) -> dict[str, str]:
        return {name: value.strip()
                for name, value in re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})",
                                              text)}
    light = grab(head)
    dark = {**light, **grab(tail.split("}\n}", 1)[0])}
    assert light and dark, "палитра не прочиталась — разбор сломан"
    return light, dark


def _linear(channel: float) -> float:
    channel /= 255
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    text = colour.lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def ratio(fore: str, back: str) -> float:
    a, b = luminance(fore), luminance(back)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)
