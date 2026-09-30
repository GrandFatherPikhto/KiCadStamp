# tests/test_net_trace_identity.py
"""Э2 identity tests: `net_traces:` identity moves from `net:` to `name:`
(plan_2026_09_12_internode_copper_core, stage Э2; design
design_2026_09_12_tree_internode_copper_reread §11).

The contract under test:
  * `net_trace_effective_name` is `name or net` — the ONE seam, so every
    consumer (--only, anchor_graph, rename, the registry key) follows for free;
  * duplicates of `net:` become LEGAL (two bridges of one net) while duplicate
    effective names stay fatal;
  * a legacy record (no `name:`) is byte-identical on disk: load -> save adds no
    `name:` key and reproduces the original text exactly.
"""
import pytest

from kicadstamp.apply_pipeline import apply_only_filter
from kicadstamp.config import Config, NetTrace, load_config
from kicadstamp.config.models import net_trace_effective_name
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config_writer import read_data, write_data
from kicadstamp.exceptions import ValidationError
from kicadstamp.internode_copper import generate_trace_name
from kicadstamp.link_trees import link_trees
from kicadstamp.net_trace_extract import net_trace_to_dict
from kicadstamp.net_trace_planner import net_trace_anchor_id
from kicadstamp.trees import Tree, TreeAnchor, TreeNode


def _legacy_record(net="DAC_DB0", **overrides):
    """A valid net_traces record dict WITHOUT name: — the shape every existing
    profile on disk has."""
    record = {
        "net": net, "anchor_role": "FPGA", "anchor_pad": "42",
        "tracks": [{"start_along_mm": 1.0, "start_across_mm": 2.0,
                    "end_along_mm": 3.0, "end_across_mm": 4.0,
                    "width_mm": 0.2, "net": net, "layer": "B.Cu"}],
        "vias": [{"offset_along_mm": 5.0, "offset_across_mm": 6.0,
                  "net": net, "drill_mm": 0.3, "diameter_mm": 0.6}],
    }
    record.update(overrides)
    return record


# ── the identity seam ─────────────────────────────────────────────────────

def test_legacy_record_effective_name_is_its_net():
    nt = NetTrace(net="DAC_DB0", anchor_role="FPGA")
    assert nt.name is None
    assert net_trace_effective_name(nt) == "DAC_DB0"


def test_named_record_effective_name_is_its_name():
    nt = NetTrace(net="DAC_DB0", anchor_role="FPGA", name="dac_db0__a__b")
    assert net_trace_effective_name(nt) == "dac_db0__a__b"


def test_anchor_id_uses_the_name_and_keeps_the_net_prefix():
    """The registry key changes only in its middle: the `net:` PROTECTION
    prefix stays (design §11 — a cosmetic rename is not worth a registry
    migration), a legacy record's key is byte-identical to before."""
    legacy = NetTrace(net="DAC_DB0", anchor_role="FPGA")
    assert net_trace_anchor_id(legacy) == "net:DAC_DB0"
    named = NetTrace(net="DAC_DB0", anchor_role="FPGA", name="dac_db0__a__b")
    assert net_trace_anchor_id(named) == "net:dac_db0__a__b"


# ── --only: a name, and (for compatibility) a net ─────────────────────────

def test_only_accepts_the_record_name():
    cfg = Config(net_traces=[
        NetTrace(net="DAC_DB0", anchor_role="FPGA", name="dac_db0__a__b"),
        NetTrace(net="DAC_DB0", anchor_role="FPGA", name="dac_db0__b__c"),
    ])
    out = apply_only_filter(cfg, ["dac_db0__b__c"])
    assert [net_trace_effective_name(nt) for nt in out.net_traces] == \
        ["dac_db0__b__c"]


def test_only_accepts_a_net_and_expands_to_every_bridge():
    """A user's habitual `--only <net>` keeps working now that several records
    may share one net — it selects ALL bridges of that net."""
    cfg = Config(net_traces=[
        NetTrace(net="DAC_DB0", anchor_role="FPGA", name="dac_db0__a__b"),
        NetTrace(net="DAC_DB0", anchor_role="FPGA", name="dac_db0__b__c"),
        NetTrace(net="OTHER", anchor_role="FPGA"),
    ])
    out = apply_only_filter(cfg, ["DAC_DB0"])
    assert sorted(net_trace_effective_name(nt) for nt in out.net_traces) == \
        ["dac_db0__a__b", "dac_db0__b__c"]


def test_only_unknown_net_trace_name_is_still_fatal():
    cfg = Config(net_traces=[NetTrace(net="DAC_DB0", anchor_role="FPGA")])
    with pytest.raises(Exception, match="DAC_DB9"):
        apply_only_filter(cfg, ["DAC_DB9"])


# ── loader: net duplicates legal, name duplicates fatal ───────────────────

def test_two_records_on_one_net_are_legal_when_named(tmp_path):
    path = tmp_path / "board.sexp"
    path.write_text(dict_to_sexp({"net_traces": [
        _legacy_record(name="dac_db0__a__b"),
        _legacy_record(name="dac_db0__b__c"),
    ]}), encoding="utf-8")
    cfg, _ctx = load_config(str(path))
    assert [nt.name for nt in cfg.net_traces] == ["dac_db0__a__b", "dac_db0__b__c"]
    assert [nt.net for nt in cfg.net_traces] == ["DAC_DB0", "DAC_DB0"]


def test_two_records_with_the_same_name_are_fatal(tmp_path):
    path = tmp_path / "board.sexp"
    path.write_text(dict_to_sexp({"net_traces": [
        _legacy_record(net="A", name="same"),
        _legacy_record(net="B", name="same"),
    ]}), encoding="utf-8")
    with pytest.raises(ValidationError, match="unique name"):
        load_config(str(path))


def test_two_nameless_records_on_one_net_stay_fatal(tmp_path):
    """Backward compatible: with no name: the effective name IS the net, so the
    old "one record per net" fatal still fires — no silent override."""
    path = tmp_path / "board.sexp"
    path.write_text(dict_to_sexp({"net_traces": [
        _legacy_record(), _legacy_record(),
    ]}), encoding="utf-8")
    with pytest.raises(ValidationError, match="unique name"):
        load_config(str(path))


def test_empty_name_is_fatal(tmp_path):
    path = tmp_path / "board.sexp"
    path.write_text(dict_to_sexp({"net_traces": [
        _legacy_record(name="   "),
    ]}), encoding="utf-8")
    with pytest.raises(ValidationError, match="empty name"):
        load_config(str(path))


# ── serialization ─────────────────────────────────────────────────────────

def test_legacy_record_serializes_without_a_name_key():
    nt = NetTrace(net="DAC_DB0", anchor_role="FPGA")
    assert "name" not in net_trace_to_dict(nt)


def test_named_record_serializes_with_its_name():
    nt = NetTrace(net="DAC_DB0", anchor_role="FPGA", name="dac_db0__a__b")
    assert net_trace_to_dict(nt)["name"] == "dac_db0__a__b"


def test_legacy_profile_load_and_save_is_byte_identical(tmp_path):
    """The mandatory Э2 compatibility test: an existing profile (no name:
    anywhere) is loaded and written back BYTE-IDENTICALLY, and re-emitting the
    record from the LOADED MODEL adds no name: key either."""
    original = dict_to_sexp({"net_traces": [_legacy_record()]})
    path = tmp_path / "board.sexp"
    path.write_text(original, encoding="utf-8")

    cfg, _ctx = load_config(str(path))
    assert cfg.net_traces[0].name is None

    # The real Save path: dict read -> dict write.
    write_data(path, read_data(path))
    assert path.read_text(encoding="utf-8") == original

    # Model -> dict -> s-expr reproduces the input text exactly (no name: key).
    reemitted = dict_to_sexp({"net_traces": [net_trace_to_dict(cfg.net_traces[0])]})
    assert reemitted == original
    assert "name" not in sexp_to_dict(reemitted)["net_traces"][0]


def test_named_record_survives_a_save(tmp_path):
    """A record WITH name: survives load -> save byte-identically and keeps the
    name as its identity. (The fixture is produced through net_trace_to_dict,
    exactly like a real save: in s-expr the field order follows the dataclass,
    so a hand-written file with the nodes in another order is normalized on
    save — the same convention every other field already has.)"""
    named = NetTrace(net="DAC_DB0", anchor_role="FPGA", name="dac_db0__a__b")
    original = dict_to_sexp({"net_traces": [net_trace_to_dict(named)]})
    path = tmp_path / "board.sexp"
    path.write_text(original, encoding="utf-8")

    cfg, _ctx = load_config(str(path))
    write_data(path, read_data(path))
    assert path.read_text(encoding="utf-8") == original
    assert net_trace_effective_name(cfg.net_traces[0]) == "dac_db0__a__b"
    assert dict_to_sexp({"net_traces": [net_trace_to_dict(cfg.net_traces[0])]}) \
        == original


# ── name generation ──────────────────────────────────────────────────────

def test_generate_trace_name_uses_the_net_leaf_and_sorted_nodes():
    """`<net leaf>__<node A>__<node B>`, lowercased, unsafe chars collapsed —
    the shape of the design's example."""
    assert generate_trace_name("/FPGA/SPI_CLK", ["ch0_dac", "fpga"]) == \
        "spi_clk__ch0_dac__fpga"


def test_generate_trace_name_does_not_depend_on_traversal_order():
    assert generate_trace_name("SPI_CLK", ["fpga", "ch0_dac"]) == \
        generate_trace_name("SPI_CLK", ["ch0_dac", "fpga"])


def test_generate_trace_name_sanitizes_unsafe_characters():
    assert generate_trace_name("/Channel_0/DAC/+3V3_AVDD", ["ch1"]) == \
        "3v3_avdd__ch1"


def test_generate_trace_name_collision_gets_a_numeric_suffix():
    existing = {"spi_clk__a__b", "spi_clk__a__b_2"}
    assert generate_trace_name("SPI_CLK", ["a", "b"], existing) == \
        "spi_clk__a__b_3"
    assert generate_trace_name("SPI_CLK", ["a", "b"], set()) == "spi_clk__a__b"


def test_generate_trace_name_is_stable_for_the_same_edge():
    """Two captures of the same pair of nodes produce the same base name —
    which is exactly why the caller must reuse an existing record instead of
    generating a second one."""
    first = generate_trace_name("SPI_CLK", ["fpga", "ch0_dac"], set())
    second = generate_trace_name("SPI_CLK", ["fpga", "ch0_dac"], set())
    assert first == second


# ── the tree node follows the identity (link_trees) ───────────────────────

def test_tree_node_ref_resolves_by_the_record_name():
    """The node's ref is the record's effective NAME: link_trees builds its key
    from net_trace_effective_name, so a named record is found by its name while
    a legacy record is still found by its net."""
    tree = Tree(name="t", anchor=TreeAnchor(is_origin=True), nodes=[
        TreeNode(ref="dac_db0__a__b", kind="net_trace", xy=None, polar=None,
                 rotation=0.0, name=None, group=None, children=[]),
        TreeNode(ref="LEGACY_NET", kind="net_trace", xy=None, polar=None,
                 rotation=0.0, name=None, group=None, children=[]),
    ])
    cfg = Config(net_traces=[
        NetTrace(net="DAC_DB0", anchor_role="FPGA", name="dac_db0__a__b"),
        NetTrace(net="LEGACY_NET", anchor_role="FPGA"),
    ])
    linked = link_trees(cfg, [tree])
    assert [n.record.name if n.record else None for n in linked[0].nodes] == \
        ["dac_db0__a__b", "LEGACY_NET"]
