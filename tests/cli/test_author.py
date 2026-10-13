#!/usr/bin/env python3
"""Tests for kicadstamp/author.py — build ClonePlacement in Python, dump back
to s-expr, or feed straight into the apply pipeline (2026-08-28,
core_yaml_removal: author fragments are s-expr/.sexp now)."""

import pytest
from unittest.mock import patch

from kicadstamp.config import ClonePlacement, Config, load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import ValidationError
from kicadstamp.author import apply_config, dump_template
from kicadstamp.apply_pipeline import RunOptions
from tests.fakes.format3 import format3  # noqa: F401  (pytest fixture: pin to 3)


def _fragment_profile_root(tmp_path, cells, fragment_name):
    """A profile root that INCLUDES the fragment, plus the fragment as a
    placeholder (walk_include_tree needs the include file to exist before the
    first dump overwrites it).

    The dump writes a FRAGMENT: under format 3 its references resolve against
    the profile root the fragment is included into (У3.5 Ф2), so the root must
    carry the referenced cells: records. Loading the root (not the fragment
    alone) is the only shape a format-3 fragment round-trips through — a bare
    fragment has no graph to resolve its own references against. Under format 2
    the root is inert and the merge is the same as before.
    """
    (tmp_path / fragment_name).write_text(
        dict_to_sexp({"cells": {}}, format_number=2), encoding="utf-8")
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp(
        {"cells": {name: {} for name in cells}, "include": [fragment_name]},
        format_number=2), encoding="utf-8")
    return root


class TestApplyConfig:
    def test_builds_runoptions_with_every_field_run_apply_reads(self):
        """Regression guard: if run_apply grows a new RunOptions field, this
        test must be updated too — otherwise apply_config would silently
        stop forwarding it and fail at runtime."""
        cfg = Config()
        with patch("kicadstamp.author.run_apply") as mock_run_apply:
            apply_config(cfg, "my_run.yaml", dry_run=True, only=["a"], cluster=["b"],
                        timeout_ms=1234, batch_size=5, no_collision_check=True,
                        collision_margin=0.5)

        mock_run_apply.assert_called_once()
        options = mock_run_apply.call_args.args[0]
        assert isinstance(options, RunOptions)
        assert mock_run_apply.call_args.kwargs["cfg"] is cfg
        assert options.config_path == "my_run.yaml"
        assert options.dry_run is True
        assert options.only == ["a"]
        assert options.cluster == ["b"]
        assert options.timeout_ms == 1234
        assert options.batch_size == 5
        assert options.no_collision_check is True
        assert options.collision_margin == 0.5

    def test_defaults_match_cli_defaults(self):
        cfg = Config()
        with patch("kicadstamp.author.run_apply") as mock_run_apply:
            apply_config(cfg, "my_run.yaml")

        options = mock_run_apply.call_args.args[0]
        assert options.dry_run is False
        assert options.only is None
        assert options.cluster is None
        assert options.no_collision_check is False
        assert options.collision_margin == 0.2

    def test_forwards_ctx_to_run_apply(self):
        """Regression: ctx (RuntimeContext, carries sheet_names built from
        schematic_dir) must reach run_apply — otherwise anchor_sheet-based
        clone_placements fatal with "sheet name dictionary is empty" even
        though schematic_dir was set and parsed correctly."""
        cfg = Config()
        ctx = object()
        with patch("kicadstamp.author.run_apply") as mock_run_apply:
            apply_config(cfg, "my_run.yaml", ctx=ctx)

        assert mock_run_apply.call_args.kwargs["ctx"] is ctx

    def test_ctx_defaults_to_none(self):
        cfg = Config()
        with patch("kicadstamp.author.run_apply") as mock_run_apply:
            apply_config(cfg, "my_run.yaml")

        assert mock_run_apply.call_args.kwargs["ctx"] is None


class TestDumpTemplate:
    def test_writes_template_dict_wrapped_in_cells(self, tmp_path):
        """Wrapped under 'cells:' since 2026-08-02 (cells_file:/cell_files:
        folded into include:, which expects the same wrapped shape as an
        inline cells: block)."""
        template_dict = {"cap_pair_standard": {"components": [
            {"role": "C_IN_BULK", "offset_along_mm": 0.0, "offset_across_mm": 0.0, "angle_deg": 0.0},
        ]}}
        out = tmp_path / "cell.sexp"
        dump_template(template_dict, str(out))

        loaded = sexp_to_dict(out.read_text(encoding="utf-8"))
        # The s-expr writer default-strips the component's 0.0 offsets — check the
        # surviving required field (role) and the wrapped cells: key.
        assert set(loaded["cells"]) == {"cap_pair_standard"}
        assert loaded["cells"]["cap_pair_standard"]["components"][0]["role"] == "C_IN_BULK"

    def test_overwrites_rather_than_merges(self, tmp_path):
        """Unlike cmd_extract's merge-into-existing behaviour, dump_template
        always overwrites — a script regenerating its own dedicated file
        should get a clean result, not accumulate stale entries."""
        from tests.fakes.format3 import without_identity
        out = tmp_path / "cell.sexp"
        dump_template({"old_name": {"components": []}}, str(out))
        dump_template({"new_name": {"components": []}}, str(out))

        loaded = sexp_to_dict(out.read_text(encoding="utf-8"))
        # default-stripped canonical form (empty components: [] is omitted by the
        # s-expr writer) — the important invariant is overwrite, not accumulate.
        # У3.5 К3, row 7: the format-3 write stamps a uuid on the record; the
        # subject here is overwrite-vs-accumulate, so compare without it.
        assert without_identity(loaded) == {"cells": {"new_name": {}}}


@pytest.fixture(autouse=True)
def _active_graph_root(tmp_path):
    """У3.5 A, class (в): the format-3 writer resolves a reference's UUID against
    the ACTIVE GRAPH ROOT. These cells write a self-contained config; the root is
    a path that does NOT exist, so the stamp indexes THIS write's own records
    (config/format3._build_format3_index) — the format-3 product path, no
    on-disk graph walked. Under format 2 (< 3) the root is never consulted."""
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(tmp_path / "active_root.sexp")
    yield
    set_active_graph_root(None)
