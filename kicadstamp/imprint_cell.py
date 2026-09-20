# kicadstamp/imprint_cell.py
"""Imprint -> Cell: the PURE half of the imprint page's "Convert to cell" button.

Plan: `plan_2026_09_18_scheme_list_to_cell_and_capture.md` (Д2, decisions
Р12/Р20); design `design_2026_09_17_spoke_cell_editing.md` §10а.

What the conversion is, and what it is NOT:

  * it builds a `cells:` ENTRY out of one recorded imprint plus the Roles the
    user filled in on the imprint page — components, vias and tracks come
    straight from the record (they are already offsets from the region centre,
    which is the same local frame a Cell uses), the role of every component
    comes from the table, and the imprint's one cluster becomes the cell's
    address (Р20: a cell is cloned by its cluster, so one imprint = one cluster);
  * it writes NOTHING and decides nothing about the board: no adapter, no file,
    no Qt. The caller (gui/docks/imprint_refs_tab.py) validates, writes the
    entry into the ROOT config and says what it did;
  * it does NOT switch the Entity from `imprint:` to `cell:` — that is the
    dangerous half (the copper can double on the next redraw) and stays with
    stage 4's dry run (Д2's "два действия, не одно").

The refusals are collected, not raised: the button must be able to say EVERY
reason at once in one line (a missing Role AND a duplicated one AND no cluster),
and the config must stay untouched when any of them is present.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional

from .config.models import ImprintConfig
from .i18n import _

# Why a conversion is refused — KEYS, not sentences (same discipline as
# gui/role_table_model.py's SKIP_*): the caller turns them into the message.
PROBLEM_NO_ROLE = "no_role"
PROBLEM_DUPLICATE_ROLE = "duplicate_role"
PROBLEM_NO_CLUSTER = "no_cluster"


def cell_name_for_cluster(cluster: str) -> str:
    """The DEFAULT name of the cell an imprint becomes: the cluster tag slugged
    exactly the way `gui/docks/tree_from_selection.py::cluster_cell_name` slugs
    a cluster's cell (``DAC_BUF`` -> ``dac_buf``).

    Deliberately a second, tiny implementation instead of an import: that helper
    lives in the GUI package, and this module is the config side of the
    conversion (importing gui from kicadstamp is forbidden). The rule it
    mirrors is one line long and is pinned by a test against the GUI's own."""
    return re.sub(r"[^0-9a-zA-Z]+", "_", (cluster or "").strip().lower()).strip("_")


def effective_roles(refs: Iterable[str], roles_by_ref: Mapping[str, str]) -> dict:
    """{ref: role} with the empties dropped and the values stripped — what both
    the refusal check and the entry builder read (the caller passes the roles
    the table carries: the typed value, else the value in force)."""
    out: dict = {}
    for ref in (refs or ()):
        role = str((roles_by_ref or {}).get(ref) or "").strip()
        if role:
            out[str(ref)] = role
    return out


def imprint_cell_problems(refs: Iterable[str],
                          roles_by_ref: Mapping[str, str],
                          cluster: str) -> list[str]:
    """Every reason this imprint CANNOT become a cell — ready-to-show sentences,
    in a stable order (missing roles, duplicate roles, no cluster). Empty list =
    the conversion is allowed. NOTHING is written by the caller when it is
    non-empty (guard С2)."""
    refs = [str(r) for r in (refs or ())]
    roles = effective_roles(refs, roles_by_ref)
    problems: list[str] = []

    missing = [ref for ref in refs if ref not in roles]
    if missing:
        problems.append(
            _("no Role for {refs} — fill the Role column of this imprint")
            .format(refs=", ".join(missing)))

    by_role: dict = {}
    for ref, role in roles.items():
        by_role.setdefault(role, []).append(ref)
    for role in sorted(r for r, refs_of in by_role.items() if len(refs_of) > 1):
        problems.append(
            _("Role {role} is used by {refs} — a cell's roles must be unique "
              "(they identify its slots)").format(
                role=role, refs=", ".join(by_role[role])))

    if not (cluster or "").strip():
        problems.append(
            _("the imprint has no Cluster — a cell is cloned by its cluster, "
              "so one cluster per imprint is required"))

    return problems


def imprint_cell_entry(record: ImprintConfig, roles_by_ref: Mapping[str, str],
                       cluster: str) -> dict:
    """The `cells:` ENTRY one imprint becomes (NOT the {name: entry} dict).

    Geometry comes from the record verbatim: a recorded component carries
    ``offset_along_mm``/``offset_across_mm`` from the region centre and its
    ABSOLUTE angle (stored as ``rotation_deg``), which is exactly the pair a
    cell slot stores as offsets + ``angle_deg``. The record's ``pivot`` — the
    point that lands on a placement node — is the cell's mount point, so it is
    written as ``anchor_xy`` when it is not the default centre.

    The cell's own ``layer`` is deliberately NOT written (the default F.Cu
    applies): the side of the copper is a decision about the placement (mirror),
    and this conversion only turns a record's geometry into a template."""
    refs = [c.ref for c in record.components]
    roles = effective_roles(refs, roles_by_ref)

    components = []
    for c in record.components:
        components.append({
            "role": roles.get(str(c.ref), ""),
            "offset_along_mm": c.offset_along_mm,
            "offset_across_mm": c.offset_across_mm,
            "angle_deg": c.rotation_deg,
        })

    vias = [{
        "offset_along_mm": v.offset_along_mm,
        "offset_across_mm": v.offset_across_mm,
        "drill_mm": v.drill_mm,
        "diameter_mm": v.diameter_mm,
        "net": v.net,
    } for v in record.vias]

    tracks = []
    for t in record.tracks:
        row: dict = {
            "start_along_mm": t.start_along_mm,
            "start_across_mm": t.start_across_mm,
            "end_along_mm": t.end_along_mm,
            "end_across_mm": t.end_across_mm,
            "width_mm": t.width_mm,
            "net": t.net,
        }
        if t.layer:
            row["layer"] = t.layer
        tracks.append(row)

    entry: dict = {"components": components}
    if vias:
        entry["vias"] = vias
    if tracks:
        entry["tracks"] = tracks
    pivot = record.pivot or (0.0, 0.0)
    if tuple(pivot) != (0.0, 0.0):
        entry["anchor_xy"] = [float(pivot[0]), float(pivot[1])]
    return entry


def imprint_cell_payload(record: ImprintConfig, roles_by_ref: Mapping[str, str],
                         cluster: str,
                         name: Optional[str] = None) -> tuple:
    """(cell_name, {cell_name: entry}) — what the caller validates with the
    config loader and then merges into the ROOT config. Raises ValueError when
    the imprint cannot be converted (the caller checks the problems FIRST, so
    this is the "you forgot the check" guard, not the user-facing refusal)."""
    problems = imprint_cell_problems([c.ref for c in record.components],
                                     roles_by_ref, cluster)
    if problems:
        raise ValueError("; ".join(problems))
    cell_name = (name or cell_name_for_cluster(cluster)).strip()
    if not cell_name:
        raise ValueError("a cell needs a name")
    return cell_name, {cell_name: imprint_cell_entry(record, roles_by_ref, cluster)}


def cell_summary(entry: dict) -> dict:
    """(components, vias, tracks) counts — for the success line and the tests."""
    return {"components": len(entry.get("components") or ()),
            "vias": len(entry.get("vias") or ()),
            "tracks": len(entry.get("tracks") or ())}


def imprint_to_cell_plan(record: ImprintConfig,
                         roles_by_ref: Mapping[str, str],
                         cluster: str,
                         name: Optional[str] = None) -> dict:
    """One dict with everything the caller needs to report and to write:
    {"problems", "name", "cells"} — a PLAN, never a write. `cells` is empty when
    there are problems (guard С2: a refused conversion touches nothing)."""
    refs = [c.ref for c in record.components]
    problems = imprint_cell_problems(refs, roles_by_ref, cluster)
    if problems:
        return {"problems": problems, "name": None, "cells": {}, "summary": {}}
    cell_name, cells = imprint_cell_payload(record, roles_by_ref, cluster, name)
    return {"problems": [], "name": cell_name, "cells": cells,
            "summary": cell_summary(cells[cell_name])}
