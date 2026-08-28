# kicadstamp/cloner/models.py
"""
Channel cloner models. All file‑based: source of truth is .kicad_pcb /
.kicad_sch / .net, no IPC (see issue #24966 and why writing through the API
is a gamble). Coordinates are in mm, as everywhere in the project.
"""

from dataclasses import dataclass, field



@dataclass
class NetlistComponent:
    """Component from the netlist with hierarchical addressing."""
    ref: str
    value: str
    footprint: str
    sheet_names: str      # "/Channel_0/DAC Sheet/"
    sheet_tstamps: str    # "/channel-uuid/sublist-uuid/"
    uuid: str             # in‑schematic uuid of the symbol (tail of tstamps)

    @property
    def channel(self) -> str | None:
        parts = self.sheet_names.strip("/").split("/")
        return parts[0] if parts and parts[0].startswith("Channel") else None

    @property
    def inner_path(self) -> str:
        """Path inside the channel — common to twins of all channels."""
        parts = self.sheet_names.strip("/").split("/")
        return "/" + "/".join(parts[1:]) if len(parts) > 1 else "/"

    @property
    def inner_key(self) -> str:
        """Twin key: inner path + symbol uuid from the sheet template."""
        return f"{self.inner_path}#{self.uuid}"


@dataclass
class ChannelInfo:
    """Channel instance: name, root sheet uuid, template file."""
    name: str             # "Channel_0"
    sheet_uuid: str       # first segment of sheet_tstamps
    components: dict[str, NetlistComponent] = field(default_factory=dict)  # inner_key -> comp
    local_nets: list[str] = field(default_factory=list)


@dataclass
class TwinMap:
    """
    Component and net mapping between channels.
    components[inner_key][channel_name] -> ref
    """
    channels: dict[str, ChannelInfo]
    components: dict[str, dict[str, str]]

    def twin_ref(self, ref: str, src_ch: str, dst_ch: str) -> str | None:
        for _, by_ch in self.components.items():
            if by_ch.get(src_ch) == ref:
                return by_ch.get(dst_ch)
        return None

    def twin_net(self, net: str, src_ch: str, dst_ch: str) -> str:
        """Local net → twin net; global nets are returned as‑is."""
        prefix = f"/{src_ch}/"
        if net.startswith(prefix):
            return f"/{dst_ch}/" + net[len(prefix):]
        return net


@dataclass
class PcbFootprint:
    uuid: str
    ref: str
    lib_id: str
    path: str             # "/ch_uuid/sub_uuid/comp_uuid"
    x_mm: float
    y_mm: float
    rotation_deg: float
    layer: str
    # Custom Role field (the same role the extract/placement pipeline reads
    # from the live adapter) — Phase 3 step 3.1: needed to auto-generate the
    # clone_placements role->net mapping from the file-based snapshot, without
    # IPC. None when the footprint has no Role property (e.g. non-channel
    # footprints, or a board not yet tagged).
    role: str | None = None
    # Real pad nets, in pad order ("" pads skipped? no — kept, they simply
    # carry no net). Used to derive each role's expected net on the target
    # channel via TwinMap.twin_net.
    pad_nets: list[str] = field(default_factory=list)

    @property
    def channel_uuid(self) -> str | None:
        parts = self.path.strip("/").split("/")
        return parts[0] if parts and parts[0] else None


@dataclass
class PcbSegment:
    uuid: str
    start_x_mm: float
    start_y_mm: float
    end_x_mm: float
    end_y_mm: float
    width_mm: float
    layer: str
    net_id: int
    net_name: str


@dataclass
class PcbVia:
    uuid: str
    x_mm: float
    y_mm: float
    size_mm: float
    drill_mm: float
    layers: list[str]
    net_id: int
    net_name: str


@dataclass
class ChannelPcbSnapshot:
    """Snapshot of a channel on the board: what will be cloned."""
    channel: str
    channel_uuid: str
    footprints: list[PcbFootprint] = field(default_factory=list)
    segments: list[PcbSegment] = field(default_factory=list)
    vias: list[PcbVia] = field(default_factory=list)
    # Global (non‑channel) segments/vias inside the channel bbox — candidates
    # for manual resolution (GND stitching, etc.), not included in clone v1:
    foreign_segments: list[PcbSegment] = field(default_factory=list)
    foreign_vias: list[PcbVia] = field(default_factory=list)

    def bbox_mm(self):
        xs, ys = [], []
        for f in self.footprints:
            xs.append(f.x_mm); ys.append(f.y_mm)
        for s in self.segments:
            xs += [s.start_x_mm, s.end_x_mm]; ys += [s.start_y_mm, s.end_y_mm]
        for v in self.vias:
            xs.append(v.x_mm); ys.append(v.y_mm)
        if not xs:
            return None
        return (min(xs), min(ys), max(xs), max(ys))