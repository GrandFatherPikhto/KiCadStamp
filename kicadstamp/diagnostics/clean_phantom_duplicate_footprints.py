"""
clean_phantom_duplicate_footprints.py — finds and (with --delete) removes
physical footprint duplicates left over on the live board by the
pre-2026-08-03 set_field_values_bulk bug (same object written twice in one
update_items() call created a second physical copy — see
techdocs/handoff/handoff_2026_08_03_stage_duplicate_footprints_fix.md).
That bug is fixed; this only cleans up damage that predates the fix and was
never cleaned up on this particular board (renamed from
list_phantom_duplicate_footprints.py, 2026-08-07 — it does more than list
now).

The "which refdes stands on the board more than once" question is NOT answered
here any more: it is the product rule of ``kicadstamp.refdes_duplicates``
(2026-10-12, plan_2026_10_12_duplicate_refdes_guard — the pipeline's door guard
and this report must never disagree about what a duplicate is). This script
keeps only what is ITS OWN: the safe-deletion criterion and the deletion.

Input:
    None (connects to the live board).

Safe criterion (verified 2026-08-07 to hold on ALL duplicated refs on
boards/3ch-awg-tia, zero exceptions): for a ref with N>1 physical
footprints, if exactly ONE has a non-empty Role or Cluster and the REST are
at the identical (x, y) position with BOTH Role and Cluster empty, the
empty ones are phantom duplicates — same ref, same spot, no data of their
own. Any ref that does NOT match this exact shape (more than one populated
copy, or copies at different positions, or something in between) is left
alone and reported separately under "irregular" — never guessed at, never
deleted.

Without --delete: read-only, prints the same report as before, deletes
nothing. This is the default — you have to opt into deletion explicitly,
not opt out of a dry-run (unlike tools/dedupe_vias_tracks.py's convention;
deliberately inverted here — this is real customer-board component data,
not vias/tracks, and a one-off historical-incident cleanup rather than a
routine maintenance tool, so the safer default felt right).

With --delete: sends ALL matched phantom footprints in ONE
board.remove_items_by_id() call — a single KiCad command, so a single
Ctrl+Z in KiCad undoes the entire cleanup at once, not one undo step per
footprint.

Run:
    python -m kicadstamp.diagnostics.clean_phantom_duplicate_footprints            # report only
    python -m kicadstamp.diagnostics.clean_phantom_duplicate_footprints --delete   # actually delete
"""
import argparse
from collections import defaultdict

from kipy import KiCad
from kipy.board_types import Field

from kicadstamp.refdes_duplicates import duplicate_refdes_groups


class _KipyRecord:
    """One raw kipy footprint in the duck-typed shape the product detector
    (``kicadstamp.refdes_duplicates``) reads: ``ref``, ``uuid``, ``position`` and
    the RAW ``sheet_path`` — the ONE chain reader accepts both the domain and
    the kipy shape, so nothing is duplicated here."""

    __slots__ = ("ref", "uuid", "position", "sheet_path")

    def __init__(self, fp):
        ref_field = getattr(fp, "reference_field", None)
        text = getattr(ref_field, "text", None)
        self.ref = text.value if text is not None else None
        ident = getattr(fp, "id", None)
        self.uuid = getattr(ident, "value", None)
        self.position = getattr(fp, "position", None)
        self.sheet_path = getattr(fp, "sheet_path", None)


def _role_cluster(fp) -> tuple:
    """(Role, Cluster) as typed on the live footprint — both None when absent."""
    role = cluster = None
    for item in fp.texts_and_fields:
        if isinstance(item, Field):
            if item.name == "Role":
                role = item.text.value if item.text else None
            elif item.name == "Cluster":
                cluster = item.text.value if item.text else None
    return role, cluster


def find_phantoms(footprints):
    """Returns (to_delete, irregular): to_delete is a list of (ref, fp) safe
    to remove per the module docstring's criterion; irregular is a list of
    (ref, count, unique_positions, populated_count) for refs that have
    duplicates but don't match the exact expected shape — reported, never
    touched.

    The duplicate refdes set itself comes from the PRODUCT detector
    (``duplicate_refdes_groups``) — this function asks it and only classifies
    what it finds."""
    entries = [(_KipyRecord(fp), fp) for fp in footprints]
    duplicates = duplicate_refdes_groups([record for record, _ in entries])

    by_ref = defaultdict(list)
    for record, fp in entries:
        if record.ref in duplicates:
            by_ref[record.ref].append((record, fp))

    to_delete = []
    irregular = []

    for ref, members in sorted(by_ref.items()):
        positions = {(record.position.x, record.position.y)
                     for record, _fp in members}
        labelled = [(record, fp, _role_cluster(fp)) for record, fp in members]
        populated = [(fp, role, cluster) for _rec, fp, (role, cluster) in labelled
                     if (role or cluster)]
        empty = [(fp, role, cluster) for _rec, fp, (role, cluster) in labelled
                 if not (role or cluster)]
        if len(positions) == 1 and len(populated) == 1 and len(empty) == len(members) - 1:
            keep_fp, keep_role, keep_cluster = populated[0]
            print(f"{ref}: keep 1 (Role={keep_role!r} Cluster={keep_cluster!r}, "
                  f"id={keep_fp.id.value}), delete {len(empty)} phantom(s)")
            for fp, _, _ in empty:
                to_delete.append((ref, fp))
        else:
            irregular.append((ref, len(members), len(positions), len(populated)))

    return to_delete, irregular


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--delete", action="store_true",
                     help="Actually delete the phantom footprints (default: report only, "
                          "delete nothing)")
    args = ap.parse_args()

    kicad = KiCad(timeout_ms=8000)
    board = kicad.get_board()
    footprints = board.get_footprints()

    to_delete, irregular = find_phantoms(footprints)

    dupe_count = len(to_delete) and len({ref for ref, _ in to_delete})
    print(f"\nRefs с дублями, подходящими под безопасный критерий: {dupe_count}, "
          f"{len(to_delete)} футпринтов-фантомов")
    if irregular:
        print(f"\nНЕ подходят под критерий (руками смотреть, НЕ трогаются этим скриптом): "
              f"{len(irregular)}")
        for ref, n, n_pos, n_pop in irregular:
            print(f"  {ref}: {n} копий, {n_pos} уникальных позиций, {n_pop} с данными")

    if not to_delete:
        print("\nНечего удалять.")
        return

    if not args.delete:
        print(f"\n[dry-run] Ничего не удалено. Запустите с --delete, чтобы реально удалить "
              f"все {len(to_delete)} футпринтов ОДНОЙ командой (один Ctrl+Z отменит всё).")
        return

    print(f"\nУдаляю {len(to_delete)} футпринтов одной командой...")
    board.remove_items_by_id([fp.id for _, fp in to_delete])
    print("Готово. Один Ctrl+Z в KiCad отменит удаление целиком.")


if __name__ == "__main__":
    main()
