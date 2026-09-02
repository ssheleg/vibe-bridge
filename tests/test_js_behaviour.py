"""Наш JS — исполняется, а не читается (A-39).

Каждый тест здесь запускает настоящий движок на настоящем исходнике страницы.
Проверяется ПОВЕДЕНИЕ: что функция возвращает на входах, включая те, на
которых она однажды уже ошиблась у владельца.
"""
from __future__ import annotations

from tests.js_runner import extract, run


def test_localts_turns_utc_into_the_owners_clock():
    """Баг, который доехал до владельца: журнал пишет UTC, а панель печатала
    его как настенное время — 20:45 при 22:45 на часах. Прикрыт он был одним
    `assert "function localTs(" in html`, то есть ничем."""
    src = extract("index.html", "localTs")
    got = run([src], """
        console.log(JSON.stringify({
          berlin: localTs("2026-09-02T18:45:32+00:00"),
          same_moment_other_offset: localTs("2026-09-02T20:45:32+02:00"),
        }));
    """, tz="Europe/Berlin")
    # 18:45:32 UTC в Берлине (летом, UTC+2) — это 20:45:32.
    assert got["berlin"] == "2026-09-02 20:45:32"
    # Тот же момент, записанный со смещением, обязан дать ТО ЖЕ время.
    assert got["same_moment_other_offset"] == got["berlin"]


def test_localts_respects_the_readers_timezone_not_the_writers():
    src = extract("index.html", "localTs")
    moment = "2026-09-02T18:45:32+00:00"
    berlin = run([src], f'console.log(JSON.stringify(localTs("{moment}")))',
                 tz="Europe/Berlin")
    tokyo = run([src], f'console.log(JSON.stringify(localTs("{moment}")))',
                tz="Asia/Tokyo")
    assert berlin == "2026-09-02 20:45:32"
    assert tokyo == "2026-09-03 03:45:32"      # +9, и дата другая


def test_localts_does_not_invent_a_time_from_rubbish():
    """«Invalid Date» на экране — это тоже ложь, просто заметная."""
    src = extract("index.html", "localTs")
    got = run([src], """
        console.log(JSON.stringify({
          empty: localTs(""), junk: localTs("не дата"),
          nothing: localTs(null),
        }));
    """)
    assert got["empty"] == "" and got["nothing"] == ""
    assert "Invalid" not in got["junk"]


def test_the_attribute_escaper_closes_the_hole_it_was_written_for():
    """A-38 проверялась чтением исходника. Здесь она ИСПОЛНЯЕТСЯ: вход,
    который ломал атрибут, больше его не ломает."""
    src = extract("mascot.js", "vbEscAttr")
    got = run([src], """
        const evil = 'x" onmouseover="alert(1)';
        console.log(JSON.stringify({
          escaped: vbEscAttr(evil),
          rendered: `<div title="${vbEscAttr(evil)}">`,
          quote: vbEscAttr('"'), apos: vbEscAttr("'"),
          amp: vbEscAttr("a & b"), lt: vbEscAttr("<b>"),
          empty: vbEscAttr(null),
        }));
    """)
    assert '"' not in got["escaped"], "кавычка уцелела — атрибут всё ещё рвётся"
    # Настоящее свойство: в разметке ровно ДВЕ кавычки — открывающая и
    # закрывающая значение. Слово `onmouseover=` внутри значения остаётся, и
    # это правильно: как ТЕКСТ оно безвредно, а вырезать его значило бы
    # чинить не то. Ломала атрибут кавычка, и её больше нет.
    assert got["rendered"].count('"') == 2
    value = got["rendered"].split('title="', 1)[1].rsplit('"', 1)[0]
    assert "&quot;" in value and '"' not in value
    assert got["quote"] == "&quot;" and got["apos"] == "&#39;"
    assert got["amp"] == "a &amp; b" and got["lt"] == "&lt;b&gt;"
    assert got["empty"] == ""


def test_the_escaper_does_not_double_encode_its_own_output():
    """Двойное экранирование — это `&amp;quot;` на экране владельца: не дыра,
    но враньё о том, что сказал робот."""
    src = extract("mascot.js", "vbEscAttr")
    got = run([src], """
        console.log(JSON.stringify(vbEscAttr("a & b")));
    """)
    assert got == "a &amp; b"
