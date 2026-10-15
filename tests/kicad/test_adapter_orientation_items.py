#!/usr/bin/env python3
"""Watchdogs for the kipy orientation shim —
``kicadstamp/kicad/kipy_footprint_safety.py`` (plan_2026_10_15_footprint_3d_model_loss).

The disease (measured live 15.10.2026 on the ControllerSTM32F103C8T6 board): kipy 0.8.0's
``FootprintInstance.orientation`` setter REBUILDS ``definition.items`` out of the items it
knows how to rotate, so everything else — ``Footprint3DModel`` above all — is dropped, at a
ZERO delta as much as at any other angle. 73 of 77 footprints carried a 3D model and all 73
lost it, so EVERY move through the adapter (``_sync_dto_to_kipy`` set the angle on every
shift) damaged the board silently.

Style: REAL kipy objects, no board and no IPC — ``FootprintInstance``/``Field``/``Pad``/
``Footprint3DModel``/``BoardTextBox``/``BoardRectangle`` are built through kipy's own public
API, so the setter under audit is the real one. The two cells that must go through
``update_items`` (К3, К10) pass a DOMAIN ``Footprint`` DTO with its ``_kipy`` reference, which
is the only shape ``_sync_dto_to_kipy`` acts on — a raw kipy object there would be a green
cell that verifies nothing (``unwrap()`` passes it straight through).

К7 and К8 pin SOMEBODY ELSE'S behaviour on purpose: they go RED when kipy stops dropping
items, and that red is the signal to delete the shim (its docstring says so). They are not
requirements of ours.
"""
from unittest.mock import MagicMock

import pytest

from kipy.board_types import (
    BoardLayer,
    BoardRectangle,
    BoardShape,
    BoardText,
    BoardTextBox,
    Field,
    Footprint3DModel,
    FootprintInstance,
    Pad,
    unwrap as kipy_unwrap,
)
from kipy.geometry import Angle as KipyAngle, Vector2 as KipyVector2
from kipy.proto.board import board_types_pb2

from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer as DomainLayer, Vector2
from kicadstamp.kicad.adapter import KiCadBoardAdapter as Adapter
from kicadstamp.kicad.kipy_footprint_safety import set_footprint_orientation


# ── builders: real kipy objects, public API only ─────────────────────────────

def _field(name="Reference", value="U3"):
    field = Field()
    field.name = name
    field.text.value = value
    return field


def _model(filename="SOT-223.step"):
    model = Footprint3DModel()
    model.filename = filename
    model.visible = True
    model.opacity = 1.0
    return model


def _kipy_fp(extra_items=(), angle=0.0, ref="U3"):
    """A real kipy FootprintInstance holding a reference field plus `extra_items`.

    The angle is set BEFORE the items on purpose: the raw setter drops them, and this helper
    must hand out an object that still HAS them (otherwise every cell would start broken).
    """
    fp = FootprintInstance()
    fp.id.value = "uuid-U3"
    fp.position = KipyVector2.from_xy(0, 0)
    fp.orientation = KipyAngle.from_degrees(angle)
    ref_field = _field("Reference", ref)
    fp.reference_field = ref_field
    fp.definition.items = [ref_field, *extra_items]
    return fp


def _branch(item):
    """The SETTER BRANCH an item falls into — every BoardShape subclass is ONE branch, and
    that branch re-wraps the object (a rectangle turns into a BoardPolygon off the
    orthogonal angles), so cells about ORDER and losses compare branches, not classes."""
    return "BoardShape" if isinstance(item, BoardShape) else type(item).__name__


def _types(items):
    return [_branch(item) for item in items]


def _model_pose(model):
    return (model.filename, model.visible, model.opacity,
            (model.offset.x, model.offset.y, model.offset.z),
            (model.rotation.x, model.rotation.y, model.rotation.z))


def _adapter():
    """Adapter over a recording board. ``_write_risk_checked`` skips the real IPC probe
    (the same switch tests/kicad/test_kicad.py uses); nothing here touches a transaction."""
    adapter = Adapter.__new__(Adapter)
    adapter._board = MagicMock()
    adapter._write_risk_checked = True
    return adapter


def _dto(kipy_fp, angle_deg, x=0, y=0):
    """The DOMAIN DTO that update_items actually syncs (see the module docstring)."""
    return Footprint(
        ref="U3", uuid="uuid-U3",
        position=Vector2(x, y), angle_deg=angle_deg,
        layer=DomainLayer.BL_F_Cu, _kipy=kipy_fp,
    )


# ── К1: the field symptom — a rotation keeps the model and still rotates the rest ──

def test_rotation_keeps_the_3d_model_and_rotates_the_other_items():
    model = _model()
    pad = Pad()
    # The point that really loses a nanometre to kipy's truncation (see
    # tests/kicad/test_kipy_vector_rotate.py): the neighbouring axis has to outgrow the
    # computed one by ~1.8x before the float error survives int().
    pad.position = KipyVector2.from_xy(-1_800_000, 960_000)
    fp = _kipy_fp([model, pad, BoardRectangle()])

    pose_before = _model_pose(model)
    restored = set_footprint_orientation(fp, KipyAngle.from_degrees(90.0))

    assert restored == 1, "the model is the one item kipy's setter drops here"
    models = [i for i in fp.definition.items if isinstance(i, Footprint3DModel)]
    assert len(models) == 1
    assert models[0] is model, "the SAME object must come back, not a copy"
    assert _model_pose(models[0]) == pose_before, \
        "a 3D model is placed relative to the footprint: it must not be rotated"

    # The expected pad is written out HERE — (x, y) -> (y, -x) about the footprint origin.
    # This cell used to compare against kipy's OWN setter on a snapshot; after Ч2 that
    # baseline is the truncating arithmetic, one nanometre off, so keeping it would have
    # demanded the defect back (rule 33: the cell is restated, not weakened).
    shim_pad = next(i for i in fp.definition.items if isinstance(i, Pad))
    assert (shim_pad.position.x, shim_pad.position.y) == (960_000, 1_800_000)


# ── К2: a zero rotation never reaches the setter ─────────────────────────────

def test_zero_rotation_does_not_rebuild_the_item_list():
    fp = _kipy_fp([_model()])
    items_before = fp.definition.items

    restored = set_footprint_orientation(fp, KipyAngle.from_degrees(0.0))

    assert restored == 0
    assert fp.definition.items is items_before, \
        "the list object itself must be untouched — a rebuild is the whole problem"


# ── К3: a pure move through update_items (what the placer does on EVERY shift) ──

def test_a_pure_move_never_touches_the_item_list():
    kipy_fp = _kipy_fp([_model("U1.step")], angle=0.0)
    items_before = kipy_fp.definition.items

    _adapter().update_items([_dto(kipy_fp, angle_deg=0.0, x=1_000_000, y=2_000_000)])

    assert kipy_fp.definition.items is items_before
    assert any(isinstance(i, Footprint3DModel) for i in kipy_fp.definition.items), \
        "this is the live step-2 symptom: the move used to strip the model off every part"
    assert (kipy_fp.position.x, kipy_fp.position.y) == (1_000_000, 2_000_000)


# ── К4: the second dropped type (absent from the live board, proven by Ч0 offline) ──

def test_rotation_keeps_a_text_box_too():
    """Ч0-1 (15.10.2026) measured that the setter drops ``BoardTextBox`` as well — it is not
    a ``BoardText`` subclass in kipy 0.8.0, so it has no branch. The live board carries none,
    hence a constructed one: the RULE is what this cell protects, not the board's inventory."""
    box = BoardTextBox()
    fp = _kipy_fp([box, _model()])

    restored = set_footprint_orientation(fp, KipyAngle.from_degrees(90.0))

    assert restored == 2, "both the text box and the model have no branch in the setter"
    assert box in fp.definition.items
    assert any(isinstance(i, BoardTextBox) for i in fp.definition.items)


# ── К5: the rebuilt list keeps the original ORDER ────────────────────────────

def test_restored_items_keep_their_original_positions_in_the_list():
    fp = _kipy_fp([_model(), Pad(), BoardTextBox(), BoardRectangle()])
    order_before = _types(fp.definition.items)

    set_footprint_orientation(fp, KipyAngle.from_degrees(90.0))

    assert _types(fp.definition.items) == order_before, \
        "dropped items are put back on their own slots, not appended at the end"


# ── К6: a shape is re-wrapped by kipy, never duplicated by us ────────────────

def test_a_reclassed_shape_is_not_duplicated():
    fp = _kipy_fp([_model(), BoardRectangle()])

    set_footprint_orientation(fp, KipyAngle.from_degrees(45.0))

    shapes = [i for i in fp.definition.items if isinstance(i, BoardShape)]
    assert len(shapes) == 1, "the identity-blind rule would have appended a second figure"
    assert type(shapes[0]).__name__ == "BoardPolygon", \
        "kipy's own re-wrap (rectangle -> polygon off the orthogonal angles) must survive"
    assert any(isinstance(i, Footprint3DModel) for i in fp.definition.items)


# ── К9: the same physical angle in another canonicalisation ──────────────────

def test_a_proto_holding_270_accepts_a_minus_90_dto_without_a_write():
    proto = board_types_pb2.FootprintInstance()
    proto.orientation.value_degrees = 270.0        # KiCad may store this; Angle.__eq__ lies
    kipy_fp = FootprintInstance(proto=proto)
    ref_field = _field()
    kipy_fp.reference_field = ref_field
    kipy_fp.definition.items = [ref_field, _model()]
    items_before = kipy_fp.definition.items

    restored = set_footprint_orientation(kipy_fp, KipyAngle.from_degrees(-90.0))

    assert restored == 0
    assert kipy_fp.definition.items is items_before
    assert kipy_fp.orientation.degrees == 270.0, "nothing was rewritten for a zero delta"


# ── К10: the model reaches the PACKED proto (a lazy _pack() hides it otherwise) ──

def test_update_items_pushes_the_model_into_the_packed_proto():
    kipy_fp = _kipy_fp([_model("SOT-223.step")], angle=0.0)
    adapter = _adapter()

    adapter.update_items([_dto(kipy_fp, angle_deg=90.0)])

    (sent,), _kwargs = adapter._board.update_items.call_args
    assert sent == [kipy_fp]
    assert sent[0].orientation.degrees == 90.0
    packed = sent[0].proto                            # the bytes KiCad would receive
    # kipy's OWN unwrap: the domain one is for our DTOs and passes a raw proto item through
    # unchanged, so it would report a green cell over a list of Any messages.
    packed_types = [type(kipy_unwrap(item)).__name__ for item in packed.definition.items]
    assert "Footprint3DModel" in packed_types, \
        f"the model never made it into the sent proto: {packed_types}"


# ── К7/К8: canaries on kipy itself — they belong to kipy, not to us ──────────

def test_raw_kipy_orientation_setter_still_drops_the_3d_model():
    """CANARY on the UPSTREAM bug, not on our behaviour.

    GREEN while kipy drops items (that is why the shim exists). RED the day kipy is fixed:
    then delete kicadstamp/kicad/kipy_footprint_safety.py, put the plain
    ``kipy_fp.orientation = angle`` back in the adapter, and delete this cell.
    """
    fp = _kipy_fp([_model()])

    fp.orientation = KipyAngle.from_degrees(90.0)

    assert not any(isinstance(i, Footprint3DModel) for i in fp.definition.items), (
        "kipy's orientation setter no longer drops the 3D model — the bug this shim works "
        "around is FIXED upstream: remove kicadstamp/kicad/kipy_footprint_safety.py")


#: What kipy 0.8.0's setter keeps (board_types.py:2138-2164) — the shim's own statement of
#: it, restated here so a drift fails loudly instead of silently.
KIPY_KEEPS = {"Field", "Pad", "BoardText", "BoardShape"}

#: Zone is deliberately absent: a bare Zone cannot be rotated by kipy at all (its outline
#: has no polygons — IndexError, measured 15.10.2026), so there is no such thing as a cheap
#: Zone cell here. Real zones are covered by the live census probe.
_BUILDERS = {
    "Field": _field,
    "Pad": Pad,
    "BoardText": BoardText,
    "BoardShape": BoardRectangle,
    "BoardTextBox": BoardTextBox,
    "Footprint3DModel": _model,
}


@pytest.mark.parametrize("name", sorted(_BUILDERS))
def test_raw_setter_keeps_exactly_the_branch_types(name):
    """Drift canary: kipy keeps an item type if and only if the shim says it does.

    A change here means kipy's item list moved under the shim — re-read
    kicadstamp/kicad/kipy_footprint_safety.py before trusting it again.
    """
    item = _BUILDERS[name]()
    fp = _kipy_fp([item])

    fp.orientation = KipyAngle.from_degrees(90.0)

    survived = any(_branch(i) == name for i in fp.definition.items)
    assert survived == (name in KIPY_KEEPS), (
        f"kipy's orientation setter now treats {name} differently "
        f"(kept={survived}, expected={name in KIPY_KEEPS})")
