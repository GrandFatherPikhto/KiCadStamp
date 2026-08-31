"""Tests for kicadstamp/cluster_matching.py — the single shared Cluster
segment-prefix matcher (exact, or parent-cluster '/' prefix). Case-insensitive
since 2026-08-31 (plan_2026_08_31_fpga_flash_rigid_redraw_not_following.md):
a Cluster tag is a user-visible label, its case is not semantically
significant — an Entity materialized from a tree may fall back its cluster to
its own lower-case name while the physical Cluster field on the board is
upper-case."""
import pytest

from kicadstamp.cluster_matching import cluster_prefix_match


def test_exact_match_same_case():
    assert cluster_prefix_match("FPGA_FLASH", "FPGA_FLASH") is True


def test_exact_match_case_insensitive():
    """The live fpga_flash repro: the materialized clone's fallback cluster is
    the entity's own lower-case name ('fpga_flash'), the physical Cluster field
    is upper-case ('FPGA_FLASH'). A case-sensitive match empties the candidate
    set and the net auto-derivation cannot find the unique instance -> the whole
    apply/redraw fatals."""
    assert cluster_prefix_match("FPGA_FLASH", "fpga_flash") is True
    assert cluster_prefix_match("fpga_flash", "FPGA_FLASH") is True


def test_hierarchical_parent_matches_child_same_case():
    """Segment-prefix semantics preserved: a parent Cluster tag matches a
    deeper child ('CH0' matches 'CH0/SUB'), a substring does not."""
    assert cluster_prefix_match("CH0/SUB", "CH0") is True


def test_hierarchical_parent_matches_child_case_insensitive():
    assert cluster_prefix_match("Channel_0/1V2_PLL", "channel_0") is True


def test_not_a_segment_prefix_substring_does_not_match():
    """Segment-prefix, NOT substring: 'Channel_10' must not match 'Channel_1'
    (the original guard), and 'FPGA_FLASH2' must not match 'fpga_flash'."""
    assert cluster_prefix_match("Channel_10", "Channel_1") is False
    assert cluster_prefix_match("Channel_10", "channel_1") is False
    assert cluster_prefix_match("FPGA_FLASH2", "fpga_flash") is False


def test_empty_candidate_never_matches_nonempty_wanted():
    assert cluster_prefix_match("", "CH0") is False
    assert cluster_prefix_match("", "") is True
