# tests/config/test_retired_sections.py
"""Д1 (plan_2026_10_08_remove_spokes.md, Б4): a config file that still carries
the retired `chains:` section — or its pre-2026-09-01 `rules:` spelling — is
REFUSED while its text is parsed, and NOTHING rewrites such a file.

The ONE point is `sexp_format.sexp_to_dict` (the "file text -> dict" step) for
.s-expr, and `normalize_section_aliases` (the raw-dict entry every dict reader
shares) for .json; both call config/retired_sections.py. The loader, the
read-modify-write config_writer and the on-disk format lift all read through
those, so the section can never be dropped without a word:

  * the on-disk lift would REBUILD the file from the parsed dict — chains gone;
  * a read-modify-write would write its own dict back — chains gone.

An EMPTY `(chains)` is not a refusal: it is accepted and simply does not survive
into the dict, so the next write drops it (the documented behaviour).

Rule 35 table (behaviour x survivor), each row its own cell:
  * non-empty, root file          -> fatal, names the FILE and the RECORDS;
  * non-empty, INCLUDED file      -> fatal, names THAT file (never the root);
  * legacy `(rules ...)` spelling -> the same fatal;
  * empty section                 -> accepted, gone after the write;
  * the on-disk lift              -> fatal, bytes untouched, no `.bak`;
  * a read-modify-write           -> fatal, bytes untouched;
  * .json                         -> the same fatal (the dict path).
"""
import json
from pathlib import Path

import pytest

from kicadstamp.config.loader import load_config
from kicadstamp.config.upgrade_on_disk import upgrade_graph_on_disk
from kicadstamp.config_writer import merge_write
from kicadstamp.exceptions import ValidationError

# A file written by an OLDER build: the retired section is the only thing that
# makes it unreadable now. No version node, so the on-disk lift would normally
# take it to the current format — and that is exactly the write the refusal
# must stop.
CHAINED_SEXP = (
    '(kicadstamp-config\n'
    '  (cells (cell "c" (layer "B.Cu")))\n'
    '  (chains\n'
    '    (chain (name "ch1") (net "GND") (anchor_ref "U1"))))\n')

EMPTY_CHAINED_SEXP = (
    '(kicadstamp-config\n'
    '  (cells (cell "c" (layer "B.Cu")))\n'
    '  (chains))\n')


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_a_non_empty_chains_section_is_refused_naming_the_file_and_records(tmp_path):
    root = _write(tmp_path, "root.sexp", CHAINED_SEXP)

    with pytest.raises(ValidationError) as e:
        load_config(str(root))

    msg = str(e.value)
    assert "no longer supported" in msg
    assert str(root) in msg          # the FILE
    assert "ch1" in msg              # the record's identity


def test_a_non_empty_chains_section_in_an_included_file_names_THAT_file(tmp_path):
    """The refusal lives at the parse of EACH file, so the message points at the
    file that actually carries the section — not at the root that includes it."""
    _write(tmp_path, "sub.sexp", CHAINED_SEXP)
    root = _write(tmp_path, "root.sexp",
                  '(kicadstamp-config (include "sub.sexp"))\n')

    with pytest.raises(ValidationError) as e:
        load_config(str(root))

    msg = str(e.value)
    assert "no longer supported" in msg
    assert str(tmp_path / "sub.sexp") in msg
    assert str(root) not in msg


def test_the_legacy_rules_spelling_is_refused_the_same_way(tmp_path):
    root = _write(tmp_path, "root.sexp",
                  '(kicadstamp-config\n'
                  '  (cells (cell "c" (layer "B.Cu")))\n'
                  '  (rules\n'
                  '    (chain (name "ch1") (net "GND") (anchor_ref "U1"))))\n')

    with pytest.raises(ValidationError) as e:
        load_config(str(root))

    msg = str(e.value)
    assert "no longer supported" in msg
    assert "legacy" in msg and "rules" in msg
    assert "ch1" in msg


def test_an_empty_chains_section_is_accepted_and_dropped_by_the_next_write(tmp_path):
    """The documented counterpart of the refusal: an empty section is legal and
    must NOT survive into the dict — otherwise every write would re-emit it."""
    root = _write(tmp_path, "root.sexp", EMPTY_CHAINED_SEXP)

    cfg, _ctx = load_config(str(root))       # no raise

    assert cfg.cells                      # the rest of the file loaded
    assert "(chains)" not in root.read_text(encoding="utf-8")


def test_the_on_disk_lift_refuses_and_leaves_the_bytes_and_no_bak(tmp_path):
    root = _write(tmp_path, "root.sexp", CHAINED_SEXP)
    before = root.read_bytes()

    with pytest.raises(ValidationError):
        upgrade_graph_on_disk(str(root))

    assert root.read_bytes() == before, "the file must be byte-for-byte untouched"
    assert list(tmp_path.glob("*.bak.*")) == [], "no write means no .bak"


def test_a_read_modify_write_refuses_and_leaves_the_file_untouched(tmp_path):
    target = _write(tmp_path, "cfg.sexp", CHAINED_SEXP)
    before = target.read_bytes()

    # config_writer wraps a ValidationError from its own read in OSError (the
    # GUI docks' `except OSError` contract) — either way the write never starts.
    # The message is pinned to the RETIREMENT refusal, not just "some parse
    # error": this read path goes straight to sexp_to_dict (no alias pass), so
    # it is the node-level check that must fire here.
    with pytest.raises((OSError, ValidationError)) as e:
        merge_write(target, {"cells": {"c2": {}}}, section="cells")
    assert "no longer supported" in str(e.value)

    assert target.read_bytes() == before


def test_a_json_config_with_chains_is_refused_by_the_dict_path(tmp_path):
    """The .json reader never goes through sexp_to_dict, so it gets the same
    refusal from normalize_section_aliases — otherwise the section would be
    dropped silently by the next write of that file."""
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"cells": {"c": {}},
                             "chains": [{"net": "GND", "name": "ch1"}]}),
                 encoding="utf-8")

    with pytest.raises(ValidationError) as e:
        load_config(str(p))

    assert "no longer supported" in str(e.value)
