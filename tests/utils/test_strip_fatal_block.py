# tests/test_strip_fatal_block.py
"""Guards for the Log's own view of a fatal: `strip_fatal_block` and the ONE
funnel that uses it, `gui/docks/_common.show_message` (design
design_2026_09_17_spoke_cell_editing.md §9 X2 / plan_2026_09_18_spoke_tails.md
Р1, С1–С4).

What the guards defend:
  * С1 — a format_fatal_error() block reaching the Log loses its '=' box and
    its "Placement stopped ... run again" verdict (in the GUI nothing is
    restarted; the box ate four Log lines — Denis's live complaint 2026-09-18);
  * С2 — the REASON is kept verbatim: the 'FATAL ERROR: ...' and '✗ ...' lines,
    indentation included;
  * С3 — an ordinary single-line error passes through unchanged;
  * С3b — an ordinary MULTI-line message passes through byte-for-byte (the
    regression `fatal_error_reason`'s default mode would cause: it joins the
    reason lines with "; ");
  * С4 — the CLI's block is untouched: format_fatal_error still builds the box
    and the verdict, so CLI output is byte-identical.

Under `ru` the box and the verdict are recognised by their OWN localized msgids
(never by an English prefix), so a translated catalogue strips a translated
block — the same discipline fatal_error_reason already documents.
"""
import gettext
import logging

from gui.docks._common import ERROR_STYLE, show_message
import kicadstamp.exceptions as exceptions_module
import kicadstamp.i18n as i18n_module
from kicadstamp.exceptions import (
    ValidationError,
    format_fatal_error,
    strip_fatal_block,
)

_TITLE = "C44 has Role 'C_OUT_BYPASS' which is not in cell 'fpga_pwr_bank'"
_HINT = "cell 'fpga_pwr_bank' has roles: C_FPGA_BULK, C_FPGA_BYPASS"
_VERDICT = "Placement stopped, board not modified. Fix the config and run again."


def _fatal(title=_TITLE, hints=(_HINT,)):
    return ValidationError(format_fatal_error(title, list(hints)))


def _logged(caplog, text, style=ERROR_STYLE):
    """show_message the way a dock's own _show_message does, and return the ONE
    record's message — the exact string the Log dock renders."""
    logger = logging.getLogger("guard.strip_fatal_block")
    with caplog.at_level(logging.INFO, logger=logger.name):
        show_message(text, style, logger)
    assert caplog.records, "show_message logged nothing"
    return caplog.records[-1].getMessage()


# ── С1/С2: the block becomes its reason ─────────────────────────────────────

def test_c1_box_and_verdict_never_reach_the_log(caplog):
    text = _logged(caplog, str(_fatal()))
    assert "=" * 10 not in text, "the '=' box still reaches the Log"
    assert _VERDICT not in text, "the CLI verdict still reaches the Log"
    assert "run again" not in text


def test_c2_the_reason_is_kept_verbatim(caplog):
    reason = _fatal()
    text = _logged(caplog, str(reason))
    assert ("FATAL ERROR: " + _TITLE) in text
    assert ("✗ " + _HINT) in text
    # Byte-for-byte the same view the helper under test documents.
    assert text == strip_fatal_block(str(reason))


# ── С3/С3b: ordinary messages are untouched ─────────────────────────────────

def test_c3_a_plain_error_passes_through_unchanged(caplog):
    assert _logged(caplog, "just a plain problem") == "just a plain problem"


def test_c3b_a_multiline_non_fatal_passes_through_byte_for_byte(caplog):
    """fatal_error_reason's default mode joins lines with '; ' — that is right
    for an embedded one-line warning and WRONG for the Log. The funnel must
    return the dock's own multi-line text unchanged."""
    message = "first line\nsecond line\n\nfourth line"
    assert _logged(caplog, message) == message


# ── С4: the CLI block is untouched ──────────────────────────────────────────

def test_c4_format_fatal_error_still_builds_the_cli_block():
    text = str(_fatal())
    assert "=" * 70 in text
    assert _VERDICT in text
    assert "\n" + "=" * 70 + "\n" in text


def test_c4_strip_only_removes_the_box_and_verdict():
    stripped = strip_fatal_block(str(_fatal()))
    assert "=" not in stripped
    assert _VERDICT not in stripped
    assert "FATAL ERROR: " + _TITLE in stripped
    assert "✗ " + _HINT in stripped


def test_non_block_text_is_returned_identically():
    """The helper must never mistake a message that merely contains '=' inside
    a line for a box, and must return anything else untouched."""
    plain = "net = +3V3 is fine"
    assert strip_fatal_block(plain) == plain
    assert strip_fatal_block("") == ""


# ── ru: the box and the verdict come from the localized msgids ──────────────

def _force_ru(monkeypatch):
    translation = gettext.translation(
        "kicadstamp", localedir=str(i18n_module.LOCALE_DIR), languages=["ru"],
        fallback=True)
    assert translation.gettext("  FATAL ERROR: {title}") == "  ФАТАЛЬНАЯ ОШИБКА: {title}", (
        "the ru catalogue does not translate the fatal heading — this guard "
        "would silently test English")
    monkeypatch.setattr(exceptions_module, "_", translation.gettext)
    return translation.gettext


def test_ru_block_is_stripped_by_its_own_localized_marker(monkeypatch, caplog):
    _ = _force_ru(monkeypatch)
    reason = _fatal(title="ПРИЧИНА", hints=("подсказка",))
    text = str(reason)
    assert "ФАТАЛЬНАЯ ОШИБКА" in text          # the ru catalogue really built it

    logged = _logged(caplog, text)
    assert "=" * 10 not in logged
    assert _("Placement stopped, board not modified. Fix the config and run again.") not in logged
    assert "ПРИЧИНА" in logged
    assert "✗ подсказка" in logged
