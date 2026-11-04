#!/usr/bin/env python3
"""Watchdogs for the REFERENCE POINT geometry through the adapter —
``kicadstamp/kicad/kipy_footprint_safety.py`` (plan_2026_11_04_kipy_reference_point, Т1, Р3).

The disease, twice over: kipy's ``FootprintInstance.position`` setter has an arm for Field / Pad /
BoardText / Zone / BoardShape only (kipy/board_types.py:2107-2117), and its ``orientation`` setter
REBUILDS ``definition.items`` out of exactly those five types (``board_types.py:2138-2164``). A
KiCad 10.0.7 ``ReferencePoint`` therefore stayed behind on every move and was DROPPED on every
rotation — on a board where four footprints carry one (measured live 04.11.2026, HP-Power-v099:
Q1, Q2, D4, D8).

Style: the real bytes of Q1 (``tests/kicad/reference_point_bytes.py``), a real
``kipy.FootprintInstance`` reached through kipy's own ``unwrap``, and the DTO ->
``sync_dto_to_kipy`` path the adapter actually uses (``adapter.py:1005``) — a raw kipy object there
would pass ``unwrap()`` straight through and the cell would verify nothing.

Д6 and Д7 pin SOMEBODY ELSE'S behaviour on purpose: when they go red, kipy has learned to carry
the point and the matching half of ``kipy_footprint_safety`` must be deleted (its docstring says
which half and which cell).
"""
import base64
import logging
import math

import pytest
from google.protobuf.any_pb2 import Any

import kicadstamp.kicad.kipy_footprint_safety as safety
from kipy.board_types import unwrap
from kipy.geometry import Angle as KipyAngle, Vector2 as KipyVector2
from kipy.proto.board import board_types_pb2
from kipy.proto.common.types.base_types_pb2 import KIID

from kicadstamp.domain.board import Footprint, layer_from_kipy
from kicadstamp.domain.geometry import Vector2 as DomainVector2
from kicadstamp.kicad.kipy_footprint_safety import (
    set_footprint_orientation,
    sync_dto_to_kipy,
)
from kicadstamp.kicad.kipy_item_types import (
    ReferencePointItem,
    is_opaque_item,
    is_reference_point,
)
from tests.kicad import reference_point_bytes as fx

#: Two types nobody knows — the log is per (footprint, TYPE), and one type alone cannot show it.
UNKNOWN_A = "type.googleapis.com/kiapi.board.types.SomeFutureThing"
UNKNOWN_B = "type.googleapis.com/kiapi.board.types.AnotherFutureThing"
UNKNOWN_PAYLOAD = KIID(value="11111111-2222-3333-4444-555555555555").SerializeToString()

FP_X, FP_Y = fx.FOOTPRINT_POSITION_NM
POINT_X, POINT_Y = fx.POINT_POSITION_NM


@pytest.fixture(autouse=True)
def _clean_report_memory():
    """The log line is once per (ref, type) for the PROCESS — each cell starts with a clean slate."""
    safety._STATIONARY_REPORTED.clear()
    yield
    safety._STATIONARY_REPORTED.clear()


# ── builders ────────────────────────────────────────────────────────────────────────────

def _instance(extra_items=()):
    instance = board_types_pb2.FootprintInstance()
    instance.ParseFromString(base64.b64decode(fx.FOOTPRINT_INSTANCE_B64))
    for type_url in extra_items:
        instance.definition.items.add(type_url=type_url, value=UNKNOWN_PAYLOAD)
    return instance


def _footprint(extra_items=()):
    """Q1 as kipy hands it out — a fresh object per cell, so nothing leaks between them."""
    packed = Any()
    packed.Pack(_instance(extra_items))
    return unwrap(packed)


def _point(kipy_fp) -> ReferencePointItem:
    points = [item for item in kipy_fp.definition.items if is_reference_point(item)]
    assert len(points) == 1, "the fixture footprint carries exactly one reference point"
    return points[0]


def _dto(kipy_fp, x=None, y=None, angle_deg=None) -> Footprint:
    """The DTO shape ``update_items`` really hands to ``sync_dto_to_kipy``."""
    return Footprint(
        ref=fx.REF, uuid=str(kipy_fp.id.value),
        position=DomainVector2(int(kipy_fp.position.x) if x is None else x,
                               int(kipy_fp.position.y) if y is None else y),
        angle_deg=float(kipy_fp.orientation.degrees) if angle_deg is None else angle_deg,
        layer=layer_from_kipy(kipy_fp.layer), _kipy=kipy_fp)


def _rotated_nm(x, y, degrees, center_x, center_y):
    """The expected point, written out HERE — the cell must not ask the code under test.

    Same geometry the pads get (KiCad rotates a footprint's children about its origin), and an
    independent statement of it: a quarter turn is exact integer arithmetic, any other angle is
    the textbook formula with rounding to the nanometre.
    """
    dx, dy = x - center_x, y - center_y
    quarter = float(degrees) % 360.0
    if quarter == 0.0:
        return (x, y)
    if quarter == 90.0:
        return (center_x + dy, center_y - dx)
    if quarter == 180.0:
        return (center_x - dx, center_y - dy)
    if quarter == 270.0:
        return (center_x - dy, center_y + dx)
    radians = math.radians(quarter)
    return (round(dy * math.sin(radians) + dx * math.cos(radians)) + center_x,
            round(dy * math.cos(radians) - dx * math.sin(radians)) + center_y)


# ── Д1: a move carries the point by exactly the same delta as the pads ──────────────────

@pytest.mark.parametrize("dx,dy", [(1_000_000, -2_000_000), (-3_500_000, 250_000)])
def test_the_move_carries_the_reference_point_by_the_same_delta(dx, dy):
    kipy_fp = _footprint()
    point = _point(kipy_fp)
    pad = kipy_fp.definition.pads[0]
    pad_before = (pad.position.x, pad.position.y)

    sync_dto_to_kipy(_dto(kipy_fp, x=FP_X + dx, y=FP_Y + dy), kipy_fp)

    assert (point.position.x, point.position.y) == (POINT_X + dx, POINT_Y + dy)
    assert (pad.position.x - pad_before[0], pad.position.y - pad_before[1]) == (dx, dy), (
        "the point and the pads must move together — one rule, one delta")


# ── Д2: a rotation turns the point about the footprint's position ──────────────────────

@pytest.mark.parametrize("degrees", [90, 180, 270, -90, 30])
def test_the_rotation_turns_the_point_about_the_footprint_position(degrees):
    kipy_fp = _footprint()
    point = _point(kipy_fp)
    off_centre_pad = next((p for p in kipy_fp.definition.pads
                           if (p.position.x, p.position.y) != (FP_X, FP_Y)), None)
    pad_before = ((off_centre_pad.position.x, off_centre_pad.position.y)
                  if off_centre_pad is not None else None)

    set_footprint_orientation(kipy_fp, KipyAngle.from_degrees(degrees))

    assert (point.position.x, point.position.y) == _rotated_nm(
        POINT_X, POINT_Y, degrees, FP_X, FP_Y)
    assert is_reference_point(point), "the setter drops it; the shim must put it back"
    if off_centre_pad is not None:
        # The pad went through kipy's OWN branch — the point must land where the same maths put
        # the pad, which is what "как пад рядом с ней" means.
        assert (off_centre_pad.position.x, off_centre_pad.position.y) == _rotated_nm(
            pad_before[0], pad_before[1], degrees, FP_X, FP_Y)


# ── Д3: a zero delta touches nothing ────────────────────────────────────────────────────

def test_a_zero_rotation_leaves_the_point_alone():
    kipy_fp = _footprint()
    point = _point(kipy_fp)
    before = point.proto.SerializeToString()

    sync_dto_to_kipy(_dto(kipy_fp), kipy_fp)

    assert point.proto.SerializeToString() == before
    assert (point.position.x, point.position.y) == (POINT_X, POINT_Y)


# ── Д4: an opaque item stays where it was, byte for byte ────────────────────────────────

@pytest.mark.parametrize("type_url", [UNKNOWN_A, UNKNOWN_B])
def test_an_unknown_item_stays_in_place_and_keeps_its_bytes(type_url):
    kipy_fp = _footprint([type_url])
    unknown = next(i for i in kipy_fp.definition.items if is_opaque_item(i))

    sync_dto_to_kipy(_dto(kipy_fp, x=FP_X + 7_000_000, y=FP_Y - 3_000_000, angle_deg=90.0),
                     kipy_fp)

    assert unknown in kipy_fp.definition.items, "an unknown element is never dropped"
    assert unknown.proto.SerializeToString() == UNKNOWN_PAYLOAD
    assert _point(kipy_fp).position is not None


def test_the_unknown_item_is_reported_once_per_footprint_and_type(caplog):
    kipy_fp = _footprint([UNKNOWN_A, UNKNOWN_B])

    with caplog.at_level(logging.WARNING, logger="kicadstamp.kicad.kipy_footprint_safety"):
        sync_dto_to_kipy(_dto(kipy_fp, x=FP_X + 1_000_000), kipy_fp)
        sync_dto_to_kipy(_dto(kipy_fp, x=FP_X + 2_000_000), kipy_fp)

    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(lines) == 2, "one line per TYPE, and no repeat on the next move of the same part"
    assert any("SomeFutureThing" in line for line in lines)
    assert any("AnotherFutureThing" in line for line in lines)
    assert all(fx.REF in line for line in lines)

    # A DIFFERENT footprint is a different series — it gets its own line.
    other = _footprint([UNKNOWN_A])
    dto = _dto(other, x=FP_X + 1_000_000)
    dto.ref = "Q9"
    sync_dto_to_kipy(dto, other)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 3


# ── Д5: only the position changes — the rest of the point is not rebuilt ───────────────

def test_a_move_changes_only_the_position_of_the_point():
    kipy_fp = _footprint()
    point = _point(kipy_fp)

    sync_dto_to_kipy(_dto(kipy_fp, x=FP_X + 1_234_567, y=FP_Y + 7_654_321), kipy_fp)

    assert point.id.value == fx.POINT_ID
    assert point.layer == fx.POINT_LAYER
    assert point.size_nm == fx.POINT_SIZE_NM
    assert point.locked == fx.POINT_LOCKED
    assert point.parent.value == fx.POINT_PARENT
    assert (point.position.x, point.position.y) == (POINT_X + 1_234_567, POINT_Y + 7_654_321)
    assert len(point.proto.SerializeToString()) == len(fx.POINT_VALUE)


# ── Д6: CANARY — kipy's own position setter does not move the point ─────────────────────

def test_raw_kipy_position_setter_does_not_move_the_reference_point():
    """Removal condition 3 of ``kipy_footprint_safety``: RED means kipy carries it by itself."""
    kipy_fp = _footprint()
    point = _point(kipy_fp)

    kipy_fp.position = KipyVector2.from_xy(FP_X + 5_000_000, FP_Y - 5_000_000)

    assert (point.position.x, point.position.y) == (POINT_X, POINT_Y), (
        "kicad-python now moves the point itself — delete _move_reference_points")


# ── Д7: CANARY — kipy's own orientation setter drops the point ──────────────────────────

def test_raw_kipy_orientation_setter_drops_the_reference_point():
    """Removal condition 4 of ``kipy_footprint_safety``: RED means kipy rotates it itself."""
    kipy_fp = _footprint()
    assert _point(kipy_fp) is not None

    kipy_fp.orientation = KipyAngle.from_degrees(90.0)

    assert not [item for item in kipy_fp.definition.items if is_reference_point(item)], (
        "kicad-python now keeps the point across a rotation — delete _rotate_reference_points")
