# gui/docks/instances_validation.py
"""What the Instances dialog asks BEFORE it writes a row — Qt-free (Н7/Н4 of
plan_2026_11_07_tree_instances_dialog_and_subtree).

Two questions, both answered from the SAME graph the Save will flush:

1. Н7 — "does the declaration still load?" The row list is merged over the
   file's section (`config_writer.merged_tree_instances`, the writer's own rule)
   and the expansion runs IN MEMORY (`expand_tree_instances` with a failures
   list). A refusal is shown in the dialog and NOTHING is written: the dialog
   must never be able to write a row the loader refuses — the live `switch_b`
   row did exactly that ("template 'switch_a' must be role-anchored OR
   self-anchored..." on the very next load).

2. Н4 — "will the instance have anything to place?" A WARNING (never a
   refusal): the row's sheet must exist in the project's sheet dictionary, and
   at least one cluster the template generates must be present on that sheet in
   the config. Cost and honesty: the config + `ctx.sheet_names`, no board read
   (the dialog is a config editor; the board may not be connected, and a UI
   thread read is forbidden by the door contract). A warning may be a false
   negative against the LIVE board, and that is accepted — a false REFUSAL
   would not be.
"""
from __future__ import annotations

from kicadstamp.config.loader import resolved_graph
from kicadstamp.config.tree_instance_checks import template_generated_clusters
from kicadstamp.config.tree_instances import expand_tree_instances
from kicadstamp.config_writer import merged_tree_instances
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.sheet_names import ROOT_SHEET

__all__ = ["expansion_refusal", "proposed_graph", "sheet_warnings"]


def proposed_graph(root_path, template: str, rows: list) -> dict:
    """The resolved graph WITH this template's rows already merged in — the data
    "as it will be after the write", never written anywhere."""
    data = resolved_graph(str(root_path))
    data["tree_instances"] = merged_tree_instances(
        data.get("tree_instances") or [], template, rows)
    return data


def expansion_refusal(data: dict, template: str, rows: list) -> str | None:
    """The loader's own refusal text for THIS template's proposed rows, or None.

    `data` must already carry the merged rows (`proposed_graph`). Refusals of
    OTHER templates' declarations are not this row's business — they are why the
    dialog was opened at all (Н8) and must not block an unrelated fix. A
    graph-wide refusal (a non-list section, duplicate names) is returned too: it
    is exactly what the next load would die on."""
    failures: list[dict] = []
    names = {r.get("name") for r in rows}
    try:
        expand_tree_instances(data, failures=failures)
    except ValidationError as e:
        return str(e)
    for failure in failures:
        if (failure.get("template") == template
                and failure.get("name") in names):
            return failure.get("error")
    return None


def sheet_warnings(rows: list, template_name: str, data: dict,
                   sheet_names: dict | None) -> list[str]:
    """One WARNING line per row that will probably place nothing (Н4).

    Two independent checks, each skipped when its evidence is absent:
      * the sheet must be one of the project's sheets (`ctx.sheet_names` values,
        plus the reserved root label) — skipped when the dictionary is empty
        (a profile without a schematic: nothing is known, nothing is claimed);
      * at least one cluster the template's placement records generate must be
        present on that sheet among the config's `entities`.
    """
    template = next((t for t in data.get("trees") or []
                     if isinstance(t, dict) and t.get("name") == template_name),
                    None)
    entities = [e for e in data.get("entities") or [] if isinstance(e, dict)]
    by_name = {e["name"]: e for e in entities
               if isinstance(e.get("name"), str)}
    template_clusters = (template_generated_clusters(
        template.get("nodes") or [], by_name) if template else set())
    known_sheets = {str(v) for v in (sheet_names or {}).values() if v}
    known_sheets.add(ROOT_SHEET)

    out: list[str] = []
    for row in rows:
        sheet = row.get("sheet")
        name = row.get("name")
        if sheet_names and sheet not in known_sheets:
            out.append(_("sheet {sheet!r} of instance {name!r} is not among the "
                         "project's sheets").format(sheet=sheet, name=name))
            continue
        if template_clusters:
            on_sheet = {e.get("cluster") for e in entities
                        if e.get("sheet") == sheet and e.get("cluster")}
            if not (on_sheet & template_clusters):
                out.append(_(
                    "no clusters of template {template!r} ({clusters}) are on "
                    "sheet {sheet!r} — the instance will place nothing there")
                    .format(template=template_name,
                            clusters=", ".join(sorted(template_clusters)),
                            sheet=sheet))
    return out
