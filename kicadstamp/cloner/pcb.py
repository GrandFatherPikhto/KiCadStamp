# kicadstamp/cloner/pcb.py
"""
Parsing .kicad_pcb: footprints (with hierarchical path), segments, vias,
net table — and extracting "everything of channel N".

Footprint selection key: the FIRST segment (path "/ch_uuid/.../comp_uuid"),
matched against the channel sheet uuid from the netlist. No name‑based matching.

Segments/vias belong to a channel BY NET: a net with prefix /Channel_N/ is
channel‑local. Elements of GLOBAL nets (GND, rails) inside the channel bbox
are collected separately (foreign_*): they are not included in the clone v1,
but we need to know about them — these are GND stitching and power feeds that
are made deliberately after cloning.
"""

import logging


from .sexp import load_file, children, child, atom, sval
from .models import PcbFootprint, PcbSegment, PcbVia, ChannelPcbSnapshot
from ..constants import ROLE_FIELD_NAME
from ..i18n import _

logger = logging.getLogger(__name__)


def _num(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class PcbDocument:
    def __init__(self, pcb_path: str):
        logger.info(_("Reading board: {path}").format(path=pcb_path))
        self.root = load_file(pcb_path)
        self.net_names: dict[int, str] = {}
        for n in children(self.root, 'net'):
            # (net 42 "name") — top‑level declarations only
            if len(n) >= 3:
                self.net_names[int(n[1])] = sval(n[2])
        self._net_ids_by_name = {v: k for k, v in self.net_names.items()}
        self.footprints = self._parse_footprints()
        self.segments = self._parse_segments()
        self.vias = self._parse_vias()
        # KiCad 10: there is no numeric net table in pcb any more; nets are by name
        used_nets = {s.net_name for s in self.segments} | {v.net_name for v in self.vias}
        used_nets.discard('')
        logger.info(_("Board: {fp} footprints, {seg} segments, {vias} vias; nets in copper: {nets}")
                    .format(fp=len(self.footprints), seg=len(self.segments),
                            vias=len(self.vias), nets=len(used_nets)))

    # --- low‑level parsers ---

    def _parse_footprints(self) -> list[PcbFootprint]:
        out = []
        for fp in children(self.root, 'footprint'):
            at = child(fp, 'at') or [None, 0, 0]
            ref = ''
            role = None
            for prop in children(fp, 'property'):
                if len(prop) < 3:
                    continue
                key = sval(prop[1])
                if key == 'Reference':
                    ref = sval(prop[2])
                elif key == ROLE_FIELD_NAME:
                    role = sval(prop[2]) or None
            # Phase 3 step 3.1: capture each pad's real net (in pad order),
            # for deriving the role's expected net on the target channel via
            # TwinMap.twin_net — the file-based clone_placements auto-fill.
            pad_nets = [self._net_ref(pad)[1] for pad in children(fp, 'pad')]
            pad_nets = [n for n in pad_nets if n]
            out.append(PcbFootprint(
                uuid=atom(fp, 'uuid', ''),
                ref=ref,
                lib_id=sval(fp[1]) if len(fp) > 1 else '',
                path=atom(fp, 'path', ''),
                x_mm=_num(at[1]),
                y_mm=_num(at[2]) if len(at) > 2 else 0.0,
                rotation_deg=_num(at[3]) if len(at) > 3 else 0.0,
                layer=atom(fp, 'layer', ''),
                role=role,
                pad_nets=pad_nets,
            ))
        return out

    def _net_ref(self, node) -> tuple[int, str]:
        """
        (net X): in KiCad 10 X can be a numeric id OR a string name
        (observed on real boards 10.0.4). Return (id, name); the missing half
        is recovered from the net table.
        """
        raw = atom(node, 'net', None)
        if raw is None:
            return 0, ''
        if isinstance(raw, int):
            return raw, self.net_names.get(raw, '')
        name = str(raw)
        return self._net_ids_by_name.get(name, 0), name

    def _parse_segments(self) -> list[PcbSegment]:
        out = []
        for s in children(self.root, 'segment'):
            st = child(s, 'start') or [None, 0, 0]
            en = child(s, 'end') or [None, 0, 0]
            net_id, net_name = self._net_ref(s)
            out.append(PcbSegment(
                uuid=atom(s, 'uuid', ''),
                start_x_mm=_num(st[1]), start_y_mm=_num(st[2]),
                end_x_mm=_num(en[1]), end_y_mm=_num(en[2]),
                width_mm=_num(atom(s, 'width', 0)),
                layer=atom(s, 'layer', ''),
                net_id=net_id,
                net_name=net_name,
            ))
        return out

    def _parse_vias(self) -> list[PcbVia]:
        out = []
        for v in children(self.root, 'via'):
            at = child(v, 'at') or [None, 0, 0]
            layers_node = child(v, 'layers') or []
            net_id, net_name = self._net_ref(v)
            out.append(PcbVia(
                uuid=atom(v, 'uuid', ''),
                x_mm=_num(at[1]), y_mm=_num(at[2]),
                size_mm=_num(atom(v, 'size', 0)),
                drill_mm=_num(atom(v, 'drill', 0)),
                layers=[sval(x) for x in layers_node[1:]] if layers_node else [],
                net_id=net_id,
                net_name=net_name,
            ))
        return out

    # --- channel extraction ---

    def snapshot_channel(self, channel_name: str, channel_uuid: str,
                         bbox_margin_mm: float = 1.0) -> ChannelPcbSnapshot:
        snap = ChannelPcbSnapshot(channel=channel_name, channel_uuid=channel_uuid)
        for f in self.footprints:
            if f.channel_uuid == channel_uuid:
                snap.footprints.append(f)

        net_prefix = f"/{channel_name}/"
        for s in self.segments:
            if s.net_name.startswith(net_prefix):
                snap.segments.append(s)
        for v in self.vias:
            if v.net_name.startswith(net_prefix):
                snap.vias.append(v)

        # Foreign objects inside channel bounds (GND stitching, power feeds)
        bbox = snap.bbox_mm()
        if bbox:
            x0, y0, x1, y1 = bbox
            x0 -= bbox_margin_mm; y0 -= bbox_margin_mm
            x1 += bbox_margin_mm; y1 += bbox_margin_mm
            inside = lambda x, y: x0 <= x <= x1 and y0 <= y <= y1
            for s in self.segments:
                if not s.net_name.startswith(net_prefix) and \
                        (inside(s.start_x_mm, s.start_y_mm) or inside(s.end_x_mm, s.end_y_mm)):
                    snap.foreign_segments.append(s)
            for v in self.vias:
                if not v.net_name.startswith(net_prefix) and inside(v.x_mm, v.y_mm):
                    snap.foreign_vias.append(v)

        logger.info(_("{channel}: {fp} footprints, {seg} segments, {vias} vias; "
                      "foreign in bbox: {fseg} segs, {fvia} vias")
                    .format(channel=channel_name, fp=len(snap.footprints),
                            seg=len(snap.segments), vias=len(snap.vias),
                            fseg=len(snap.foreign_segments), fvia=len(snap.foreign_vias)))
        return snap