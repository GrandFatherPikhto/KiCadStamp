# kicadstamp/role_cluster_damage.py
"""«Поля Role/Cluster испорчены обновлением футпринтов из библиотеки» — сторож.

Why this module exists (2026-10-21, Ч1 of
plan_2026_10_21_role_lookup_steals_across_clusters.md, решение В2)

KiCad's Tools → Update Footprints from Library has a bug
(`techdocs/bugreport/2026_10_15_footprint_update/4_…`, KiCad #25887): it renames
a USER footprint field into the NAME of a library field, keeping OUR value. The
pairing is by SIMILARITY of the fields' text (attributes and position count
too; a tie goes by memory address), never by name — so on one footprint
`Cluster` becomes `KiLib_Generator`, on another `Role`, and two runs over the
same data damage different fields. Measured on `Tests/Test02` (21.10, .history
snapshots):

    after F8 (clean)                  KiLib_Generator 0
    after Update Footprints, run 1    9 components: C1 C3 C4 C5 C6 D1 D2 R1 R2
    after Update Footprints, run 2    8 components: C1 C3 C4 C5 C6 D2 R1 R2
    "all boxes ticked" (Remove text)  11 components, U1 WITHOUT KiLib_Generator

The damage is what turns a role lookup into a theft: the RIGHT component loses
its `Role` (so it stops being a candidate), and a component of ANOTHER cluster
keeps `Role` but loses `Cluster` — the one candidate then gets taken (the live
fact `R_LED`/`P5V_IN` → R2 of `LDO_P3V3`). Confining role lookups to the named
cluster (Ч1 of the same plan) makes them REFUSE instead; this guard makes the
refusal honest — «press F8» — before a single move is planned.

The signature is BOTH facts, and only this pair
------------------------------------------------
``has_field(KiLib_Generator)`` AND (``Role`` or ``Cluster`` PHYSICALLY absent).
The control samples are what make it sharp rather than a guess:

  * a clean saved board carries NO `KiLib_Generator` at all (0 on v103, Heating,
    Test02 after F8) — so its presence alone already smells of the dialog;
  * a component whose values match no library text keeps both fields and gets
    `KiLib_Generator` BESIDE them (C2, J1) — not damage;
  * U1 of the "all boxes ticked" run HAS neither Role nor Cluster and WAS damaged
    (Remove text items deleted both fields) — yet the guard does NOT see it,
    because SOT-223 carries no `KiLib_Generator` in its library copy; and no
    theft follows either, since a part without `Role` is not a candidate at all
    ("not found"). This is the signature's honest LIMIT: it catches the rename
    that leaves the library field's name behind, not a plain deletion of ours.
    A part that was NEVER tagged and then went through Update Footprints from
    Library gets `KiLib_Generator` WITHOUT `Role` and IS refused here — F8
    clears it, since the symbol carries no such field. No such part exists on
    our boards (v103 336/336, Heating 77/77, Test02 12/12 are tagged).

So the plain XOR of Role/Cluster is deliberately NOT the guard: the 15.10 v103
log did look like XOR ("… carries this role but has no Cluster field" on dozens
of parts), but that was the same bug on the day it was being caught — today
v103's XOR count is 0. A signature that only ever agrees with this one adds
nothing and can fire on a board that was never touched.

What it must read: the BOARD, not the value in force. ``has_field`` answers
about the footprint physically (`field_override_adapter.FieldOverrideAdapter`
keeps it board-only on purpose), which is exactly the question here — the
override store can hold our value for a symbol whose field KiCad renamed away,
and that stored value is no evidence the field is still there.

The refusal (B2): ALWAYS when the signature is on the board, not only when the
config addresses the damaged part — the thief is precisely a component of
ANOTHER cluster that the config does not address. No dialog, no question: a
`ValidationError` from the one pre-validation door (`validation.run_all_checks`,
which `ApplyPipeline._validate` calls for apply, redraw, cascade, the CLI and
every GUI worker) — the Log shows it, the run does not start. The cure is the
same every time: F8 with its defaults removes `KiLib_Generator` and restores
both fields from the schematic (F8 is a full update from the schematic — it
refreshes footprints, values and nets too, so the refusal says only what it
can promise).
"""
from __future__ import annotations

from .constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from .exceptions import ValidationError, format_fatal_error
from .i18n import _

__all__ = [
    "KILIB_GENERATOR_FIELD_NAME", "SHOWN_DAMAGED_REFS",
    "damaged_footprints", "check_role_cluster_fields_intact",
]

#: The library field's name the bug renames OUR field into (bug report 4: 12045
#: of 15464 stock footprints carry it, so any board with user fields is exposed).
KILIB_GENERATOR_FIELD_NAME = "KiLib_Generator"

#: How many damaged refdes the refusal names before it stops counting them out.
SHOWN_DAMAGED_REFS = 10


def damaged_footprints(adapter) -> list:
    """``[(ref, [missing field name, …])]`` — every footprint carrying the bug 4
    signature, in board order (the order the Log's neighbours report in).

    The detector is the pair described in the module docstring, and it reads the
    PHYSICAL board through ``adapter.has_field`` only: a damaged footprint whose
    value happens to come from the override store is still damaged. A footprint
    with no `KiLib_Generator` is out of this signature's reach whether it was
    never tagged or was damaged by a plain deletion — the module docstring says
    so and why that is acceptable. The refdes is a label here, never a key — the
    caller only names parts.
    """
    damaged = []
    for fp in adapter.get_footprints():
        if not adapter.has_field(fp, KILIB_GENERATOR_FIELD_NAME):
            continue
        missing = [name for name in (ROLE_FIELD_NAME, CLUSTER_FIELD_NAME)
                   if not adapter.has_field(fp, name)]
        if missing:
            damaged.append((getattr(fp, "ref", "") or "?", missing))
    return damaged


def check_role_cluster_fields_intact(adapter) -> None:
    """The guard itself — silent on a healthy board, one fatal on a damaged one.

    Called by ``validation.run_all_checks`` FIRST after the board-identity check:
    a board whose fields were renamed is not a board any role/cluster address can
    be trusted on, so every other check would merely describe the wreckage.
    """
    damaged = damaged_footprints(adapter)
    if not damaged:
        return

    shown = ", ".join(
        _("{ref} ({fields})").format(ref=ref, fields="/".join(missing))
        for ref, missing in damaged[:SHOWN_DAMAGED_REFS])
    hidden = len(damaged) - SHOWN_DAMAGED_REFS
    if hidden > 0:
        shown = _("{shown} and {count} more").format(shown=shown, count=hidden)

    raise ValidationError(format_fatal_error(
        _("{count} component(s) on this board lost their Role/Cluster fields — "
          "KiCad's “Update Footprints from Library” renamed them to {library_field!r}")
        .format(count=len(damaged), library_field=KILIB_GENERATOR_FIELD_NAME),
        [
            _("the renamed fields: {shown}").format(shown=shown),
            _("this is the known KiCad bug: a library field is matched to a board "
              "field by similarity of their text, not by name, while the value "
              "stays ours, so a placement addressed by Role/Cluster would take a "
              "component of ANOTHER cluster instead of refusing"),
            _("press Update PCB from Schematic (F8) with its defaults — it removes "
              "{library_field!r} and restores both fields from the schematic")
            .format(library_field=KILIB_GENERATOR_FIELD_NAME),
        ]
    ))
