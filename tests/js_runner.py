"""Исполнение нашего JS настоящим движком — потому что grep им не является.

~90 ассертов по нашим страницам были поиском подстроки в тексте файла. Они
проверяют, что код НАПИСАН, и ничего не говорят о том, что он делает.
`localTs()` — та самая функция, чей баг «UTC как настенные часы» доехал до
владельца, — была прикрыта единственным `assert "function localTs(" in html`
(A-39). Такой ассерт остался бы зелёным при любом содержимом тела.

`node` на машине есть и уже применялся руками. Здесь он применяется набором.

Отсутствие `node` — не молчаливый пропуск: тест пропускается С ПРИЧИНОЙ,
названной вслух, потому что «зелено, потому что не проверяли» — это та самая
подмена, которую весь угол тестов и разбирает.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import vibebridge

WEBUI = Path(vibebridge.__file__).parent / "webui"


def node_or_skip() -> str:
    exe = shutil.which("node")
    if not exe:
        pytest.skip("node не найден — исполняемые проверки JS НЕ прогонялись "
                    "(это пропуск, а не успех)")
    return exe


def extract(page: str, name: str) -> str:
    """Исходник одной функции целиком — по балансу фигурных скобок.

    `split("}")` здесь не годится по той же причине, по какой не годился в
    моторной доктрине (A-33): он режет по первой скобке, а у функции их
    много.
    """
    text = (WEBUI / page).read_text(encoding="utf-8")
    start = text.index(f"function {name}(")
    i = text.index("{", start)
    depth, j = 1, i + 1
    while j < len(text) and depth:
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
        j += 1
    return text[start:j]


def run(sources: list[str], driver: str, *, tz: str = "UTC") -> object:
    """Собрать сорцы + драйвер, выполнить в node, вернуть разобранный JSON.

    `TZ` задаётся явно: местное время — это то, что мы и проверяем, и
    зависеть от зоны машины, на которой гоняют набор, значит писать тест,
    который зеленеет по-разному в разных часовых поясах.
    """
    exe = node_or_skip()
    script = "\n".join([*sources, driver])
    out = subprocess.run([exe, "-e", script], capture_output=True, text=True,
                         timeout=30, env={"TZ": tz, "PATH": "/usr/bin:/bin"})
    if out.returncode != 0:
        raise AssertionError(f"node упал: {out.stderr.strip()[:400]}")
    return json.loads(out.stdout)
