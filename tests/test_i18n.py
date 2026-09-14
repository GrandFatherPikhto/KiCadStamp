#!/usr/bin/env python3
"""Tests for kicadstamp/i18n.py — language detection precedence and the
setup_i18n() install mechanism. See docs/i18n_translation.md."""
import ast
import json
import re
import string
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import gettext
import pytest
from kicadstamp.i18n import detect_language, setup_i18n

ROOT = Path(__file__).parent.parent
EN_PO = ROOT / "locales" / "en" / "LC_MESSAGES" / "kicadstamp.po"
RU_PO = ROOT / "locales" / "ru" / "LC_MESSAGES" / "kicadstamp.po"
RU_MO = RU_PO.with_suffix(".mo")

# Config-section names that appear verbatim in user-facing catalog entries.
# Kept in sync with diagnostics/scan_ru_catalogue_damage.py's SECTIONS — the
# detector that found the 2026-09-13 catalogue drift.
CONFIG_SECTIONS = (
    "thermal_via_arrays", "clone_placements", "coordinate_placements",
    "net_traces", "tree_instances", "scheme_lists", "extract_profiles",
    "clone_profiles", "sheet_templates", "entities", "chains", "cells",
    "points", "rules", "trees",
)

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)")

# msgid -> (placeholders the RU msgstr may legitimately DROP, reason).
# Dropping a placeholder is RED by default: .format(**kwargs) does not raise,
# but the value silently disappears from the message. The live proof is the
# 2026-09-13 bug where 'duplicate name(s) in {section}: {names}' lost
# {section}, so every duplicate-name error in ANY config section reported the
# section as 'thermal_via_arrays'. Escape here only with a stated reason.
PLACEHOLDER_DROP_EXCEPTIONS = {
    "Exported {count} entr{suffix} to {name}": (
        frozenset({"suffix"}),
        "English-only pluralization suffix ('entry'/'entries'); Russian "
        "'записей' is invariant and needs no suffix",
    ),
}

# The recognized-duplicates list for the rule below: one JSON object per line,
# {"reason": ..., "msgids": [...]}.
RU_DUP_EXCEPTIONS_FILE = ROOT / "tests" / "ru_duplicate_msgstr_exceptions.jsonl"

# The shipped packages — the same scope pyproject.toml's packages.find uses.
# tests/ and tools/ are deliberately NOT scanned: their strings never reach
# users, so they don't need catalog entries.
SHIPPED_DIRS = (ROOT / "kicadstamp", ROOT / "gui", ROOT / "mcp_server")


class TestDetectLanguagePrecedence:
    """LANGUAGE > LC_ALL > LC_MESSAGES > LANG, default English."""

    def test_lang_ru(self, monkeypatch):
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.setenv("LANG", "ru_RU.UTF-8")
        assert detect_language() == "ru"

    def test_lang_non_ru(self, monkeypatch):
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.setenv("LANG", "de_DE.UTF-8")
        assert detect_language() == "en"

    def test_nothing_set_defaults_to_english(self, monkeypatch):
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.delenv("LANG", raising=False)
        assert detect_language() == "en"

    def test_lc_all_overrides_lang(self, monkeypatch):
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        assert detect_language() == "ru"

    def test_lc_messages_used_when_lc_all_and_lang_absent(self, monkeypatch):
        """LC_MESSAGES is the POSIX category specifically for message
        language — must be checked before falling back to the catch-all LANG."""
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.setenv("LC_MESSAGES", "ru_RU.UTF-8")
        monkeypatch.delenv("LANG", raising=False)
        assert detect_language() == "ru"

    def test_language_takes_priority_over_everything(self, monkeypatch):
        """LANGUAGE is a gettext extension with the highest precedence."""
        monkeypatch.setenv("LANGUAGE", "ru:en")
        monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        assert detect_language() == "ru"

    def test_language_only_first_entry_matters(self, monkeypatch):
        monkeypatch.setenv("LANGUAGE", "en:ru")
        monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
        assert detect_language() == "en"


class TestSetupI18n:
    """setup_i18n() actually installs a working translation function."""

    def test_returns_detected_language(self, monkeypatch):
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.setenv("LANG", "ru_RU.UTF-8")
        assert setup_i18n() == "ru"

    def test_ru_translates_a_known_string(self, monkeypatch):
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.setenv("LANG", "ru_RU.UTF-8")
        setup_i18n()
        import kicadstamp.i18n as i18n_module
        assert i18n_module._("Flip performed") == "Флип выполнен"

    def test_en_leaves_a_known_string_in_english(self, monkeypatch):
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        setup_i18n()
        import kicadstamp.i18n as i18n_module
        assert i18n_module._("Flip performed") == "Flip performed"

    def test_unknown_locale_falls_back_without_raising(self, monkeypatch):
        """fallback=True in gettext.translation() must swallow a missing
        catalog gracefully — should never crash the whole CLI over this."""
        monkeypatch.delenv("LANGUAGE", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        monkeypatch.setenv("LANG", "fr_FR.UTF-8")
        lang = setup_i18n()
        assert lang == "en"


def _unescape_po_string(raw: str) -> str:
    """One or more adjacent "..." quoted PO lines -> the decoded string
    (gettext escapes: \\n, \\t, \\", \\\\). Deliberately not str.encode()
    .decode('unicode_escape') — that mangles non-ASCII text, which this
    catalog is full of (Cyrillic)."""
    body = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', raw))
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            n = body[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(n, n))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _po_entries(po_path: Path):
    """Yields (msgid, msgstr) for every entry with BOTH sides non-empty —
    the header entry (msgid "") and untranslated entries (msgstr "") carry
    nothing to check here."""
    text = po_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'msgid ((?:"(?:[^"\\]|\\.)*"\n?)+)msgstr ((?:"(?:[^"\\]|\\.)*"\n?)+)')
    for m in pattern.finditer(text):
        mid = _unescape_po_string(m.group(1))
        mstr = _unescape_po_string(m.group(2))
        if mid and mstr:
            yield mid, mstr


def _recognized_duplicate_groups() -> dict:
    """Parse RU_DUP_EXCEPTIONS_FILE (JSONL, one group per line) into
    {frozenset(msgids): reason}."""
    out: dict = {}
    for line in RU_DUP_EXCEPTIONS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        msgids = obj["msgids"]
        assert len(msgids) >= 2, f"need >=2 msgids: {line!r}"
        out[frozenset(msgids)] = obj["reason"]
    return out


class TestLocaleCatalogIntegrity:
    """Catches malformed RU translations that `pybabel compile` does NOT
    reject — pybabel only validates %-style (python-format), never the
    {name}-style (python-brace-format) placeholders this project actually
    uses everywhere (see i18n.py/adapter.py's f-string-like `_(...).format(
    ...)` pattern).

    Found live 2026-08-08: a translation pass truncated one msgstr mid
    format-spec ('...повтор через {wait:.1f' — no closing brace, no
    trailing text). `pybabel compile` reported success; the break only
    surfaced at runtime, inside a retry-on-busy-KiCad handler
    (kicadstamp/kicad/adapter.py), the first time a RU-locale user hit a
    busy/modal KiCad dialog during a commit — turning a should-be-transient
    retry into a hard ValueError crash. These tests parse the actual .po
    text (not the compiled .mo, which by construction can't contain this
    class of bug post-fix) so a future truncated/malformed translation
    fails the test suite instead of waiting for a user to hit it live."""

    def test_ru_catalog_has_entries(self):
        """Guards the two tests below against a path typo silently
        iterating zero entries and passing vacuously."""
        assert sum(1 for _ in _po_entries(RU_PO)) > 500

    def test_ru_msgstr_is_valid_format_string(self):
        """Every translated msgstr must parse as a syntactically valid
        str.format() template on its own — this is what a truncated msgstr
        (unmatched '{') violates, independent of which placeholder names it
        happens to contain."""
        broken = []
        for mid, mstr in _po_entries(RU_PO):
            try:
                list(string.Formatter().parse(mstr))
            except ValueError as e:
                broken.append((mid[:70], mstr[:70], str(e)))
        assert not broken, (
            "malformed format string(s) in locales/ru/LC_MESSAGES/kicadstamp.po "
            f"(msgid, msgstr, error): {broken}"
        )

    def test_ru_msgstr_does_not_name_a_config_section_absent_from_msgid(self):
        """Found live 2026-09-13: the mass translation pass f20a806 slid
        msgstrs onto the wrong msgids, so some RU errors named a config
        section the English original never mentions. The worst case was
        'duplicate name(s) in {section}: {names}' -> 'дублирующиеся имена в
        thermal_via_arrays: {names}': {section} was dropped and the section
        name hard-coded, so EVERY duplicate-name error in ANY config section
        reported 'thermal_via_arrays' to the user. A msgstr may only name a
        section the msgid already names. No exceptions have been found; if
        one ever is, describe it here instead of silencing it."""
        sections = set(CONFIG_SECTIONS)
        bad = []
        for mid, mstr in _po_entries(RU_PO):
            extra = ({s for s in sections if s in mstr}
                     - {s for s in sections if s in mid})
            if extra:
                bad.append((mid[:70], mstr[:70], sorted(extra)))
        assert not bad, (
            "msgstr names config section(s) absent from its msgid — "
            "(msgid, msgstr, extra sections): " + repr(bad))

    def test_ru_msgstr_placeholders_are_a_subset_of_msgid(self):
        """A msgstr must never INVENT a placeholder name absent from msgid:
        the call site's .format(...) only ever supplies the English
        original's names, so a stray {typo} in the translation raises
        KeyError at runtime. (For DROPPED placeholders — the mirror image,
        and the one that bit us on 2026-09-13 — see
        test_ru_msgstr_does_not_drop_a_msgid_placeholder below.)"""
        invented = []
        for mid, mstr in _po_entries(RU_PO):
            mid_names = set(_PLACEHOLDER_RE.findall(mid))
            mstr_names = set(_PLACEHOLDER_RE.findall(mstr))
            extra = mstr_names - mid_names
            if extra:
                invented.append((mid[:70], mstr[:70], extra))
        assert not invented, (
            "msgstr references placeholder(s) not in msgid (would KeyError "
            f"at runtime) — (msgid, msgstr, extra names): {invented}"
        )

    def test_ru_msgstr_does_not_drop_a_msgid_placeholder(self):
        """The rule that was missing until 2026-09-13. The old docstring of
        the test above argued a DROPPED placeholder was safe because
        '.format(**kwargs) ignores unused kwargs' — true for crashing, false
        for meaning, as '{section}' proved: the translation hard-coded a
        section name and every duplicate-name error misdiagnosed any section
        as 'thermal_via_arrays'. So a lost placeholder is RED here, escaping
        only through the named, justified PLACEHOLDER_DROP_EXCEPTIONS."""
        bad = []
        for mid, mstr in _po_entries(RU_PO):
            allowed = PLACEHOLDER_DROP_EXCEPTIONS.get(mid, (frozenset(), ""))[0]
            lost = (set(_PLACEHOLDER_RE.findall(mid))
                    - set(_PLACEHOLDER_RE.findall(mstr)) - allowed)
            if lost:
                bad.append((mid[:70], mstr[:70], sorted(lost)))
        assert not bad, (
            "msgstr drops placeholder(s) present in msgid — the value is "
            "silently lost to the user; fix the translation or add a "
            "justified PLACEHOLDER_DROP_EXCEPTIONS entry — "
            "(msgid, msgstr, dropped names): " + repr(bad))

    def test_ru_msgstr_duplicates_are_only_the_recognized_ones(self):
        """One RU msgstr serving several DIFFERENT msgids is a red flag for
        the 2026-09-13 drift: the mass pass f20a806 slid translations onto
        the wrong msgids, so 'Center X mm:' / 'Center Y mm:' / 'Diameter
        (mm):' all read 'Диаметр (мм):'. Genuine coincidences (punctuation,
        case, synonyms, English wording variants) are listed, each with a
        reason, in tests/ru_duplicate_msgstr_exceptions.txt; every other
        repeat must be fixed in the .po."""
        by_msgstr: dict = {}
        for mid, mstr in _po_entries(RU_PO):
            by_msgstr.setdefault(mstr, []).append(mid)
        recognized = _recognized_duplicate_groups()
        bad = []
        for mstr, mids in by_msgstr.items():
            if len(mids) > 1 and frozenset(mids) not in recognized:
                bad.append((mstr[:60], sorted(mids)))
        assert not bad, (
            "msgstr shared by several msgids without being recognized — fix "
            "the translation(s) or add a justified line to "
            "tests/ru_duplicate_msgstr_exceptions.txt — "
            "(msgstr, msgids): " + repr(bad))

    def test_duplicate_exception_list_has_no_stale_groups(self):
        """Guards the whitelist from rotting: every recognized group must
        still exist as an actual duplicate (a stale entry would silently
        pre-approve a future mis-assignment), and each must carry a reason."""
        by_msgstr: dict = {}
        for mid, mstr in _po_entries(RU_PO):
            by_msgstr.setdefault(mstr, []).append(mid)
        actual = {frozenset(v) for v in by_msgstr.values() if len(v) > 1}
        recognized = _recognized_duplicate_groups()
        stale = sorted(tuple(sorted(k)) for k in set(recognized) - actual)
        assert not stale, (
            "stale entries in tests/ru_duplicate_msgstr_exceptions.txt "
            "(no longer a duplicate group): " + repr(stale))
        empty = [sorted(k) for k, r in recognized.items() if not r.strip()]
        assert not empty, f"recognized group(s) without a reason: {empty}"

    def test_set_anchor_translations_are_not_rule_anchor_leftovers(self):
        """Regression 2026-09-02: the "Set anchor…"/"Set anchor" RU msgstrs
        used to carry the stale «якорь правила» left over from the removed
        "rule anchor" msgid (Rule -> Chain rename); they must be the proper
        imperative «Назначить якорь…»/«Назначить якорь».

        Kept as a regression, but it is a private instance of a class now
        closed by general rules: a translation drifted onto a neighbouring
        message. See test_ru_msgstr_duplicates_are_only_the_recognized_ones
        (shared-msgstr rule) and
        test_ru_msgstr_does_not_name_a_config_section_absent_from_msgid
        (config-section rule) — both added 2026-09-13, when the same drift
        was found across the whole RU catalogue."""
        ru = dict(_po_entries(RU_PO))
        assert ru.get("Set anchor…") == "Назначить якорь…"
        assert ru.get("Set anchor") == "Назначить якорь"


def _source_msgids() -> set:
    """Every _("...") literal in the SHIPPED source, via stdlib ast. Implicit
    string concatenation is folded by the parser, so a multi-line
    `_("a " "b")` yields ONE msgid — the same folding pybabel (which generates
    the .po files) does, but without the babel dependency here. This is the
    exact set the en/ru catalogs must cover."""
    msgs: set = set()
    for base in SHIPPED_DIRS:
        for py in base.rglob("*.py"):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "_" and node.args):
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        msgs.add(arg.value)
    return msgs


def _po_all_msgids(po_path: Path) -> set:
    """Every msgid in a .po, translated OR untranslated — presence, not
    translation quality, is what completeness means (gettext falls back to the
    English msgid for an empty msgstr, so an untranslated entry still works).
    Obsolete (#~) entries are excluded: they're retired strings, not coverage."""
    text = po_path.read_text(encoding="utf-8")
    text = "\n".join(ln for ln in text.splitlines() if not ln.startswith("#~"))
    out: set = set()
    for m in re.finditer(r'^msgid ((?:"(?:[^"\\]|\\.)*"\n?)+)', text, re.M):
        mid = _unescape_po_string(m.group(1))
        if mid:
            out.add(mid)
    return out


class TestCatalogCompleteness:
    """A new _("...") string that isn't added to the catalogs silently falls
    back to English for RU users — and before this class no test caught it.
    These tests pin the completeness of BOTH catalogs against the shipped
    source, plus the compiled RU .mo (the binary gettext actually reads)."""

    def test_every_source_string_is_in_en_catalog(self):
        missing = sorted(_source_msgids() - _po_all_msgids(EN_PO))
        assert not missing, (
            "strings missing from locales/en/LC_MESSAGES/kicadstamp.po "
            f"(source _() without a catalog entry): {missing}")

    def test_every_source_string_is_in_ru_catalog(self):
        missing = sorted(_source_msgids() - _po_all_msgids(RU_PO))
        assert not missing, (
            "strings missing from locales/ru/LC_MESSAGES/kicadstamp.po "
            f"(source _() without a catalog entry): {missing}")

    def test_compiled_ru_mo_is_in_sync_with_po(self):
        """The .mo binary is what gettext reads at runtime — a .po edit that
        isn't recompiled ships a STALE binary (RU shows English, or worse: the
        OLD text, despite the .po being correct).

        TWO checks, because presence alone cannot see a half-done update:

          * PRESENCE — every TRANSLATED (non-empty msgstr) .po entry exists in
            the compiled .mo. Catches a recompile that dropped entries (e.g.
            the fuzzy ones, see i18ntroubles Ловушка 3);
          * VALUE — the .mo's message EQUALS the .po's, compared as DECODED
            strings. Catches a .po edit that was never recompiled at all: the
            .mo keeps the previous wording while the .po shows the fix.

        Found live 2026-09-14 (plan_2026_09_13_three_unsentinelled_guards Х1):
        with `git show 9bf240c:locales/ru/LC_MESSAGES/kicadstamp.mo` placed next
        to the fixed .po, all 22 tests in this file stayed GREEN while the user
        read the very lie the .po fix removed — 'дублирующиеся имена в
        thermal_via_arrays' for every config section. The .po is not what
        anybody reads; the .mo is.

        Plural entries are keyed by (msgid, n) in a .mo, never by plain msgid:
        the RU catalogue has none today (verified 2026-09-14 — `msgid_plural`
        appears 0 times in both .po files and the compiled catalog has 0 tuple
        keys), so a plain msgid lookup is exact here. The assertion below
        reports a plural entry appearing later instead of mistaking it for a
        missing message."""
        with open(RU_MO, "rb") as f:
            mo = gettext.GNUTranslations(f)
        catalog = getattr(mo, "_catalog", {})
        assert catalog, "the compiled .mo has an empty catalog"

        plural_keys = sorted(k for k in catalog if isinstance(k, tuple))
        assert not plural_keys, (
            "the compiled .mo now carries plural entries, which this test keys "
            "by plain msgid — extend it (see the docstring): "
            + repr(plural_keys[:5]))

        missing = []
        stale = []
        for mid, mstr in _po_entries(RU_PO):
            if mid not in catalog:
                missing.append(mid)
            elif catalog[mid] != mstr:
                stale.append((mid, mstr, catalog[mid]))
        assert not missing, (
            "locales/ru/LC_MESSAGES/kicadstamp.po has translated entries missing "
            "from the compiled .mo — recompile it (pybabel compile): " + repr(missing))
        assert not stale, (
            "the compiled .mo carries an OLDER translation than the .po — the "
            "edit was never recompiled (pybabel compile), and the user reads "
            "the .mo, not the .po — (msgid, .po msgstr, .mo msgstr): "
            + repr([(m[:60], a[:60], b[:60]) for m, a, b in stale]))
