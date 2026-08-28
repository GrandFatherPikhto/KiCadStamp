# kicadstamp/cloner/plan.py
"""
Phase 3 step 3.1/3.2 — auto-generate the `clone_placements:` block for a
channel clone WITHOUT manually writing params:/nets:.

`plan_clone_placements()` turns the file-based snapshot (netlist TwinMap + the
.kicad_pcb parse) into a ready `clone_placements:` dict:
  - cluster/cell/xy/anchor — positional fields;
  - params: {channel: N} — so the cell's {channel} net templates resolve;
  - nets: {role: net} — the role→net mapping AUTO-derived per target channel
    from the source channel's real pad nets via TwinMap.twin_net (local nets
    are prefix-remapped /Channel_0/... -> /Channel_N/...; a role with exactly
    one global net keeps it as-is; bridging/multi-net roles are deliberately
    left to the cell's own auto-derived net_template — never a silent guess).

`verify_channel_net_mapping()` is the net part of trace transfer in production:
it runs net_matching (Kuhn + Tarjan SCC, Phase 0) on the two channels'
role<->net evidence. SCC ambiguity is DIAGNOSTIC, never a stop (safe-default
thesis: every member of an ambiguous SCC is a formally correct answer) —
exactly the plan rule "SCC = диагностика, не стоп".

The role<->net graph is built from the same per-footprint Role + pad nets the
PCB parser now captures (see PcbFootprint.role / .pad_nets).
"""

import logging
import re
from typing import Any

from ..exceptions import ValidationError
from ..i18n import _
from ..net_matching import Graph, match_template_to_target
from .models import TwinMap
from .pcb import PcbDocument

logger = logging.getLogger(__name__)

_CHANNEL_NUM_RE = re.compile(r'^Channel_(?P<n>\d+)$')


def channel_uuid_to_name(twin: TwinMap) -> dict[str, str]:
    """{channel sheet uuid: channel name} — footprints' path[0] is the channel
    sheet uuid (pcb.py's channel_uuid), the netlist knows which uuid belongs to
    which Channel_N (ChannelInfo.sheet_uuid)."""
    return {info.sheet_uuid: name for name, info in twin.channels.items()}


def _channel_number(name: str) -> int | None:
    m = _CHANNEL_NUM_RE.match(name)
    return int(m.group('n')) if m else None


def footprints_by_channel(doc: PcbDocument, uuid_to_name: dict[str, str]) -> dict[str, list]:
    """{channel name: [PcbFootprint]} — a footprint belongs to the channel whose
    sheet uuid is path[0]."""
    out: dict[str, list] = {}
    for fp in doc.footprints:
        ch = uuid_to_name.get(fp.channel_uuid)
        if ch is not None:
            out.setdefault(ch, []).append(fp)
    return out


def role_to_nets_for_channel(fps: list) -> dict[str, list[str]]:
    """{role: [pad nets in pad order]} for one channel's footprints. A role
    must be unique within a channel (same rule as extract); on a duplicate the
    FIRST occurrence wins and a warning is logged."""
    out: dict[str, list[str]] = {}
    for fp in fps:
        if not fp.role:
            continue
        if fp.role in out:
            logger.warning(_("duplicate role {role!r} on {ref} in one channel — "
                             "clone-plan uses the first occurrence")
                           .format(role=fp.role, ref=fp.ref))
            continue
        out[fp.role] = list(fp.pad_nets)
    return out


def _designated_source_net(role_nets: dict[str, list[str]], role: str,
                           local_nets: set[str]) -> str | None:
    """The one remappable net of a role on the source channel:
      - exactly ONE local (channel-scoped) net wins -> twin_net remaps it;
      - else exactly one DISTINCT global net -> kept as-is (not remapped);
      - else None — bridging/multi-net role: the cell's own auto-derived
        net_template + params are responsible, never a silent guess."""
    pads = role_nets.get(role) or []
    local = [n for n in pads if n in local_nets]
    if len(local) == 1:
        return local[0]
    if not local:
        distinct_global = sorted({n for n in pads if n})
        if len(distinct_global) == 1:
            return distinct_global[0]
    return None


def _channel_bbox_origin(fps: list) -> tuple[float, float] | None:
    """Lower-left corner of the channel's footprint positions (mm) — the
    deterministic default for the clone's absolute xy (mirrors extract's
    bbox-origin convention). The caller can override with an explicit xy."""
    if not fps:
        return None
    return min(f.x_mm for f in fps), min(f.y_mm for f in fps)


def verify_channel_net_mapping(source_role_nets: dict[str, list[str]],
                               target_role_nets: dict[str, list[str]],
                               source_channel: str, target_channel: str) -> list[str]:
    """Phase 3 step 3.2: run net_matching (Kuhn + Tarjan SCC, Phase 0) on the
    two channels' role<->net evidence. Returns diagnostics:
      - a ValidationError from net_matching (roles/nets not isomorphic) — the
        prefix-remap produced nets that don't exist on the target;
      - one entry per SCC ambiguity group (electrically symmetric roles) —
        every assignment in the group is a valid answer; disambiguate by
        sheet/cluster when several instances share these nets.
    NEVER raises — SCC is a diagnostic layer, not a hard human stop."""
    diagnostics: list[str] = []
    if not source_role_nets or not target_role_nets:
        return diagnostics

    def _graph(role_nets: dict[str, list[str]]) -> Graph:
        return Graph(
            roles={role: {i + 1: net for i, net in enumerate(pads)}
                   for role, pads in role_nets.items()},
        )

    try:
        _mapping, ambiguous = match_template_to_target(
            _graph(source_role_nets), _graph(target_role_nets))
    except ValidationError as exc:
        diagnostics.append(_("net_matching {src} -> {dst}: {error}")
                           .format(src=source_channel, dst=target_channel, error=exc))
        return diagnostics
    for group in ambiguous:
        diagnostics.append(
            _("net_matching {src} -> {dst}: electrically symmetric roles in group "
              "{roles} — any assignment is valid; disambiguate by sheet/cluster "
              "when several instances share these nets")
            .format(src=source_channel, dst=target_channel,
                    roles=", ".join(sorted(group))))
    return diagnostics


def plan_clone_placements(*, twin: TwinMap, doc: PcbDocument,
                          source_channel: str,
                          cell: str,
                          cluster: str | None = None,
                          xy: tuple[float, float] | None = None,
                          anchor_role: str | None = None,
                          anchor_sheet: str | None = None,
                          target_channels: list[str] | None = None,
                          ) -> tuple[list[dict[str, Any]], list[str]]:
    """Generate a ready `clone_placements:` LIST of records, one per target
    channel (default: every other channel of the twin map), WITHOUT manual
    params:/nets:. `clone_placements:` is a LIST section in the config schema —
    the generated records are exactly what the s-expr serializer and the
    loader iterate.

    Per record:
      - name: the target channel — the save/--only identity, unique per entry
        (kept distinct even when a shared --cluster tag is used);
      - cluster: explicit base tag if given, else the target channel name;
      - cell: the cell (template) name to clone;
      - xy: explicit absolute position if given, else the target channel's own
        footprint bbox lower-left corner (deterministic default; override it);
      - anchor_role/anchor_sheet: only when explicitly requested;
      - params: {channel: N} when the target channel is named Channel_N;
      - nets: {role: net} auto-derived via TwinMap + (verified by) net_matching.

    Returns (placements, diagnostics); diagnostics are the net_matching report
    (SCC ambiguity / non-isomorphism) and are logged as warnings by the caller —
    they never make the plan fail.
    """
    diagnostics: list[str] = []
    uuid_to_name = channel_uuid_to_name(twin)
    fps_by_ch = footprints_by_channel(doc, uuid_to_name)

    if source_channel not in twin.channels:
        raise ValueError(_("Channel {channel!r} not found; available: {avail}")
                         .format(channel=source_channel, avail=sorted(twin.channels)))
    if source_channel not in fps_by_ch:
        raise ValueError(_("no footprints for channel {channel!r} on the board")
                         .format(channel=source_channel))

    targets = list(target_channels) if target_channels is not None else \
        sorted(c for c in twin.channels if c != source_channel)
    for target in targets:
        if target not in twin.channels:
            raise ValueError(_("target channel {channel!r} not found; available: {avail}")
                             .format(channel=target, avail=sorted(twin.channels)))

    src_local = set(twin.channels[source_channel].local_nets)
    src_role_nets = role_to_nets_for_channel(fps_by_ch[source_channel])
    src_fp_by_role = {fp.role: fp for fp in fps_by_ch[source_channel] if fp.role}

    placements: list[dict[str, Any]] = []
    for target in targets:
        tgt_fps = {fp.ref: fp for fp in fps_by_ch.get(target, [])}
        nets: dict[str, str] = {}
        for role in sorted(src_role_nets):
            src_fp = src_fp_by_role.get(role)
            if src_fp is None:
                continue
            twin_ref = twin.twin_ref(src_fp.ref, source_channel, target)
            if twin_ref is None or twin_ref not in tgt_fps:
                continue  # incomplete twin group — not clonable by mapping
            net = _designated_source_net(src_role_nets, role, src_local)
            if net is None:
                continue  # bridging/multi-net — cell net_template handles it
            nets[role] = twin.twin_net(net, source_channel, target)

        entry: dict[str, Any] = {"name": target, "cluster": cluster or target,
                                 "cell": cell}
        if xy is not None:
            entry["xy"] = [round(float(xy[0]), 4), round(float(xy[1]), 4)]
        else:
            origin = _channel_bbox_origin(fps_by_ch.get(target, []))
            if origin is not None:
                entry["xy"] = [round(origin[0], 4), round(origin[1], 4)]
        if anchor_role:
            entry["anchor_role"] = anchor_role
            if anchor_sheet:
                entry["anchor_sheet"] = anchor_sheet
        num = _channel_number(target)
        if num is not None:
            entry["params"] = {"channel": num}
        if nets:
            entry["nets"] = nets
        placements.append(entry)

        diagnostics += verify_channel_net_mapping(
            src_role_nets, role_to_nets_for_channel(fps_by_ch.get(target, [])),
            source_channel, target)

    return placements, diagnostics


def clone_placements_to_dict(placements: list[dict]) -> dict[str, Any]:
    """Wrap the generated records under a `clone_placements:` top-level key,
    ready to paste into (or merge with) the main config — the exact shape the
    config loader expects for the LIST section."""
    return {"clone_placements": placements}
