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


# ── create_project(): the project directory and what is born with it ───────
#
# 2026-09-24, SECOND step — Denis: "В File будет «Создать проект». Открываем
# диалог создания проекта. Там указываем имя, как в кикад, автоматически
# создаётся директория с нужной инфраструктурой." The cells live HERE rather than
# in the GUI file because the writing is Qt-free (kicadstamp/project_setup.py): the
# dialog only collects a name and a folder and calls it.

# The literal expectation, deliberately NOT imported: this file must still COLLECT
# on the base commit (that is how Н2's green is measured), and PROJECT_INFRA_DIRS
# does not exist there. Drift between this tuple and the module's own is exactly
# what test_the_infra_dir_list_covers_every_derived_store forbids.
_INFRA = ("registry", "tracks", "logs", "overrides", "operational")


def test_create_project_writes_the_config_and_its_infrastructure(tmp_path):
    """The §5 row, FLIPPED by Denis on 2026-09-24 ("автоматически создаётся
    директория с нужной инфраструктурой") — consciously overriding the 2026-09-11
    decision that every consumer creates its own directory on demand. A new project
    is now born with the five, and the row is the WHOLE listing: the config plus
    exactly those five, nothing else."""
    from kicadstamp.project_setup import create_project

    project = tmp_path / "Clean-Project"
    config = create_project(project)

    assert config == project / "Clean-Project.sexp"
    # The file IS a valid empty config, and it is written by the product's OWN
    # writer: since Т3 that writer stamps the FORMAT number, so the expected text
    # is produced by asking the writer rather than by pinning a literal that
    # would have to be kept in step with CURRENT_FORMAT by hand.
    from kicadstamp.config.sexp_format import dict_to_sexp

    assert config.read_text(encoding="utf-8").strip() == dict_to_sexp({}).strip()
    assert sorted(p.name for p in project.iterdir()) == sorted(
        ["Clean-Project.sexp", *_INFRA])


@pytest.mark.parametrize("name", _INFRA)
def test_each_infrastructure_directory_is_created(tmp_path, name):
    """One row per directory (rule 35), so a missing one names itself instead of
    leaving "the listing differs" to be diffed by eye — and each is asserted
    EMPTY, because creating a project must not start writing stores into them."""
    from kicadstamp.project_setup import create_project

    project = create_project(tmp_path / "Proj")

    assert (project.parent / name).is_dir(), name
    assert list((project.parent / name).iterdir()) == []


def test_create_project_creates_the_directory_and_its_parents(tmp_path):
    """The dialog asks for a name and a folder, so the directory is frequently
    NEW and sometimes more than one level deep — the creation must make it, not
    fail on a missing parent."""
    from kicadstamp.project_setup import create_project

    config = create_project(tmp_path / "deep" / "er" / "Proj")

    assert config == tmp_path / "deep" / "er" / "Proj" / "Proj.sexp"
    assert config.is_file()


def test_create_project_refuses_to_overwrite_an_existing_config(tmp_path):
    """Н4's core half: an existing config is never overwritten — the call raises
    ProjectConfigExists BEFORE writing, and the bytes on disk stay as they were.
    The dialog's half (a warning box, one ERROR line, the dialog left open) is in
    tests/gui/test_create_project_dialog.py."""
    from kicadstamp.project_setup import ProjectConfigExists, create_project

    project = tmp_path / "taken"
    (project / "registry").mkdir(parents=True)
    existing = project / "taken.sexp"
    existing.write_text('(kicadstamp-config\n  (layer "B.Cu"))\n', encoding="utf-8")
    before = existing.read_text(encoding="utf-8")

    with pytest.raises(ProjectConfigExists):
        create_project(project)

    assert existing.read_text(encoding="utf-8") == before
    # Nothing was added either: the refusal happens before ANY write.
    assert sorted(p.name for p in project.iterdir()) == ["registry", "taken.sexp"]


def test_the_infra_dir_list_covers_every_derived_store(tmp_path):
    """The drift guard. Every directory the five default builders point into (the
    containing directory for the four file-returning ones, the path itself for
    `operational/`) must be one of PROJECT_INFRA_DIRS — otherwise adding a sixth
    derived store silently produces projects that are missing its directory, and
    nobody sees it until a consumer writes into a path that was never made."""
    from kicadstamp.project_setup import create_project
    from kicadstamp.utils.paths import (PROJECT_INFRA_DIRS,
                                        default_log_file_for_config,
                                        default_operation_log_dir_for_config,
                                        overrides_path_for_config,
                                        registry_path_for_config,
                                        track_registry_path_for_config)

    project = create_project(tmp_path / "Proj")
    config = str(project)
    file_builders = (registry_path_for_config, track_registry_path_for_config,
                     overrides_path_for_config, default_log_file_for_config)
    derived = {Path(bst(config)).parent for bst in file_builders}
    derived.add(Path(default_operation_log_dir_for_config(config)))

    assert derived == {project.parent / d for d in PROJECT_INFRA_DIRS}


# ── ДОПОЛНЕНИЕ 1 (24.09.2026): the name, and what may be opened ─────────────

@pytest.mark.parametrize("tail", [".", ".."], ids=["dot", "dot-dot"])
def test_a_relative_directory_name_is_refused_by_the_core(tmp_path, tail):
    """Д4 (ДОПОЛНЕНИЕ 1, Д-1): `project_config_path_for_dir` refuses on its own, with
    no dialog in the picture — so EVERY caller is covered, not just the widget.

    The raw TEXT is what carries the information here, and that is the whole point:
    ``Path(folder) / "."`` IS ``Path(folder)`` — pathlib eats the dot at construction —
    so a Path-shaped argument has already lost it, while ``..`` survives (pathlib does
    not resolve it) and is caught either way. A string keeps both, which is why the
    refusal reads the raw last component before Path() normalises anything."""
    from kicadstamp.utils.paths import project_config_path_for_dir

    picked = tmp_path / "picked"
    picked.mkdir()

    with pytest.raises(ValueError):
        project_config_path_for_dir(f"{picked}/{tail}")
    # and the ".." form survives even as a Path, because pathlib keeps it
    if tail == "..":
        with pytest.raises(ValueError):
            project_config_path_for_dir(picked / tail)


def test_a_project_name_with_a_dot_keeps_its_full_stem(tmp_path):
    """Д5 (ДОПОЛНЕНИЕ 1, Д-2). The code was RIGHT (`p.name`, not `p.stem`) and nothing
    held it: Denis's rig substituted stem for name and every cell stayed green. The
    price of that regression: a project called `v1.03` would get a config named
    `v1.sexp`, and the three stem-derived stores would follow it — the §4 trap
    arriving from the other side. Version-shaped names are where it shows."""
    from kicadstamp.project_setup import create_project
    from kicadstamp.utils.paths import (overrides_path_for_config,
                                        registry_path_for_config,
                                        track_registry_path_for_config)

    project = tmp_path / "v1.03"
    config = create_project(project)

    assert config == project / "v1.03.sexp"
    assert sorted(p.name for p in project.iterdir()) == sorted(
        ["v1.03.sexp", *_INFRA])
    for builder in (registry_path_for_config, track_registry_path_for_config,
                    overrides_path_for_config):
        derived = Path(builder(str(config)))
        assert derived.parent.parent == project, derived
        assert derived.name.startswith("v1.03."), derived
