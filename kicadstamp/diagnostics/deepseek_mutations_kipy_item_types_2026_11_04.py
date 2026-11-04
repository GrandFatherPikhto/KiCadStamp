# kicadstamp/diagnostics/deepseek_mutations_kipy_item_types_2026_11_04.py
"""Acceptance mutations for plan_2026_11_04_kipy_reference_point Т1 (the item-type catalogue
over kipy and the reference point's geometry), 04.11.2026.

Grown from the same rig every other acceptance list uses (rule 38): basename-resolved guards
under tests/, ``_drop_pyc`` for the mutated file, the ``original.count(old) != 1`` refusal,
"ПРОМАХ" when nothing red came back, a control row that MUST survive, and ``-n auto``.

WHAT IS BEING PROVEN, and by WHICH cell:

  * М1 an unknown type is thrown away at read time  -> К1, К4, К5, К9 red (the point and the
                                                       opaque item disappear) — the pre-04.11
                                                       behaviour the plan exists to end
  * М2 the point is not moved with its footprint    -> Д1 and Д5 red
  * М3 the exact rotation goes back to int()        -> Д2's 30-degree cell red (its point is the
                                                       one where round() and int() disagree);
                                                       the quarter turns stay green, because
                                                       their arms never reach the fallback
  * М4 the "original" is read from kipy at call time instead of the frozen one (the patch
    measuring itself — what a second install really costs)
                                                    -> К6 red (the reloaded module's original is
                                                       OUR wrapper, not kipy's) and К7 red (the
                                                       canary then measures us)
  * М5 the opaque-item line loses its dedup         -> Д4's log cell red (four lines for two
                                                       moves of the same footprint+type)
  * М6 a ReferencePoint is treated as opaque        -> Д1 and Д2 red (it stops being carried by
                                                       anything: the setter drops it and nobody
                                                       puts it back rotated)
  * М7 the point is rebuilt without its `parent`    -> К2 red (the field is gone) and К3 red
                                                       (the packed footprint is no longer
                                                       byte-identical to the IPC answer — the
                                                       8.1 risk 1 cell)
  * K  a cosmetic comment                           -> MUST survive

MEASURED, first attempt (04.11.2026): the row "capture the original again on every import"
(mutating the `_ORIGINAL_MARKER is None` guard) SURVIVED — `install()` returns on
`_INSTALLED_MARKER` long before it, so a reload never reaches that line. The marker pair is
exactly what makes the second install a no-op, and the row has to attack the frozen reference
itself (М4 below) to prove the canary still measures kipy.

Run: .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_kipy_item_types_2026_11_04.py
An optional row-name prefix filter takes the rest of argv.
"""
from kicadstamp.diagnostics import deepseek_mutations_tree_reread_modules_2026_10_05 as rig

CATALOGUE = "kicadstamp/kicad/kipy_item_types.py"
SAFETY = "kicadstamp/kicad/kipy_footprint_safety.py"
TYPE_GUARDS = ["test_kipy_item_types.py"]
GEOMETRY_GUARDS = ["test_adapter_reference_point_geometry.py"]

ROWS = [
    ("М1 an unknown type is thrown away at read time", CATALOGUE,
     "    if raw_any.type_url == REFERENCE_POINT_TYPE_URL:\n"
     "        concrete = reference_point_class()()\n"
     "    else:\n"
     "        concrete = _stub_class_for(raw_any.type_url)()\n",
     "    raise NotImplementedError(  # MUTATION\n"
     "        f\"Missing type mapping for {raw_any.type_url}, can't unpack it\")\n",
     "die", TYPE_GUARDS, ()),
    ("М2 the point is not moved with its footprint", SAFETY,
     "    moved = 0\n"
     "    for item in _definition_items(kipy_fp):\n"
     "        if is_reference_point(item) and not isinstance(item, KIPY_ORIENTATION_BRANCH_TYPES):\n"
     "            item.position = item.position + delta\n"
     "            moved += 1\n"
     "    return moved\n",
     "    return 0  # MUTATION\n",
     "die", GEOMETRY_GUARDS, ()),
    ("М3 the exact rotation goes back to int()", SAFETY,
     "    self.x = round(dy * sin_angle + dx * cos_angle) + center.x\n"
     "    self.y = round(dy * cos_angle - dx * sin_angle) + center.y",
     "    self.x = int(dy * sin_angle + dx * cos_angle) + center.x  # MUTATION\n"
     "    self.y = int(dy * cos_angle - dx * sin_angle) + center.y  # MUTATION",
     "die", GEOMETRY_GUARDS, ()),
    ("М4 the frozen original reads kipy at call time", CATALOGUE,
     "_KIPY_UNPACK_ANY = getattr(_kipy_util_proto, _ORIGINAL_MARKER, None) "
     "or _kipy_util_proto.unpack_any",
     "_KIPY_UNPACK_ANY = _kipy_util_proto.unpack_any  # MUTATION: the patch measures itself",
     "die", TYPE_GUARDS, ()),
    ("М5 the opaque-item line loses its dedup", SAFETY,
     "        key = (ref, type_name)\n"
     "        if key in _STATIONARY_REPORTED:\n"
     "            continue\n"
     "        _STATIONARY_REPORTED.add(key)\n",
     "        key = (ref, type_name)  # MUTATION: no dedup\n",
     "die", GEOMETRY_GUARDS, ()),
    ("М6 a ReferencePoint is treated as opaque", CATALOGUE,
     "    return descriptor is not None and descriptor.full_name == REFERENCE_POINT_FULL_NAME",
     "    return False  # MUTATION: a point is just another unknown blob",
     "die", GEOMETRY_GUARDS, ()),
    ("М7 the point is rebuilt without its parent", CATALOGUE,
     "    try:\n"
     "        concrete.ParseFromString(raw_any.value)\n"
     "    except DecodeError:",
     "    try:\n"
     "        concrete.ParseFromString(raw_any.value)\n"
     "        if raw_any.type_url == REFERENCE_POINT_TYPE_URL:\n"
     "            concrete.ClearField(\"parent\")  # MUTATION: rebuilt, not carried\n"
     "    except DecodeError:",
     "die", TYPE_GUARDS, ()),
    ("K cosmetic comment (control)", SAFETY,
     "def _rotate_reference_points(items, delta_degrees: float, center) -> int:",
     "def _rotate_reference_points(items, delta_degrees: float, center) -> int:  # control",
     "survive", GEOMETRY_GUARDS, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
