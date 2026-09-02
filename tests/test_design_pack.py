"""The style pack, enforced instead of remembered.

`docs/design/ui.md` is the project's adopted projection of the workbench pack.
Three of its rules are mechanical, so they are checked mechanically here —
every one of them had already been broken by hand:

* the type scale ("ни одного ad-hoc font-size в диффе") did not exist at all,
  and twenty-five ad-hoc sizes had accumulated across two files;
* the chat was built with bubbles, which the pack forbids by name;
* the palette lived in two copies, one per surface.

A rule a reviewer has to remember is a rule that erodes. These fail the build.
"""
from __future__ import annotations

import re
from pathlib import Path

from webui_rules import code_of

import vibebridge

WEBUI = Path(vibebridge.__file__).parent / "webui"
def _surfaces() -> tuple[str, ...]:
    """КАЖДАЯ html-поверхность, а не список из двух, который надо помнить.

    Список был `("index.html", "mascot.html")`, а поверхностей четыре: дверь
    жила питоновской строкой в `web.py`, offline — файлом, который никто не
    перечислил. Обе держали свою палитру и свои размеры — не по небрежности,
    а потому что механизм смотрел мимо них (V-1). Перечисление руками — это
    и есть тот механизм.
    """
    found = tuple(sorted(p.name for p in WEBUI.glob("*.html")))
    assert found, "в webui нет ни одной страницы — гейт смотрит не туда"
    return found


SURFACES = _surfaces()


def _css(name: str) -> str:
    """Стили страницы БЕЗ комментариев.

    Читало прямо из файла — и ловило пример из поясняющего комментария как
    нарушение: строка «раньше здесь было `h1{font-size:20px}`» неотличима от
    самого правила, если смотреть грепом. Это класс A-32, тот же самый, и
    ответ на него в проекте уже написан — `webui_rules.code_of`.
    """
    text = code_of(name)
    return text.split("<style>", 1)[1].split("</style>", 1)[0]


def test_no_surface_declares_a_font_size_outside_the_scale():
    """The craft bar of the pack, stated in its own words."""
    for name in SURFACES:
        bad = re.findall(r"font(?:-size)?\s*:\s*[^;{}]*?\b\d+px", _css(name))
        assert not bad, f"{name}: ad-hoc размеры {bad}"


def test_the_scale_exists_and_is_shared():
    tokens = (WEBUI / "tokens.css").read_text()
    for token in ("--t-page", "--t-card", "--t-body", "--t-data", "--t-cap",
                  "--t-note"):
        assert token in tokens
    for name in SURFACES:
        assert 'href="/tokens.css"' in (WEBUI / name).read_text()


def test_the_palette_is_not_copied_per_surface():
    """Two copies of one truth is the defect this project keeps re-learning."""
    for name in SURFACES:
        css = _css(name)
        assert "--accent:#" not in css.replace(" ", "")
        assert "--ink:#" not in css.replace(" ", "")


def test_the_chat_is_a_console_not_a_messenger():
    """«Чат: пузырей нет — это консоль, не мессенджер (анти-vision §6):
    реплики как строки ленты с ролью-лейблом»."""
    css = _css("mascot.html")
    turn = css.split(".turn.me .who{", 1)[0]
    assert "border-radius" not in turn.split(".turn{", 1)[1].split("}", 1)[0]
    assert ".turn .who" in css                 # the role label the pack asks for


def test_data_is_monospaced_with_tabular_figures():
    """A version or an uptime that shifts width as it ticks is unreadable in
    a column."""
    tokens = (WEBUI / "tokens.css").read_text()
    assert "tabular-nums" in tokens
    assert "--font-data" in tokens


def test_the_ban_list_is_recorded_where_it_is_broken():
    """The pack bans mascots in product UI and the owner asked for one. That
    is a decision, and a decision that is not written down is a breach."""
    doc = (Path(vibebridge.__file__).parent.parent
           / "docs" / "design" / "ui.md").read_text()
    assert "бан-лист" in doc.lower()
    # The amendment must name the mascot explicitly, not hide behind silence.
    assert "маскот" in doc.lower()


# ── layout: cards are cards, not columns ──────────────────────────────────


def _rule(css: str, selector: str) -> str:
    """The declarations of one rule, comments stripped — never the file text.

    Two earlier tests here matched their own explanatory comments instead of
    the CSS, so this reads rules only.
    """
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for block in re.findall(r"([^{}]+)\{([^}]*)\}", body):
        if selector in [x.strip() for x in block[0].split(",")]:
            return block[1].replace(" ", "")
    return ""


def test_the_panel_does_not_split_into_a_fixed_column_and_a_leftover():
    """`280px 1fr` gave the dashboard one narrow stack of real cards and one
    wide column holding a single card — which then stretched to the stack's
    height as an empty box (seen on screen 2026-08-31). Cards flow instead."""
    css = _css("index.html")
    grid = _rule(css, ".cards")
    assert "auto-fill" in grid and "minmax" in grid
    assert "280px1fr" not in re.sub(r"/\*.*?\*/", "", css, flags=re.S).replace(" ", "")


def test_a_card_is_its_own_height():
    """The whole fix, in one declaration: a grid item stretches to its row's
    height unless told otherwise, and that stretch WAS the empty column."""
    assert "align-items:start" in _rule(_css("index.html"), ".cards")


def test_the_event_feed_is_bounded():
    """An unbounded feed grows past everything beside it and takes the row's
    height with it."""
    feed = _rule(_css("index.html"), "#feed")
    assert "max-height" in feed and "overflow-y:auto" in feed


def test_no_card_places_its_own_margin():
    """Spacing between cards belongs to the grid's `gap`. Inline
    `margin-top:16px` on some cards and not others was the patch a fixed
    layout needed; with the grid it is a second source of truth for one
    number. Content INSIDE a card is not this rule's business."""
    html = (WEBUI / "index.html").read_text()
    cards = re.findall(r"<div class=\"card[^\"]*\"[^>]*>", html)
    assert cards, "карточек не найдено — тест смотрит не туда"
    offenders = [c for c in cards if "margin" in c]
    assert not offenders, f"карточка ставит себе отступ вручную: {offenders}"



# --------------------------------------------------------------------------
# V-1: копий палитры было ЧЕТЫРЕ, а смотрел набор на две.
# --------------------------------------------------------------------------


def test_the_gate_sees_every_surface_the_product_serves():
    """Список поверхностей обнаруживается, а не помнится.

    Пока он был написан руками, дверь и offline держали свои палитры годами:
    механизм смотрел мимо них. Тест закрепляет обнаружение — добавить пятую
    страницу и забыть про неё больше нельзя.
    """
    assert set(SURFACES) == {p.name for p in WEBUI.glob("*.html")}
    for must in ("index.html", "mascot.html", "door.html", "offline.html"):
        assert must in SURFACES, f"поверхность «{must}» пропала из набора"


def test_no_surface_hardcodes_a_colour_the_tokens_already_name():
    """Хекс на странице — это копия палитры, даже если он совпадает.

    Совпадающая копия хуже расходящейся: она выглядит правильной ровно до
    того релиза, в котором палитру трогают.
    """
    tokens = (WEBUI / "tokens.css").read_text(encoding="utf-8")
    known = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{3,8}", tokens)}
    for name in SURFACES:
        used = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{3,8}", _css(name))}
        assert not (used & known), (
            f"{name}: цвета из палитры вписаны руками — {sorted(used & known)}; "
            f"это `var(--…)`")


def test_the_manifest_takes_its_colours_from_the_tokens():
    """Манифест не умеет CSS-переменных — значит его цвета ГЕНЕРИРУЮТСЯ.

    Четвёртая копия палитры лежала здесь и совпадала по случайности.
    """
    import json

    from vibebridge.web import manifest_body, token_value

    raw = (WEBUI / "manifest.webmanifest").read_text(encoding="utf-8")
    assert "background_color" not in raw and "theme_color" not in raw, (
        "цвет снова вписан в файл манифеста — это копия палитры")
    css = (WEBUI / "tokens.css").read_text(encoding="utf-8")
    data = json.loads(manifest_body(raw, css))
    assert data["background_color"] == token_value(css, "--bg")
    assert data["theme_color"] == token_value(css, "--accent")


def test_the_offline_page_keeps_its_skin_without_a_network():
    """Страница «нет связи» ссылается на токены — значит воркер обязан их
    закэшировать, иначе она облезет ровно тогда, когда нужна."""
    sw = code_of("sw.js")
    assert '"/tokens.css"' in sw, (
        "offline ссылается на /tokens.css, а воркер его не кэширует — "
        "без сети страница останется без палитры")
    assert "offline.html" in sw
