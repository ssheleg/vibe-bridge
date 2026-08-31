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

import vibebridge

WEBUI = Path(vibebridge.__file__).parent / "webui"
SURFACES = ("index.html", "mascot.html")


def _css(name: str) -> str:
    text = (WEBUI / name).read_text()
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
