# kicadstamp/near_miss.py
"""«A field NAME that only LOOKS like the one asked for» — the ONE rule behind the
read's near-miss line (С4 of plan_2026_11_20_cell_read_takes_inter_node_copper,
Denis 23.11).

WHY. The live J5 of `pwr_p24v_power` (19.11) carried `Claster` instead of
`Cluster`: the read treated it as an untagged component and dropped it without a
word. A name two edits away from the target is a typo the user can fix; a name
further away (`Clusters2025`) is a DIFFERENT field and must stay silent.

The rule is deliberately tiny and dependency-free: a handful of field names per
component does not justify an import, and «distance 1..2, case ignored, the target
itself is not a miss» is the whole of it.
"""
from __future__ import annotations

__all__ = ["edit_distance", "near_miss"]


def edit_distance(left: str, right: str) -> int:
    """Levenshtein distance between two names, CASE-INSENSITIVE.

    The plain O(len × len) table: the strings here are field names, so the cost is a
    few dozen cells and the code stays readable."""
    a, b = str(left).lower(), str(right).lower()
    if a == b:
        return 0
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(previous[j] + 1,          # a deletion
                               current[j - 1] + 1,       # an insertion
                               previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def near_miss(names, target: str) -> str | None:
    """The first name in ``names`` that looks like ``target`` but is not it.

    «Looks like» = edit distance 1 or 2, case ignored. The target itself never
    answers (an exact name is not a miss), and nothing further away does either —
    a look-alike is a typo, not a different family of fields."""
    wanted = str(target).lower()
    for name in sorted(str(n) for n in (names or ())):
        if name.lower() == wanted:
            continue
        if 0 < edit_distance(name, target) <= 2:
            return name
    return None
