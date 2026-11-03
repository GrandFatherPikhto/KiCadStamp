# tests/checks/test_merge_duplicate_mounts_key.py
"""Т3 of plan_2026_11_03_add_mounts_table — the duplicate tool wears the ONE key.

The sameness rule of a mount moved to `kicadstamp/tree_mounts.mount_key` (the
mounts dialog asks the same question), and `tools/merge_duplicate_mounts.py`
imports it. Two things can silently break here, and both are cells below:

  * the tool keeping its OWN copy again (then the zero normalization the move
    brought would be missing from the place the Checks page runs);
  * the import working but the scan not using it.

The normalization itself matters on real files: the s-expr writer omits
default-valued fields, so the SAME mount is written `(xy 0.0 0.0)` — as
HiPiMS-Controller-v099 writes both of its mounts — or with no `xy` at all, which
is what a node created by the mounts table gets.
"""
from kicadstamp.tree_mounts import mount_key_of_dict
from tools import merge_duplicate_mounts


def _mount(ref, pad, *, xy=None):
    """A config-DICT mount node — the shape the tool scans (never a TreeNode)."""
    node = {"ref": ref, "kind": "mount",
            "anchor": {"role": "FPGA", "sheet": "FPGA", "cluster": "FPGA",
                       "pad": pad}}
    if xy is not None:
        node["xy"] = list(xy)
    return node


def _tree(*nodes):
    return {"name": "fpga", "nodes": list(nodes)}


def test_the_tool_imports_the_one_key():
    """No second copy: the name IS the module's, and the old private one is gone."""
    assert merge_duplicate_mounts.mount_key_of_dict is mount_key_of_dict
    assert not hasattr(merge_duplicate_mounts, "_mount_key")


def test_written_zeros_and_no_xy_are_one_duplicate():
    """The cell the move exists for: the old raw-dict key saw two different mounts
    here, so the Checks page stayed silent about a real duplicate."""
    written = _mount("mnt_a", "107", xy=(0.0, 0.0))
    canon = _mount("mnt_b", "107")
    merges, strays = merge_duplicate_mounts._scan(_tree(written, canon), set())
    assert len(merges) == 1
    _siblings, kept, duplicates = merges[0]
    assert kept["ref"] == "mnt_a"
    assert [node["ref"] for node in duplicates] == ["mnt_b"]
    assert strays == []


def test_a_different_pad_is_a_different_mount():
    merges, _strays = merge_duplicate_mounts._scan(
        _tree(_mount("mnt_a", "107"), _mount("mnt_b", "108")), set())
    assert merges == []


def test_a_negative_zero_is_the_same_zero():
    """Real spelling met in 3ch-awg-tia-v103: `xy (0.0, -0.0)`. A raw comparison
    would have called it a different mount from `(0.0, 0.0)`."""
    merges, _strays = merge_duplicate_mounts._scan(
        _tree(_mount("mnt_a", "107", xy=(0.0, 0.0)),
              _mount("mnt_b", "107", xy=(0.0, -0.0))), set())
    assert len(merges) == 1
