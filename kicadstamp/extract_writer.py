# kicadstamp/extract_writer.py
"""Pure worker-side logic of ExtractDock's "extract template to file" flow —
moved out of gui/docks/extract.py (Phase 2 of the gui god-file decomposition,
see techdocs/handoff/handoff_2026_08_05_architecture_fixes_roadmap.md) so the
transformative/profile/placer-wiring logic lives in core and the dock stays a
thin collection of inputs + worker dispatch + finish handling.

Worker-thread contract (unchanged from ExtractDock._run_extract): board IPC +
file writes only, never touches a widget. Returns
{"messages": [...], "annotations": [...], "template_dict": {...}} on success,
or {"error": str} for the expected failure modes (an unexpected exception is
caught by the GUI's _LongOpWorker and reported through the failed signal
instead). template_dict is the raw {name: {vias/components/tracks/layer}}
extract_fn returned — added so ExtractDock can summarize which via/track
nets got auto-classified as net_from_role (see its _summarize_net_from_role),
without re-running the extractor just to inspect the result.

`extract_fn` is injectable (defaulting to the real extractor) so the GUI can
pass its own module-global through at call time — that is what lets the GUI
tests monkeypatch `gui.docks.extract.extract_template_from_selection` and
still have the worker use the fake.
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kicadstamp.config_writer import add_list_entry, display_path, merge_write, non_includable_keys
from kicadstamp.exceptions import PlacerError
from kicadstamp.i18n import _
from kicadstamp.template_extraction import extract_template_from_selection

logger = logging.getLogger(__name__)


def run_extract_to_file(adapter, *, name: str, params, items, net_template_role,
                        rule_nets, origin_kwargs: Dict[str, Any], target_path: Path,
                        save_profile: bool, profile_key: str, profile_path: Optional[Path],
                        placer_path: Optional[Path], raw_selection: bool = False,
                        extract_fn=extract_template_from_selection,
                        clone_placements: Optional[list] = None) -> Dict[str, Any]:
    """Run the extract-transform-write flow for one ExtractDock payload.

    Parameters mirror the GUI payload keys (see ExtractDock._collect_extract_inputs)
    so the dock's thin wrapper is a plain forward of the payload dict:
    adapter — the board adapter to extract from; name — cell/profile name;
    params/items/net_template_role/rule_nets — extractor inputs; origin_kwargs
    — origin_* kwargs forwarded to the extractor; target_path — the cells
    file; save_profile/profile_key/profile_path — optional extract_profiles
    write; placer_path — optional placer file to wire include: into.
    `extract_fn` is injectable (default: extract_template_from_selection).

    clone_placements — OPTIONAL list of CellPlacement-shaped dicts
    (name/cell/xy/rotation_deg/mirror/layer) written into the extracted cell's
    own clone_placements: section (Sub-placements, 2026-08-25): existing
    placements wholly covered by the extraction are referenced instead of
    copied flat, so the composite cell stays in sync with them on every apply.
    """
    annotations: List[Tuple[str, str, str]] = []
    try:
        template_dict = extract_fn(
            adapter, name, params=params,
            items=items, net_template_role=net_template_role,
            rule_nets=rule_nets,
            annotations=annotations, raw_selection=raw_selection, **origin_kwargs)
    except PlacerError as e:
        return {"error": str(e)}

    if clone_placements:
        # A pure-composite cell is legitimate (only clone_placements, no flat
        # content) — but a plain extract always produces at least the named
        # cell dict, so guard defensively for injected fakes in tests.
        cell_dict = template_dict.get(name)
        if isinstance(cell_dict, dict):
            cell_dict["clone_placements"] = clone_placements

    try:
        cell_overwritten = merge_write(
            target_path, {"cells": template_dict}, section="cells")
    except OSError as e:
        return {"error": _("Write failed: {error}").format(error=e)}

    messages = [_("{action} {name!r} in {path}").format(
        action=_("Overwrote") if cell_overwritten else _("Wrote"),
        name=name, path=target_path)]

    if save_profile:
        entry: Dict[str, Any] = {"output": display_path(target_path)}
        if profile_key != name:
            entry["name"] = name
        if params:
            entry["params"] = params
        if net_template_role:
            entry["net_template_role"] = net_template_role
        if rule_nets:
            entry["rule_nets"] = sorted(rule_nets)
        if raw_selection:
            entry["raw_selection"] = True
        for key, value in origin_kwargs.items():
            # Function kwargs (origin_component_role) vs. profile YAML
            # keys (origin_by_component_role) differ by "by_" — see
            # kicadstamp_cli.py's cmd_extract profile branch.
            entry[f"origin_by_{key[len('origin_'):]}"] = value
        try:
            profile_overwritten = merge_write(
                profile_path,
                {"extract_profiles": {profile_key: entry}}, section="extract_profiles")
        except OSError as e:
            return {"error": _("Cell written, but profile write failed: {error}").format(error=e)}
        messages.append(_("{action} profile {key!r} in {path}").format(
            action=_("overwrote") if profile_overwritten else _("wrote"),
            key=profile_key, path=profile_path))

    if placer_path is not None:
        try:
            if target_path != placer_path:
                rel = Path(os.path.relpath(target_path, placer_path.parent)).as_posix()
                if add_list_entry(placer_path, "include", rel):
                    messages.append(_("added {rel!r} to include: in {path}").format(
                        rel=rel, path=placer_path))
            if save_profile and profile_path != placer_path:
                bad_keys = non_includable_keys(profile_path)
                if bad_keys:
                    messages.append(
                        _("skipped adding to include: — {path} has root-config-only key(s) "
                          "{keys} that include: can't merge (move them to the Placer file "
                          "itself, or point Placer at this same file)")
                        .format(path=display_path(profile_path), keys=sorted(bad_keys)))
                else:
                    rel = Path(os.path.relpath(profile_path, placer_path.parent)).as_posix()
                    if add_list_entry(placer_path, "include", rel):
                        messages.append(_("added {rel!r} to include: in {path}").format(
                            rel=rel, path=placer_path))
        except OSError as e:
            messages.append(_("placer file wiring failed: {error}").format(error=e))

    return {"messages": messages, "annotations": annotations, "template_dict": template_dict}
