# tests/test_near_miss.py
"""С4 cells (plan_2026_11_20_cell_read_takes_inter_node_copper, Denis 23.11): a
field name one or two edits away from the target is a TYPO worth a Log line; the
target itself and a name further away are different fields and stay silent.

The live reason: J5 of `pwr_p24v_power` carried `Claster` (19.11) and the read
dropped it as untagged without a word.
"""
from kicadstamp.near_miss import edit_distance, near_miss


def test_the_distance_is_case_insensitive_and_counts_edits():
    assert edit_distance("Cluster", "Cluster") == 0
    assert edit_distance("cluster", "Cluster") == 0
    assert edit_distance("Claster", "Cluster") == 1      # one substitution
    assert edit_distance("Clsuter", "Cluster") == 2      # a transposition is two edits
    assert edit_distance("Rol", "Role") == 1
    assert edit_distance("Clusters2025", "Cluster") == 5      # no prefix tolerance


def test_a_typo_is_a_near_miss_and_the_target_is_not():
    """`Claster` and `Rol` are typos; `Cluster` itself is the RIGHT name (the tag is
    there, the component is not untagged) and must never be reported."""
    assert near_miss(("Cluster", "Claster"), "Cluster") == "Claster"
    assert near_miss(("Role", "Rol"), "Role") == "Rol"
    assert near_miss(("Cluster",), "Cluster") is None
    assert near_miss(("claster",), "Cluster") == "claster"


def test_a_far_name_is_a_different_field_not_a_typo():
    """`Clusters2025` is somebody's own field: reporting it would teach the user to
    ignore the line."""
    assert near_miss(("Clusters2025", "Value", "Footprint"), "Cluster") is None
    assert near_miss((), "Cluster") is None


def test_an_exact_name_elsewhere_does_not_hide_a_typo():
    """The scan does not stop at the right name: a footprint may carry BOTH, and the
    typo still has to be named."""
    assert near_miss(("Claster", "Cluster"), "Cluster") == "Claster"
