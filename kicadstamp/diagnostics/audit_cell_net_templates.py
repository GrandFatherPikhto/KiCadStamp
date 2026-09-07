#!/usr/bin/env python3
"""audit_cell_net_templates.py — find Cells whose component net_template is a
HARDCODED absolute net path while the same Cell is reused across sheets.

Why this matters (live bug, Denis 2026-09-07, see
techdocs/handoff/deepseek/plan_2026_09_07_cell_net_template_audit.md): a Cell's
TemplateComponentSlot.net_template may be an ABSOLUTE hierarchical net path
like "/Channel_0/DAC/+3V3_AVDD". When the Cell is used by Entities on more
than one sheet (Channel_0 AND Channel_1 — the latter typically materialized by
a `tree_instances:` declaration), that literal silently pins the role to ONE
sheet's net: role resolution for the OTHER instance then finds — and moves —
the wrong, real component, WITHOUT any ambiguity error (the locked sheet's net
physically exists, so the search is not ambiguous — it is just wrong).

Unlike `sheet`/`cluster` (which tree_instances substitution rewrites on every
generated Entity copy), `net_template` lives at the Cell level and is never
sheet-parameterized, so every instance of a reused Cell shares the literal.

Read-only: loads each config through the standard
`kicadstamp.config.load_config` (no writing back, no live KiCad needed — the
whole audit is a pure pass over cfg.cells / cfg.entities). FIXING a flagged
Cell (replacing the literal with net_from_role / net_from_role_pad, or
re-tagging the Cluster on the schematic) is a data decision for the profile
owner, not something this script does.

Scope note (deliberate, per plan §1.4): the audit does NOT walk
cfg.tree_instances / cfg.trees separately. load_config() already runs
expand_tree_instances() internally (config/loader.py), so materialized Entity
copies ARE present in cfg.entities — a Cell used on the new sheet via a
tree_instance therefore already shows >= 2 distinct sheets here and is caught
by the plain len(sheets) >= 2 filter below.

Exit code is ALWAYS 0 — this is a human-facing diagnostic, not a CI gate.

Run (one or more profiles):
    python -m kicadstamp.diagnostics.audit_cell_net_templates \
        profiles/3ch-awg-tia-v103/config.sexp
"""
from __future__ import annotations

import io
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.config import load_config  # noqa: E402


@dataclass
class Finding:
    """One flagged Cell component: role whose net_template is a hardcoded
    absolute net path, pinned to a specific sheet, while the Cell is used by
    Entities on more than one distinct sheet.

    locked_sheet — the leading sheet segment of net_template (e.g.
        '/Channel_0/DAC/+3V3_AVDD' -> 'Channel_0'): the sheet the literal is
        actually hardcoded to. None when the string has no usable second
        segment (cannot happen for a '/'-prefixed path, kept for safety).
    sheets — sorted list of distinct sheets the Cell is used on."""

    cell: str
    role: str
    net_template: str
    locked_sheet: str | None
    sheets: list[str]


def _leading_sheet_segment(net_template: str) -> str | None:
    """The leading sheet segment of an absolute net path '/Sheet/Group/Signal'
    (split('/')[1], same idea as tree_instances.py's _substitute_net_sheet —
    deliberately nothing is imported from there). None for a malformed path
    (empty second segment or a root-level net with no sheet)."""
    parts = net_template.split("/")
    if len(parts) >= 2 and parts[0] == "" and parts[1]:
        return parts[1]
    return None


def find_hardcoded_net_templates(cfg) -> list[Finding]:
    """Core audit over a loaded Config.

    For every Cell referenced by Entities on >= 2 DISTINCT sheets, flag every
    component whose net_template starts with '/' (an absolute net path pinned
    to one concrete sheet) — the exact reuse pattern that makes a hardcoded
    literal dangerous: on any other sheet the role can only ever resolve the
    locked sheet's net. A Cell used on a single sheet (or not used at all) is
    deliberately NOT reported even with an absolute net_template — there is no
    cross-sheet reuse for the literal to corrupt (plan §2 regression).

    Returns findings grouped by Cell, each Cell's findings in component order.
    """
    cell_sheets: dict[str, set[str]] = defaultdict(set)
    for ent in cfg.entities:
        if ent.cell and ent.sheet:
            cell_sheets[ent.cell].add(ent.sheet)

    findings: list[Finding] = []
    for cell_name in sorted(cell_sheets):
        sheets = cell_sheets[cell_name]
        if len(sheets) < 2:
            # single-sheet usage (or none): no cross-sheet reuse to corrupt
            continue
        cell = cfg.cells.get(cell_name)
        if cell is None:
            continue
        for comp in cell.components:
            net_template = comp.net_template
            if not net_template or not net_template.startswith("/"):
                continue
            findings.append(Finding(
                cell=cell_name,
                role=comp.role,
                net_template=net_template,
                locked_sheet=_leading_sheet_segment(net_template),
                sheets=sorted(sheets),
            ))
    return findings


def format_findings(findings: list[Finding]) -> str:
    """Render the findings human-readably, grouped by Cell, exactly per the
    plan's §1.5 sample:

        Cell 'pif_avdd' — used on sheets: Channel_0, Channel_1
          role 'C_OUT_BULK': net_template hardcoded to '/Channel_0/DAC/+3V3_AVDD' (sheet-locked to Channel_0)
          role 'C_OUT_BYPASS': net_template hardcoded to '/Channel_0/DAC/+3V3_AVDD' (sheet-locked to Channel_0)

    Returns a single string ending WITHOUT a trailing newline. Empty findings
    produce the one-liner 'No hardcoded sheet-specific net_template found.'"""
    if not findings:
        return "No hardcoded sheet-specific net_template found."

    # group by cell, preserving component order within a cell
    by_cell: dict[str, list[Finding]] = {}
    for f in findings:
        by_cell.setdefault(f.cell, []).append(f)

    lines: list[str] = []
    for cell_name in sorted(by_cell):
        cell_findings = by_cell[cell_name]
        sheets_str = ", ".join(cell_findings[0].sheets)
        lines.append(f"Cell {cell_name!r} \u2014 used on sheets: {sheets_str}")
        for f in cell_findings:
            locked = (f" (sheet-locked to {f.locked_sheet})"
                      if f.locked_sheet else "")
            lines.append(
                f"  role {f.role!r}: net_template hardcoded to "
                f"{f.net_template!r}{locked}")
    return "\n".join(lines)


def audit_config_file(path: str) -> list[Finding]:
    """Load one config file (read-only) and return its findings."""
    cfg, _ctx = load_config(path)
    return find_hardcoded_net_templates(cfg)


def main(argv: list[str] | None = None) -> int:
    # force UTF-8 stdout (Windows console would otherwise pick cp1251 and
    # mangle the report) — same trick as the other diagnostics. Done inside
    # main(), NOT at module import, so importing this module in a test stays
    # side-effect-free (pytest capsys replaces sys.stdout).
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 0
    multiple = len(argv) > 1
    for path in argv:
        if multiple:
            print(f"\n=== {path} ===")
        try:
            findings = audit_config_file(path)
        except Exception as exc:  # noqa: BLE001 — diagnostic must not crash
            print(f"error loading {path}: {exc}")
            continue
        print(format_findings(findings))
    # the TextIOWrapper above buffers at Python level — flush so the report
    # is complete even when main() runs in-process (pytest fd-capture) or is
    # piped without a terminating process exit.
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
