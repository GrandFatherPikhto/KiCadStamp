# kicadstamp/net_trace_extract.py
"""
net_trace_extract.py — the `extract-net` library core: capture the copper of
ONE net (tracks + vias) as LOCAL offsets from an anchor pad, and upsert-write
it as a single flat `net_traces:` record.

This is deliberately NOT the selection-based extract (template_extraction.py):
the net is looked up over the WHOLE live board (never the mouse selection) and
the anchor footprint is resolved by Role field over the whole board too — the
same resolve_footprint_by_role search Rule/ClonePlacement use. The SAME anchor
fields serve both the extraction-time origin and the apply-time anchor (one
flat record, no Cell+ClonePlacement pair — see
techdocs/handoff/deepseek/plan_2026_08_21_net_traces.md §0).

Round-trip geometry: at extract, local = absolute - anchor (a plain
translation, no rotation — a net trace is a translation-following bundle, not
a rotatable cell). At apply (net_trace_planner.py), absolute = anchor + local
via the shared local_to_absolute with rotation_deg=0, so the copper keeps its
shape relative to the anchor wherever the anchor moves.
"""
import json
import logging
from pathlib import Path
from typing import Any

from .domain.geometry import BoardLayer

from .config import NetTrace
from .config.sexp_format import dict_to_sexp, sexp_to_dict
from .exceptions import (
    ValidationError,
    format_fatal_error,
    unknown_extension_config_error,
    yaml_removed_config_error,
)
from .placement.services.clone_role_resolver import resolve_footprint_by_role
from .utils.units import MM
from .i18n import _

logger = logging.getLogger(__name__)


def _layer_str(layer: BoardLayer) -> str:
    """BoardLayer -> 'F.Cu'/'B.Cu'."""
    return "B.Cu" if layer == BoardLayer.BL_B_Cu else "F.Cu"


def extract_net_trace(
    adapter,
    *,
    net: str,
    anchor_role: str,
    anchor_sheet: str | None = None,
    anchor_cluster: str | None = None,
    anchor_pad: str | None = None,
    sheet_names: dict[str, str] | None = None,
    retired: bool = False,
    skip: bool = False,
) -> NetTrace:
    """Capture one net's copper as a local-from-anchor NetTrace.

    Steps:
      1. resolve the anchor footprint over the WHOLE live board by Role
         (resolve_footprint_by_role — the shared Rule/ClonePlacement search,
         including the sheet/cluster/selection narrowing cascade); fatal if
         absent or ambiguous;
      2. anchor point = centre of anchor_pad when given, footprint centre
         otherwise (same semantics as ClonePlacement.anchor_pad / channel-copy's
         pivot anchor);
      3. every live track/via with net.name == net becomes a local
         (along/across) TemplateTrack/TemplateVia with the net written
         explicitly;
      4. fatal if the net sits on NO track/via at all (empty result is an
         explicit error, never a silent empty NetTrace).

    sheet_names — {uuid: Sheetname} for anchor_sheet narrowing (from a loaded
    config's RuntimeContext). Empty/None (the default — extract-net is a
    standalone CLI command with no config) means anchor_sheet never narrows,
    exactly like the rest of the project without schematic_dir.

    retired/skip — carried into the produced NetTrace. The CALLER is expected
    to read the existing record with the same net (if any) and pass its flags
    here: a re-extract must refresh the GEOMETRY but never silently clear the
    hand-set retired:/skip: (review fix 2026-08-21 — write_net_trace replaces
    the whole entry, so without this a re-extract would wipe them).
    """
    label = _("net_trace for net {net!r}").format(net=net)
    anchor_fp = resolve_footprint_by_role(
        adapter, anchor_role, anchor_sheet, anchor_cluster,
        sheet_names or {}, label=label,
    )

    if anchor_pad is not None:
        pad = adapter.get_pad_by_number(anchor_fp, anchor_pad)
        if pad is None:
            ref = anchor_fp.ref
            raise ValidationError(format_fatal_error(
                _("{label}: anchor pad {pad!r} not found on {ref}").format(
                    label=label, pad=anchor_pad, ref=ref),
                [_("--anchor-pad must name an existing pad number of the anchor "
                   "footprint; without it the footprint centre is used")]))
        anchor = pad.position
    else:
        anchor = anchor_fp.position

    tracks = []
    for t in adapter.get_tracks():
        t_net = t.net_name
        if t_net != net:
            continue
        tracks.append({
            "start_along_mm": round((t.start.x - anchor.x) / MM, 4),
            "start_across_mm": round((t.start.y - anchor.y) / MM, 4),
            "end_along_mm": round((t.end.x - anchor.x) / MM, 4),
            "end_across_mm": round((t.end.y - anchor.y) / MM, 4),
            "width_mm": round(t.width_mm, 4),
            "net": net,
            "layer": _layer_str(t.layer),
        })

    vias = []
    for v in adapter.get_vias():
        v_net = v.net_name
        if v_net != net:
            continue
        vias.append({
            "offset_along_mm": round((v.position.x - anchor.x) / MM, 4),
            "offset_across_mm": round((v.position.y - anchor.y) / MM, 4),
            "net": net,
            "drill_mm": round(v.drill_mm, 4),
            "diameter_mm": round(v.diameter_mm, 4),
        })

    if not tracks and not vias:
        raise ValidationError(format_fatal_error(
            _("net {net!r} has no copper (tracks or vias) on the board").format(net=net),
            [_("a net_traces record needs at least one track or via on the net — "
               "check the net name (are you looking at a LOCAL hierarchical net "
               "like '/Channel_0/...'?), or route some copper first")]))

    logger.info(_("Net {net!r}: {tracks} tracks, {vias} vias captured from anchor "
                  "({ax:.3f}, {ay:.3f}) mm (role {role!r})")
                .format(net=net, tracks=len(tracks), vias=len(vias),
                        ax=anchor.x / MM, ay=anchor.y / MM, role=anchor_role))

    from .config import load_template_track, load_template_via
    return NetTrace(
        net=net,
        anchor_role=anchor_role,
        anchor_sheet=anchor_sheet,
        anchor_cluster=anchor_cluster,
        anchor_pad=anchor_pad,
        tracks=[load_template_track(t) for t in tracks],
        vias=[load_template_via(v) for v in vias],
        retired=retired,
        skip=skip,
    )


def _template_track_dict(t) -> dict[str, Any]:
    """Clean YAML dict for one TemplateTrack — only the fields the round-trip
    needs (no all-None net_from_role/net_from_role_pad noise from asdict)."""
    return {
        "start_along_mm": t.start_along_mm,
        "start_across_mm": t.start_across_mm,
        "end_along_mm": t.end_along_mm,
        "end_across_mm": t.end_across_mm,
        "width_mm": t.width_mm,
        "net": t.net,
        "layer": t.layer,
    }


def _template_via_dict(v) -> dict[str, Any]:
    """Clean YAML dict for one TemplateVia (same reasoning as above)."""
    return {
        "offset_along_mm": v.offset_along_mm,
        "offset_across_mm": v.offset_across_mm,
        "net": v.net,
        "drill_mm": v.drill_mm,
        "diameter_mm": v.diameter_mm,
    }


def net_trace_to_dict(nt: NetTrace) -> dict[str, Any]:
    """NetTrace -> plain dict for YAML/JSON, omitting None/False fields (the
    same compact output style every other section uses)."""
    d: dict[str, Any] = {
        "net": nt.net,
        "anchor_role": nt.anchor_role,
    }
    for key in ("anchor_sheet", "anchor_cluster", "anchor_pad"):
        value = getattr(nt, key)
        if value is not None:
            d[key] = value
    d["tracks"] = [_template_track_dict(t) for t in nt.tracks]
    d["vias"] = [_template_via_dict(v) for v in nt.vias]
    if nt.retired:
        d["retired"] = True
    if nt.skip:
        d["skip"] = True
    return d


def read_net_trace_flags(path: str, net: str) -> tuple[bool, bool]:
    """(retired, skip) of the existing net_traces record with this net in
    `path`, or (False, False) when there is none (or the file can't be read).
    For a re-extract: refresh the geometry but never silently clear the
    hand-set retired:/skip: (review fix 2026-08-21 — see extract_net_trace's
    retired/skip params; write_net_trace replaces the whole entry by net).

    Parses by file suffix like every other reader (config/includes.py's
    _load_config_file / config_writer's _read_data): .sexp -> sexp_to_dict,
    .json -> JSON, anything else -> (False, False) — .yaml/.yml and unknown
    extensions are not supported config formats (2026-08-28,
    core_yaml_removal), so a re-extract of such a file cannot recover flags
    (the write path then fails loudly)."""
    p = Path(path)
    if not p.exists():
        return False, False
    try:
        text = p.read_text(encoding="utf-8")
        suffix = p.suffix.lower()
        if suffix == ".sexp":
            data = sexp_to_dict(text) or {}
        elif suffix == ".json":
            data = json.loads(text) or {}
        else:
            return False, False
    except (OSError, json.JSONDecodeError, ValidationError):
        return False, False
    for e in data.get("net_traces") or []:
        if isinstance(e, dict) and e.get("net") == net:
            return bool(e.get("retired")), bool(e.get("skip"))
    return False, False


def write_net_trace(output: str, nt: NetTrace) -> dict[str, Any]:
    """Upsert-write one NetTrace under a `net_traces:` list key in `output`
    (JSON or s-expr by file suffix), preserving everything else in the file —
    the same merge/upsert principle as extract_template: an existing entry
    with the same net is REPLACED in place, others are appended. Returns the
    written entry dict.

    Reads/writes go through config_writer's read_data/write_data (2026-09-01,
    plan project_save_model): previously this function opened the file
    directly, which BYPASSED both the read cache and the staged config working
    set — in the GUI a net trace written this way would land on disk while
    the rest of an Extract-tree was still staged, and the next Save would
    overwrite it. The helpers also select the format by extension exactly like
    this function used to (.json -> JSON, .sexp -> s-expr, anything else ->
    fatal with the same messages)."""
    from kicadstamp.config_writer import read_data, write_data
    output_path = Path(output)
    # Preserve the pre-existing fatal contract for unsupported extensions
    # (ValidationError, not the read helper's wrapped OSError): only VALID
    # .json/.sexp paths go through the staging-aware helpers below.
    suffix = output_path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        raise yaml_removed_config_error(output_path)
    if suffix not in (".json", ".sexp"):
        raise unknown_extension_config_error(output_path, suffix)

    existing: dict[str, Any] = read_data(output_path)

    net_traces = existing.setdefault('net_traces', [])
    entry = net_trace_to_dict(nt)
    replaced = False
    for i, e in enumerate(net_traces):
        if isinstance(e, dict) and e.get('net') == nt.net:
            net_traces[i] = entry
            replaced = True
            break
    if not replaced:
        net_traces.append(entry)
        logger.info(_("Net trace {net!r} appended to net_traces: in {output}")
                    .format(net=nt.net, output=output_path))
    else:
        logger.info(_("Net trace {net!r} replaced in net_traces: of {output}")
                    .format(net=nt.net, output=output_path))

    write_data(output_path, existing)
    return entry


__all__ = [
    "extract_net_trace",
    "net_trace_to_dict",
    "read_net_trace_flags",
    "write_net_trace",
]
