# tests/test_project_is_a_directory.py
"""Cells for plan_2026_09_24_project_is_a_directory — "a project is CREATED as a
DIRECTORY and OPENED as a file" (Denis, 2026-09-24: "У KiCad создаётся проект
директорией, а открывается файл проекта. Вот так и делаем").

WHY THE NEW NAME IS IMPORTED INSIDE THE TESTS. Н2 is a REGRESSION cell: it must
be GREEN on the base commit, and the only honest way to measure that is to run
THIS file on a detached base worktree (rule 34). A module-level
``from kicadstamp.utils.paths import project_config_path_for_dir`` would fail at
COLLECTION there — the name does not exist on the base — and rule 38's second
fuse says a run that collected nothing proves nothing. So the module imports only
names that exist on the base, and every test that needs the new builder imports
it itself; on the base those cells then fail as ordinary FAILED lines (red for
the right reason), which is exactly what the plan predicts for a cell standing on
a NEW seam.

The table (rule 35 — property x direction x branch, every row parametrized so one
failing row does not hide the others):

  * the NAME of a new project's config : comes from the DIRECTORY, not from a
    fixed "config" and not from the previously open root (Н1; mutation н1);
  * where the derived stores land      : INSIDE the project directory —
    registry/, tracks/, overrides/ (Н3; mutation н2);
  * the STEM of those derived stores   : the CONFIG stem, so an OLD profile
    keeps config.registry.json on disk (Н2, the §4 regression cell; mutation н5);
  * a directory with no name ("/")     : loud ValueError, never a bare ".sexp".

NOT here (deliberately, so the split is visible): "a directory that already holds
the config is refused" (Н4) and "only the config file, no infrastructure dirs"
(Н5) are properties of the DOCK's creation flow, so they live in
tests/gui/test_root_metadata.py next to the other Open/New/Recent guards.
"""
from pathlib import Path

import pytest

from kicadstamp.utils.paths import (overrides_path_for_config,
                                    registry_path_for_config,
                                    track_registry_path_for_config)

# (builder, subfolder, tail) — the three stem-derived machine stores, in the
# order the plan lists them (§2). Kept as data, not as three near-identical
# functions, so a new store means a new row rather than a new test.
STORES = [
    (registry_path_for_config, "registry", "registry.json"),
    (track_registry_path_for_config, "tracks", "tracks.registry.json"),
    (overrides_path_for_config, "overrides", "fields.json"),
]
STORE_IDS = ["registry", "tracks", "overrides"]


@pytest.mark.parametrize("as_path", [False, True], ids=["str", "Path"])
def test_new_project_config_is_named_after_its_directory(tmp_path, as_path):
    """Н1 (plan_2026_09_24_project_is_a_directory), the NAME row, both input
    flavours: QFileDialog hands back a ``str`` and the dock's own callers hold a
    ``Path``, so both directions are pinned — and the builder must be PURE (it
    computes a path, it does not create anything)."""
    from kicadstamp.utils.paths import project_config_path_for_dir

    d = tmp_path / "HiPiMS-v099"
    d.mkdir()
    arg = d if as_path else str(d)

    assert project_config_path_for_dir(arg) == str(d / "HiPiMS-v099.sexp")
    # Pure: nothing was written by asking for the path.
    assert list(d.iterdir()) == []


def test_new_project_name_ignores_the_previously_open_root(tmp_path):
    """Н1, the other direction of the name row: the OLD dialog defaulted the
    filename to the CURRENT root's stem ("<open-root-stem>.sexp"). The directory
    is the only source of the name now — a leftover stem cannot leak in."""
    from kicadstamp.utils.paths import project_config_path_for_dir

    d = tmp_path / "Fresh-Project"
    d.mkdir()

    assert project_config_path_for_dir(d) == str(d / "Fresh-Project.sexp")
    assert "config" not in project_config_path_for_dir(d).split("/")[-1]


@pytest.mark.parametrize("builder, subfolder, tail", STORES, ids=STORE_IDS)
def test_derived_stores_of_a_new_project_point_inside_its_directory(
        tmp_path, builder, subfolder, tail):
    """Н3 (plan_2026_09_24_project_is_a_directory): every derived store of a
    project created this way lands INSIDE its own directory —
    ``<dir>/<subfolder>/<dir>.<tail>`` — not beside the directory and not in its
    parent. All three stores are their own parametrized row because "inside" is
    a claim about each of them, and a half-applied path rule is worse than none."""
    from kicadstamp.utils.paths import project_config_path_for_dir

    d = tmp_path / "HiPiMS-v099"
    d.mkdir()
    config = project_config_path_for_dir(d)

    derived = Path(builder(config))
    assert derived == d / subfolder / f"HiPiMS-v099.{tail}"
    assert derived.parent.parent == d, "must be inside the project dir, not beside"
    assert derived.parent.parent != d.parent, "and never in the parent directory"


def test_an_old_profile_keeps_its_config_stem_in_the_derived_stores(tmp_path):
    """Н2 (plan_2026_09_24_project_is_a_directory) — **THE §4 REGRESSION CELL,
    and the only cell in this file that is GREEN on the base commit.**

    An existing profile carries ``config.sexp``, and the stores already on disk
    are named after THAT stem. The trap §4 is about: if a later "let us unify the
    names" pass made the derived names follow the DIRECTORY instead, the code
    would look for ``registry/<dir>.registry.json``, find nothing, read the
    registry as EMPTY, and the next redraw would DOUBLE the copper already on the
    board.

    Reproduced on a tmp replica rather than on the live profile (profiles/ is
    live data, read-only): the live numbers measured 2026-09-24 are
    registry/config.registry.json = 96 393 bytes and
    tracks/config.tracks.registry.json = 310 983 bytes, and the live config
    declares none of the four path keys — so those names came from the stem
    default, and they are the only names that lead to the real files.
    """
    d = tmp_path / "3ch-awg-tia-v103"
    cfg = d / "config.sexp"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("(kicadstamp-config)\n", encoding="utf-8")
    on_disk = {}
    for builder, subfolder, tail in STORES:
        store = d / subfolder / f"config.{tail}"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("{}\n", encoding="utf-8")
        on_disk[builder] = store

    for builder, subfolder, tail in STORES:
        derived = Path(builder(str(cfg)))
        assert derived == on_disk[builder], "the stem of the CONFIG, not of the dir"
        assert derived.exists(), "and it must point at the file that IS there"
        # The directory-name form is exactly what must NOT be produced here.
        assert derived.name == f"config.{tail}"
        assert derived.name != f"{d.name}.{tail}"


@pytest.mark.parametrize("raw", ["/", "//"], ids=["root", "double-slash"])
def test_a_project_directory_without_a_name_is_refused_loudly(raw):
    """The empty-cell edge (rule 35): a filesystem root has no name, so there is
    nothing to name the project after. That must be a loud ValueError, never a
    silent ``/.sexp`` that the user cannot see or fix."""
    from kicadstamp.utils.paths import project_config_path_for_dir

    with pytest.raises(ValueError):
        project_config_path_for_dir(raw)
