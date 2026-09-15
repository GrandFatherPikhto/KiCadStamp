# kicadstamp/constants.py

# --- Fields and roles ---
ROLE_FIELD_NAME = "Role"
# The second custom schematic field, alongside Role — physical instance/cluster
# (NOT native KiCad Group — deliberately, see chat discussion: we don't want to
# share data space with someone else's, still-unfinished multichannel
# mechanics in KiCad). Hierarchy ("Channel_1/1V2_PLL_PI_FILTER") is supported
# for free via segment-prefix comparison — a flat name without "/"
# simply degenerates to the special case of exact match.
CLUSTER_FIELD_NAME = "Cluster"

# --- Tolerances ---
POSITION_TOLERANCE_NM = 10_000       # 0.01 mm
ANGLE_TOLERANCE_DEG = 0.1
POSITION_TOLERANCE_MM = 0.01

# --- Pad shapes (the vocabulary of Pad.shape) ---
# Plain strings, so the vocabulary can be shared by the domain DTO
# (domain/board.py) and the geometry modules without importing kipy or each
# other: geometry/pad_area.py imports THIS module, and domain/board.py must not
# import geometry at all (geometry/__init__ -> thermal_grid -> domain.board is a
# cycle).
# kipy's PadStackShape PSS_* enum is mapped onto these in pad_from_kipy
# (plan_2026_09_15_pad_geometry_thermal_vias, Э1). The set below is the whole
# vocabulary; which of them carry an area of their own is decided by
# geometry/pad_area.py.
PAD_SHAPE_RECT = "rect"
PAD_SHAPE_ROUNDRECT = "roundrect"
PAD_SHAPE_CHAMFERED = "chamfered"
PAD_SHAPE_OVAL = "oval"
PAD_SHAPE_CIRCLE = "circle"
PAD_SHAPE_TRAPEZOID = "trapezoid"
PAD_SHAPE_CUSTOM = "custom"
PAD_SHAPE_UNKNOWN = "unknown"

# --- Default parameters ---
DEFAULT_BATCH_SIZE = 10
# IPC timeout for the kipy connection socket, in milliseconds. Fixed once, at
# socket-creation time (kipy's KiCadClient reads it in KiCad(timeout_ms=...) and
# KiCadClient.send() has no per-request override), so a change applies from the
# NEXT connection onwards. 5000 ms is a ~17x margin over the slowest call
# measured live on 2026-09-13 (287 ms for get_selected_items; the heaviest call,
# a full get_footprints over 325 footprints, came in at 164 ms and would grow to
# roughly 1.5 s on a board three times larger — still more than 3x headroom).
# The socket timeout is the ONLY bound on the worst-case queue stall of a stuck
# request, so keeping it at the old 20 s meant a 20 s freeze for no measured
# reason; 5 s bounds that stall five seconds while staying far above real calls.
DEFAULT_TIMEOUT_MS = 5000
# Default interval (ms) at which the GUI retries connecting to KiCad while it is
# NOT connected (MainWindow's slow poll timer). Kept next to DEFAULT_TIMEOUT_MS
# as the shared default; overridable via gui_state.json["reconnect_interval_ms"].
DEFAULT_RECONNECT_INTERVAL_MS = 5000
DEFAULT_LOG_DIR = "logs"

# --- Registry ---
SPOKE_LEVEL_ROLE_PLACEHOLDER = "__spoke__"