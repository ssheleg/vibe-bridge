"""Кнопка гранта называет НАСТРОЕННЫЙ срок, а не зашитые 15 минут (U-13).

`grant_ttl_s` — настройка, а все четыре поверхности говорили «15 мин».
Поменяв её, владелец получал кнопку, которая обещает не то, что произойдёт:
интерфейс, расходящийся с поведением, — тот же дефект, что U-2, только
дешевле в починке и потому проживший дольше.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

import vibebridge

REPO = Path(vibebridge.__file__).resolve().parents[1]
WEBUI = Path(vibebridge.__file__).parent / "webui"
sys.path.insert(0, str(REPO))

from vibebridge.consent import ConsentEngine  # noqa: E402


@pytest.mark.parametrize("seconds,expected", [
    (900, "15 мин"), (3600, "1 ч"), (7200, "2 ч"), (45, "45 с"), (60, "1 мин"),
])
def test_the_label_reads_like_a_human_wrote_it(seconds, expected):
    """«на 900 с» не читается, «на 1 ч» и «на 15 мин» читаются по-разному."""
    assert ConsentEngine(grant_ttl_s=seconds).grant_label() == expected


def test_no_surface_hardcodes_fifteen_minutes():
    """Четыре поверхности, одна правда. Проверяются РЕШЕНИЯ, а не прозаические
    упоминания: комментарий, объясняющий дефект, — не дефект."""
    места = []
    for path in (Path(vibebridge.__file__).parent / "app.py",
                 WEBUI / "index.html", WEBUI / "mascot.html",
                 WEBUI / "mascot.js"):
        text = path.read_text(encoding="utf-8")
        код = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        код = re.sub(r"^\s*(?://|#).*$", "", код, flags=re.M)
        for line in код.splitlines():
            if "15 мин" in line:
                места.append(f"{path.name}: {line.strip()[:70]}")
    assert not места, (
        "срок гранта снова зашит в поверхность: " + "; ".join(места))


def test_the_snapshot_carries_the_label_to_every_surface():
    """Слово приходит из движка — иначе поверхности снова разойдутся."""
    web = (Path(vibebridge.__file__).parent / "web.py").read_text(
        encoding="utf-8")
    assert '"grant_ttl_label": consent.grant_label()' in web, (
        "снимок не несёт срок гранта — поверхностям неоткуда его взять")
    for name in ("index.html", "mascot.html", "mascot.js"):
        assert "grant_ttl_label" in (WEBUI / name).read_text(encoding="utf-8"), (
            f"{name} не читает срок из снимка")


def test_a_surface_without_the_label_does_not_invent_a_number():
    """Пока снимок не пришёл, кнопка не имеет права называть срок наугад."""
    for name in ("mascot.js", "mascot.html"):
        text = (WEBUI / name).read_text(encoding="utf-8")
        запасное = re.search(r'grant_ttl_label \|\| "([^"]*)"', text)
        assert запасное, f"{name}: у срока нет запасного значения"
        assert not re.search(r"\d", запасное.group(1)), (
            f"{name}: запасное значение называет число «{запасное.group(1)}» — "
            f"а мы его в этот момент не знаем")
