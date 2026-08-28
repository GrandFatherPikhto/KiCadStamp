# kicadstamp/cloner/extract.py
"""
extract-channel: snapshot of a channel in YAML — components with positions,
tracks, vias, twin map, and summary. This is the cloner's "eyes": before any
recording it shows exactly what will be cloned and what will be left behind
(foreign copper of global nets inside the channel bounds).
"""

import logging
from typing import Any

import yaml

from .netlist import parse_netlist, build_twin_map
from .pcb import PcbDocument
from .models import TwinMap, ChannelPcbSnapshot
from ..i18n import _

logger = logging.getLogger(__name__)


def snapshot_to_dict(snap: ChannelPcbSnapshot, twin: TwinMap) -> dict[str, Any]:
    ch = snap.channel
    others = [c for c in sorted(twin.channels) if c != ch]

    def twins_of(ref):
        return {o: twin.twin_ref(ref, ch, o) for o in others}

    d: dict[str, Any] = {
        'channel': ch,
        'channel_sheet_uuid': snap.channel_uuid,
        'summary': {
            'footprints': len(snap.footprints),
            'segments': len(snap.segments),
            'vias': len(snap.vias),
            'foreign_segments_in_bbox': len(snap.foreign_segments),
            'foreign_vias_in_bbox': len(snap.foreign_vias),
        },
        'footprints': [],
        'segments': [],
        'vias': [],
    }
    bb = snap.bbox_mm()
    if bb:
        d['bbox_mm'] = {'x0': round(bb[0], 3), 'y0': round(bb[1], 3),
                        'x1': round(bb[2], 3), 'y1': round(bb[3], 3)}

    for f in sorted(snap.footprints, key=lambda x: x.ref):
        entry = {
            'ref': f.ref,
            'lib_id': f.lib_id,
            'x_mm': round(f.x_mm, 4), 'y_mm': round(f.y_mm, 4),
            'rotation_deg': f.rotation_deg,
            'layer': f.layer,
            'uuid': f.uuid,
            'twins': twins_of(f.ref),
        }
        # Phase 3 step 3.1: Role + real pad nets per footprint — the file-based
        # evidence clone-plan turns into the clone_placements role->net mapping
        # (also handy in the snapshot itself).
        if f.role:
            entry['role'] = f.role
        if f.pad_nets:
            entry['nets'] = list(f.pad_nets)
        d['footprints'].append(entry)

    for s in snap.segments:
        d['segments'].append({
            'start': [round(s.start_x_mm, 4), round(s.start_y_mm, 4)],
            'end': [round(s.end_x_mm, 4), round(s.end_y_mm, 4)],
            'width_mm': s.width_mm, 'layer': s.layer,
            'net': s.net_name, 'uuid': s.uuid,
        })

    for v in snap.vias:
        d['vias'].append({
            'at': [round(v.x_mm, 4), round(v.y_mm, 4)],
            'size_mm': v.size_mm, 'drill_mm': v.drill_mm,
            'layers': v.layers, 'net': v.net_name, 'uuid': v.uuid,
        })

    if snap.foreign_segments or snap.foreign_vias:
        d['foreign_in_bbox'] = {
            'note': _("Copper of GLOBAL nets inside the channel bounds: not included in the clone; "
                      "channel connections to common rails are made deliberately."),
            'segment_nets': sorted({s.net_name for s in snap.foreign_segments}),
            'via_nets': sorted({v.net_name for v in snap.foreign_vias}),
        }
    return d


def extract_channel(net_path: str, pcb_path: str, channel: str,
                    output_yaml: str) -> dict[str, Any]:
    comps, local_by_ch, _global_nets = parse_netlist(net_path)
    twin = build_twin_map(comps, local_by_ch)
    if channel not in twin.channels:
        raise ValueError(_("Channel {channel!r} not found; available: {avail}")
                         .format(channel=channel, avail=sorted(twin.channels)))

    doc = PcbDocument(pcb_path)
    snap = doc.snapshot_channel(channel, twin.channels[channel].sheet_uuid)
    d = snapshot_to_dict(snap, twin)

    with open(output_yaml, 'w', encoding='utf-8') as f:
        yaml.safe_dump(d, f, allow_unicode=True, sort_keys=False, width=100)
    logger.info(_("Snapshot of {channel} written: {output}").format(channel=channel, output=output_yaml))
    return d
