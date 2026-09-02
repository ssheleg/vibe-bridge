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

from webui_rules import code_of, declared

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


# --------------------------------------------------------------------------
# V-2 и V-7: один цвет — одно значение, и раскладку не анимирует НИЧТО.
# --------------------------------------------------------------------------

#: Селекторы, которым амбер положен по доктрине: «нужен человек» — то есть
#: ожидающий консент-запрос и всё, что его показывает. Список короткий
#: намеренно: смысл цвета держится тем, что он редкий.
AMBER_IS_FOR = ("consent", "drain", "warnc", "dot.warn", "attention",
                "problems", "asks", "deadline")


def _declarations(css: str, prop: str) -> list[tuple[str, str]]:
    """(селектор, тело) для каждого правила, где встречается свойство."""
    out = []
    for chunk in css.split("}"):
        head, sep, body = chunk.partition("{")
        if sep and prop in body:
            out.append((head.strip().replace("\n", " "), body.strip()))
    return out


def test_amber_means_one_thing_across_every_surface():
    """`--warn` занят значением «нужен человек» — и занят ЦЕЛИКОМ.

    Тумблер паузы красился `--warn`, а в сорока пикселях от него тем же
    `--warn` красилась точка ожидающего запроса: один цвет, два значения.
    Пауза по доктрине нейтральна («это не ошибка, а честное состояние»), и
    точка состояния с питомцем уже красили её нейтралью — спорил один
    тумблер (V-2).
    """
    for name in SURFACES:
        for selector, _body in _declarations(_css(name), "--warn"):
            assert any(word in selector for word in AMBER_IS_FOR), (
                f"{name}: амбер в «{selector}» — а он зарезервирован за "
                f"«нужен человек»; если значение новое, оно должно попасть "
                f"в доктрину и в AMBER_IS_FOR, а не появиться молча")


def test_pause_is_neutral_wherever_it_is_shown():
    """Пауза выглядит одинаково на всех поверхностях, а не по-своему."""
    on = []
    for chunk in _css("index.html").split("}"):
        head, sep, body = chunk.partition("{")
        # `aria-checked` живёт в СЕЛЕКТОРЕ, а не в теле правила — первая
        # версия искала его в теле и нашла пустоту, то есть зачла бы
        # отсутствие тумблера за успех.
        if sep and 'aria-checked="true"' in head and ".switch" in head:
            on.append(body)
    assert on, "тумблер паузы пропал из панели"
    for body in on:
        assert "--warn" not in body, (
            "включённая пауза снова амбер — это цвет ожидающего запроса")
    # Питомец: пауза — приглушённый чернильный цвет, без цвета статуса.
    js = code_of("mascot.js")
    paused = js.split("paused:", 1)[1].split("}", 1)[0]
    assert "--muted" in paused, f"питомец красит паузу не нейтралью: {paused}"


def test_no_transition_animates_layout_either():
    """Моторная доктрина запрещала анимировать раскладку — и проверялась
    только на `@keyframes`.

    Тумблер паузы ехал `transition: left`, то есть ровно тем, что запрещено,
    и проходил насквозь: `left` в переходе — такая же раскладка, как `left`
    в кадре. Три таймер-бара ехали `width` по той же причине (V-7).
    """
    banned = ("left", "top", "right", "bottom", "width", "height",
              "margin", "padding", "font-size", "all")
    seen = 0
    for name in (*SURFACES, "mascot.js"):
        for selector, body in _declarations(code_of(name), "transition"):
            for decl in body.split(";"):
                if "transition" not in decl:
                    continue
                value = decl.split(":", 1)[1] if ":" in decl else ""
                for step in value.split(","):
                    prop = step.strip().split()[0] if step.strip() else ""
                    seen += 1
                    assert prop not in banned, (
                        f"{name}: «{selector}» анимирует раскладку "
                        f"через transition: {prop}")
    # Канарейка на разбор: молчаливый ноль выглядит как успех.
    assert seen >= 8, f"гейт увидел всего {seen} переходов — разбор сломан"


# --------------------------------------------------------------------------
# V-4: панель — заявленный канал для телефона, а раскладки не было ни одной.
# --------------------------------------------------------------------------

DOCTRINE = Path(vibebridge.__file__).parents[1] / "docs" / "design" / "ui.md"


def _doctrine_breakpoint() -> int:
    """Число берётся ИЗ ДОКТРИНЫ, а не пишется здесь второй раз.

    Иначе тест закрепляет собственное мнение о продукте: доктрину поправят,
    набор останется зелёным, и разойдутся они молча.
    """
    text = DOCTRINE.read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if "**Responsive.**" in ln)
    tail = text.split(line, 1)[1][:400]
    found = re.search(r"<(\d{3,4})px", line + tail)
    assert found, "в доктрине не найден брейкпоинт телефона"
    return int(found.group(1))


def test_the_panel_has_a_phone_layout_at_the_doctrine_breakpoint():
    """Манифест, service worker, пуши и ссылка «Телефон» — канал заявлен.
    Единственным `@media` про ширину был `640px` про широкие карточки."""
    css = _css("index.html")
    edge = _doctrine_breakpoint()
    assert f"max-width:{edge}px" in css.replace(" ", ""), (
        f"доктрина обещает раскладку до {edge}px, в панели её нет")


def test_on_a_phone_the_tabs_are_within_thumb_reach():
    """Пак: вкладки СНИЗУ. Большой палец не достаёт до верха телефона."""
    css = _css("index.html").replace(" ", "")
    block = css.split(f"@media(max-width:{_doctrine_breakpoint()}px){{", 1)
    assert len(block) == 2, "нет блока телефонной раскладки"
    body = block[1]
    tabs = body.split(".tabs{", 1)[1].split("}", 1)[0]
    assert "position:fixed" in tabs and "bottom:0" in tabs, (
        f"вкладки не прижаты к низу: {tabs}")
    page = body.split("body{", 1)[1].split("}", 1)[0]
    assert "padding" in page and "env(safe-area-inset-bottom)" in page, (
        "страница не резервирует место под полосу — контент уедет под неё")


def test_the_safe_area_is_not_a_silent_zero():
    """`env(safe-area-inset-*)` без `viewport-fit=cover` в мете всегда ноль.

    Правило при этом выглядит написанным — то есть тихо не работает.
    """
    page = code_of("index.html")
    if "safe-area-inset" in page:
        assert "viewport-fit=cover" in page, (
            "safe-area используется, а `viewport-fit=cover` в мете нет — "
            "отступ всегда ноль, и это не видно")


def test_nothing_in_the_header_is_pinned_wider_than_a_phone():
    """Шапка `display:flex` без переноса выпирала и тянула боковой скролл:
    измерено 2026-09-02 в браузере — на 390px страница ехала на 96px, за
    краем оказывались вкладки и сам рубильник паузы."""
    css = _css("index.html").replace(" ", "")
    block = css.split(f"@media(max-width:{_doctrine_breakpoint()}px){{", 1)[1]
    bar = block.split(".appbar{", 1)[1].split("}", 1)[0]
    assert "flex-wrap:wrap" in bar, f"шапка не переносится: {bar}"
    cards = block.split(".cards{", 1)[1].split("}", 1)[0]
    assert "grid-template-columns:1fr" in cards, (
        "на телефоне карточки не в одну колонку")


# --------------------------------------------------------------------------
# V-5: кольцо фокуса «на всём» — а на виджете его не было вовсе.
# --------------------------------------------------------------------------


def test_the_focus_ring_lives_with_the_tokens_not_per_surface():
    """Пак требует кольцо «на всём». У панели правило было ОДНО, у
    плавающего виджета — ни одного, при том что решение по согласию
    принимается именно на виджете: клавиатурой там не было видно ничего.

    Копия на страницу — тот же дефект, что копия палитры: она появляется на
    трёх поверхностях из четырёх и молчит на четвёртой.
    """
    tokens = (WEBUI / "tokens.css").read_text(encoding="utf-8")
    rule = tokens.split(":focus-visible{", 1)
    assert len(rule) == 2, "кольца фокуса нет в общем файле"
    body = rule[1].split("}", 1)[0].replace(" ", "")
    assert "outline:2px" in body and "var(--accent)" in body, body
    assert "outline-offset:2px" in body, body


def test_no_surface_puts_out_the_focus_ring():
    """`outline:none` без замены — это «кольцо есть, но невидимо»."""
    for name in (*SURFACES, "tokens.css", "mascot.js"):
        css = code_of(name).replace(" ", "")
        for killer in ("outline:none", "outline:0"):
            assert killer not in css, (
                f"{name}: {killer} гасит фокус; если нужен другой индикатор, "
                f"он должен быть виден и объяснён рядом")


def test_every_surface_can_show_the_ring_at_all():
    """Правило в общем файле бесполезно на странице, которая его не грузит."""
    for name in SURFACES:
        assert 'href="/tokens.css"' in code_of(name), (
            f"{name} не грузит токены — кольца фокуса на ней не будет")


# --------------------------------------------------------------------------
# V-8 и V-9: контролы одеты один раз, и текст читается в обеих темах.
# --------------------------------------------------------------------------


def test_the_input_controls_are_dressed_once_for_every_surface():
    """Правил для `.field`, `label` и `input` не было НИ ОДНОГО — при том что
    единственный экран, где владелец вводит адрес робота, его ключ и пароль
    от Wi-Fi, это форма (V-8)."""
    tokens = (WEBUI / "tokens.css").read_text(encoding="utf-8")
    for selector in (".field{", ".field label{"):
        assert selector in tokens.replace("\n", ""), (
            f"нет общего правила «{selector.rstrip('{')}»")
    rule = tokens.split("input, select, textarea{", 1)
    assert len(rule) == 2, "поля ввода не одеты в общем файле"
    body = rule[1].split("}", 1)[0].replace(" ", "").replace("\n", "")
    for need in ("var(--font-ui)", "var(--ink)", "var(--panel)",
                 "var(--border-strong)", "var(--r-control)"):
        assert need in body, f"контрол не берёт {need} из палитры: {body}"


def test_a_control_is_not_dressed_by_hand_in_the_markup():
    """Инлайновый `style` не видит НИ ОДИН гейт этого файла: все они читают
    содержимое `<style>`. Одетые контролы жили именно там — две копии."""
    for name in SURFACES:
        page = code_of(name)
        for inline in re.findall(r'<(?:input|select|textarea)[^>]*style="([^"]*)"',
                                 page):
            for banned in ("font", "background", "border-radius", "color:"):
                assert banned not in inline, (
                    f"{name}: контрол одет инлайном («{inline}») — мимо всех "
                    f"проверок; место такому правилу в tokens.css")


def test_every_text_colour_meets_aa_in_both_themes():
    """Считается, а не запоминается: число в тесте — снимок палитры на день,
    когда его писали (V-9).

    Красная строка баннера давала 4.36:1 по `--panel-2` в тёмной теме —
    единственная строка, которая сообщает, что граница безопасности стоит
    неверно. Правило пака на этот случай было написано и не применено.
    """
    from contrast import palette, ratio

    плохо = []
    for theme, colours in zip(("светлая", "тёмная"), palette(), strict=True):
        for ink in ("--ink", "--muted"):
            for surface in ("--bg", "--panel", "--panel-2"):
                value = ratio(colours[ink], colours[surface])
                if value < 4.5:
                    плохо.append(f"{theme}: {ink} на {surface} = {value:.2f}:1")
    assert not плохо, "текст ниже AA: " + "; ".join(плохо)


def test_a_status_colour_never_carries_the_words_of_a_banner():
    """Правило пака дословно: «статус никогда цветом в одиночку» — слово
    цветом `--ink`, цвет несёт точка или заливка."""
    for name in SURFACES:
        for chunk in _css(name).split("}"):
            head, sep, body = chunk.partition("{")
            if not sep or "banner" not in head and "note" not in head:
                continue
            # ИМЯ свойства целиком: `border-color:var(--danger)` содержит
            # подстроку `color:var(--danger)`, и наивный поиск ловил рамку
            # вместо текста. Тот же класс резал разбор трижды за сессию,
            # поэтому разбор теперь общий — `webui_rules.declared`.
            painted = declared(body, "color")
            if painted is None:
                continue
            for status in ("--danger", "--warn", "--ok"):
                assert f"var({status})" not in painted.replace(" ", ""), (
                    f"{name}: «{head.strip()}» красит слово статусом "
                    f"{status} — на тёмной теме это уходит ниже AA")
