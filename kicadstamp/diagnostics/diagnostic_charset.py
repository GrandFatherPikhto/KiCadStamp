#!/usr/bin/env python3
"""

This probe shows the value PHYSICALLY lying on the board, NOT the effective
value: the Role/Cluster override store is deliberately not applied here. The
two are different questions, and telling an empty board from a value that our
store overrides is exactly what this probe is for (plan Т2а).

diagnostics/diagnostic_charset.py — scans the entire board for Role/Cluster
fields (or any other via --fields) containing characters outside printable ASCII
(0x20-0x7E).

Reason: live find on 3CH-AWG-TIA — Role of three components (C3, C9, C170)
contained Cyrillic "С" (U+0421) instead of Latin "C" (U+0043) in place of the
first letter (C_IN_BYPASS -> С_IN_BYPASS etc.) — obviously, the keyboard layout
switched to Russian while editing the field value in Eeschema. Visually
indistinguishable in almost any font, but breaks exact role matching
(component_pool.py/clone_role_resolver.py compare Role strictly byte‑wise) —
a component with such a typo is not found by any rule that expects the
"correct" (Latin) role, and conversely, if you rename it in the cell to
Cyrillic — the typo is almost impossible to reproduce manually; it is only
detected by diffing character codes.

Run:
    python -m kicadstamp.diagnostics.diagnostic_charset
    python -m kicadstamp.diagnostics.diagnostic_charset --fields Role,Cluster,Value
    python -m kicadstamp.diagnostics.diagnostic_charset --verbose

Return code: 0 — no non‑ASCII characters found, 1 — at least one found.
Convenient as a standalone step before `apply` (like run_all_checks, but for
this class of typos there is no separate check in validation.py — it lives here
in diagnostics, not in the main pipeline, to avoid slowing down a normal apply
with an extra full pass over the board for a rare find).
"""
import argparse
import logging
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.i18n import _

logger = logging.getLogger(__name__)

DEFAULT_FIELDS = [ROLE_FIELD_NAME, CLUSTER_FIELD_NAME]


def find_non_ascii(value: str):
    """Returns [(index, char, codepoint, unicode_name), ...] for each character
    outside printable ASCII (0x20-0x7E) — deliberately narrow range, not simply
    "not ASCII": tabs/newlines in single‑line Role/Cluster fields are also
    invalid, but here we are specifically looking for character substitution
    from another alphabet, not whitespace junk."""
    bad = []
    for i, ch in enumerate(value):
        if not (0x20 <= ord(ch) <= 0x7E):
            try:
                name = unicodedata.name(ch)
            except ValueError:
                name = _("NO NAME")
            bad.append((i, ch, ord(ch), name))
    return bad


def main():
    ap = argparse.ArgumentParser(
        description=_("Search for non‑ASCII characters (e.g. Cyrillic homoglyphs) "
                      "in Role/Cluster fields across the entire board")
    )
    ap.add_argument("--fields", default=",".join(DEFAULT_FIELDS),
                    help=_("comma‑separated, no spaces (default: {default})")
                    .format(default=",".join(DEFAULT_FIELDS)))
    # 20 s ON PURPOSE (Э4, plan_2026_09_13_timeout_sweep) — deliberately NOT
    # DEFAULT_TIMEOUT_MS: this one scans every field of every footprint on the
    # whole board, a heavy batch read rather than the GUI's per-call budget.
    ap.add_argument("--timeout-ms", type=int, default=20000,
                    help=_("IPC timeout in ms"))
    ap.add_argument("--verbose", action="store_true",
                    help=_("print clean fields as well"))
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]

    # shows what PHYSICALLY lies on the board — the override store is NOT applied
    adapter = create_board_adapter(timeout_ms=args.timeout_ms, use_store=False)
    adapter.refresh_board()
    footprints = adapter.get_footprints()

    findings = []
    for fp in footprints:
        ref = fp.reference_field.text.value
        for field in fields:
            value = adapter.get_field_value(fp, field)
            if not value:
                continue
            bad = find_non_ascii(value)
            if bad:
                findings.append((ref, field, value, bad))
            elif args.verbose:
                logger.debug(_("{ref}.{field} = {value!r} — clean")
                             .format(ref=ref, field=field, value=value))

    print(_("\nChecked footprints: {count}, fields per component: {fields}")
          .format(count=len(footprints), fields=fields))

    if not findings:
        print(_("No non‑ASCII characters found. All clean."))
        return 0

    print(_("\n=== FOUND {count} field(s) with suspicious characters ===\n")
          .format(count=len(findings)))
    for ref, field, value, bad in findings:
        print(_("{ref}.{field} = {value!r}").format(ref=ref, field=field, value=value))
        for i, ch, cp, name in bad:
            print(_("    position {pos}: {ch!r} U+{cp:04X} ({name})")
                  .format(pos=i, ch=ch, cp=cp, name=name))
    print(
        _("\nThis is not necessarily an error (there may be legitimate Unicode "
          "values in other fields), but for Role/Cluster pure ASCII‑Latin is expected — "
          "role comparisons in component_pool.py/clone_role_resolver.py are case‑sensitive "
          "and byte‑wise; a homoglyph from another alphabet will never match anything. "
          "Fix in Eeschema (Symbol Properties -> erase and retype the value with "
          "the English layout), then Update PCB from Schematic.")
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())