# tests/test_config_role_cluster_source.py
"""Т3 of plan_2026_09_18_field_overrides_store.md: the Role/Cluster SOURCE
switch lives in the PROFILE CONFIG (`role_cluster_source`), not in the GUI's own
state.

Why the config and not `gui_state.json` (design §2.3 / plan Т3): a switch living
in GUI state would be invisible to the CLI, and ONE profile would resolve
differently in the GUI and in the CLI — the worst class of bug ("it redrew right
for me, not on the build"). The rule has to be the same for GUI, CLI and MCP.

Guards:
  * an ABSENT key reads as "registry" — the default, so every profile written
    before this change keeps loading (Т7 migration);
  * an explicit non-default value survives the s-expr round-trip;
  * the DEFAULT value is omitted from the file (the per-field default rule);
  * an unknown value is FATAL — a typo must never silently fall back to the
    board (which would look like "the store stopped working").
"""
from pathlib import Path

import pytest

from kicadstamp.config.loader import load_config
from kicadstamp.config.models import Config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.constants import (ROLE_CLUSTER_SOURCE_BOARD,
                                  ROLE_CLUSTER_SOURCE_REGISTRY,
                                  ROLE_CLUSTER_SOURCES)
from kicadstamp.exceptions import ValidationError


def _write(tmp_path, text: str) -> Path:
    path = tmp_path / "config.sexp"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_vocabulary_is_exactly_board_and_registry():
    assert ROLE_CLUSTER_SOURCES == (ROLE_CLUSTER_SOURCE_REGISTRY,
                                    ROLE_CLUSTER_SOURCE_BOARD)


def test_default_is_registry():
    """The LITERALS are pinned, not the constants they are compared through.

    Found by mutation М-default, 2026-09-18: asserting `Config().role_cluster_
    source == ROLE_CLUSTER_SOURCE_REGISTRY` was a tautology — both sides follow
    the same constant, so the guard stayed green even when the constant itself
    became "board", i.e. when the default silently flipped to the pre-store
    behaviour."""
    assert ROLE_CLUSTER_SOURCE_REGISTRY == "registry"
    assert ROLE_CLUSTER_SOURCE_BOARD == "board"
    assert Config().role_cluster_source == "registry"


def test_absent_key_reads_as_registry(tmp_path):
    """The migration guard (Т7): a profile written before this change has no
    such key and must keep loading, with the registry as its source."""
    path = _write(tmp_path, '(kicadstamp-config (layer "B.Cu"))')

    cfg, _ctx = load_config(str(path))

    assert cfg.role_cluster_source == ROLE_CLUSTER_SOURCE_REGISTRY


def test_explicit_board_loads_and_round_trips(tmp_path):
    path = _write(tmp_path, '(kicadstamp-config (layer "B.Cu") '
                            '(role_cluster_source "board"))')

    cfg, _ctx = load_config(str(path))

    assert cfg.role_cluster_source == ROLE_CLUSTER_SOURCE_BOARD
    back = sexp_to_dict(dict_to_sexp({"layer": "B.Cu",
                                      "role_cluster_source": "board"}))
    assert back["role_cluster_source"] == ROLE_CLUSTER_SOURCE_BOARD


def test_the_default_value_is_omitted_from_the_file():
    back = sexp_to_dict(dict_to_sexp({"role_cluster_source": ROLE_CLUSTER_SOURCE_REGISTRY}))

    assert "role_cluster_source" not in back


def test_an_unknown_source_is_fatal(tmp_path):
    path = _write(tmp_path, '(kicadstamp-config (role_cluster_source "registryy"))')

    with pytest.raises(ValidationError):
        load_config(str(path))
