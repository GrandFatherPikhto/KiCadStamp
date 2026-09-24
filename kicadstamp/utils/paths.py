# kicadstamp/utils/paths.py

from pathlib import Path, PureWindowsPath


def resolve_config_relative_path(base_dir: Path, raw: str) -> str:
    """Resolves a path from a YAML config value against the config file's
    directory, unless ``raw`` is already absolute.

    ``Path(base_dir) / raw`` only recognizes ``raw`` as absolute per the
    current OS's flavor: on POSIX it discards ``base_dir`` for a leading
    ``/``, but a Windows-style absolute path (``C:/tmp/run.log``) has no
    leading ``/`` and gets silently joined onto ``base_dir`` instead of kept
    as-is. Checking both flavors here keeps config values portable across
    the OS that authored them and the OS that loads them.
    """
    if Path(raw).is_absolute() or PureWindowsPath(raw).is_absolute():
        return str(Path(raw))
    return str(base_dir / raw)


# ── Config-derived default paths (2026-09-04, plan root_metadata_path_defaults)
#
# These four "default-for-config" helpers answer "where does THIS config keep
# its registry / logs by default?" — always derived FROM the config file path,
# never resolved against CWD. Hosted HERE (kicadstamp/utils/paths.py), not in
# kicadstamp/registry.py, deliberately: cli_common's peek_log_file/
# peek_operation_log_dir and the GUI's RootMetadataDock run before the heavy
# placement/registry modules are imported, and kicadstamp.registry imports
# placement.commands -> placement.executor (which imports registry back) — a
# latent import cycle that only ever resolves when registry is reached THROUGH
# placement. Pure path builders must stay import-light so a fresh CLI/GUI
# process can compute defaults without tripping that cycle.
#
# kicadstamp.registry re-exports these four names, so the public API
# (`from kicadstamp.registry import registry_path_for_config, ...`) is
# unchanged for all existing consumers (apply_pipeline, placer dock, tests).


def project_config_path_for_dir(project_dir: str | Path) -> str:
    """<project-dir> -> <project-dir>/<project-dir-name>.sexp.

    The "the project is a DIRECTORY" rule (2026-09-24, Denis: "У KiCad
    создаётся проект директорией, а открывается файл проекта. Вот так и
    делаем"). Picking (or making) a directory is the whole act of creation; the
    config inside it is NAMED AFTER THAT DIRECTORY and its name is never asked
    for separately — exactly as KiCad writes ``<dir>/<dir>.kicad_pro``.

    The name reaches further than the config itself: the three stem-derived
    machine stores below take their file names from the CONFIG STEM, so a
    project created this way is self-consistent out of the box
    (``registry/<dir>.registry.json`` and friends).

    CREATION ONLY. This must never be used to "unify" the names of EXISTING
    profiles: they keep their ``config.sexp`` stem, and the stores already on
    disk are named after THAT stem. Renaming a live ``config.sexp`` to
    ``<dir>.sexp`` makes :func:`registry_path_for_config` point at a file that
    does not exist, the registry then reads as EMPTY, and the next redraw
    DOUBLES the copper already on the board. Measured 2026-09-24 on
    ``profiles/3ch-awg-tia-v103``: ``registry/config.registry.json`` is 96 393
    bytes and ``tracks/config.tracks.registry.json`` 310 983 bytes, and the
    config declares none of the four path keys — so those names came from the
    stem default, and they are the only names that lead to the real files.
    """
    # The RAW last component, read BEFORE Path() normalises it away: Path("x/.") IS
    # Path("x"), so by the time p.name is asked the "." is gone and a name check
    # there would silently MISS it (2026-09-24, ДОПОЛНЕНИЕ 1, Д-1).
    #
    # Neither "." nor ".." contains a path separator, so the dialog's separator
    # guard cannot see them either: "." makes the target EQUAL the picked folder (no
    # project directory is created at all) and ".." walks OUT of it. Checking "is
    # the target's parent the picked folder" looks tidier and does NOT work — for
    # ".." the target IS <picked>/.., whose parent IS <picked>; that was measured,
    # not derived, and this comment exists so it is not reinstated.
    raw = str(project_dir).replace("\\", "/").rstrip("/")
    if raw.rsplit("/", 1)[-1] in (".", ".."):
        raise ValueError(
            f"project directory is a relative path name: {project_dir!r} — "
            "'.' would create nothing and '..' would put the project outside the "
            "folder it was created in")
    p = Path(project_dir)
    if not p.name:
        # A filesystem root ("/") has no name, so there is nothing to name the
        # project after. Refuse loudly here rather than create ".sexp".
        raise ValueError(f"project directory has no name: {project_dir!r}")
    return str(p / (p.name + ".sexp"))


def registry_path_for_config(config_path: str) -> str:
    """<config>.yaml -> <config-dir>/registry/<config-stem>.registry.json.

    CHANGED (2026-09-04, plan root_metadata_path_defaults): the default used
    to live NEXT TO the config itself (<config>.registry.json); it now lives
    in a ``registry/`` SUBFOLDER so several configs in one project keep their
    per-config registry files cleanly separated. The file name inside the
    subfolder keeps the config stem so distinct configs do not collide.
    """
    p = Path(config_path)
    return str(p.parent / "registry" / (p.stem + ".registry.json"))


def track_registry_path_for_config(config_path: str) -> str:
    """<config>.yaml -> <config-dir>/tracks/<config-stem>.tracks.registry.json.

    CHANGED (2026-09-04, plan root_metadata_path_defaults): same subfolder
    move as :func:`registry_path_for_config`, into ``tracks/`` — separate
    from vias, record schema is different (two points+width+layer, not
    drill/diameter).
    """
    p = Path(config_path)
    return str(p.parent / "tracks" / (p.stem + ".tracks.registry.json"))


def overrides_path_for_config(config_path: str) -> str:
    """<config>.sexp -> <config-dir>/overrides/<config-stem>.fields.json.

    The Role/Cluster override store (2026-09-18, plan_2026_09_18_field_
    overrides_store.md Т1) sits NEXT TO the copper registries and follows the
    same discipline as :func:`registry_path_for_config`: one file per config
    stem, in its own subfolder, derived from the config path alone.

    Like the registry, this is MACHINE data, never a config: it is not reachable
    through ``include:``, and it is not meant to travel — to another machine or
    onto another board it would start lying (its key is the symbol uuid of THIS
    board's schematic, and the values are what a human typed for it).
    """
    p = Path(config_path)
    return str(p.parent / "overrides" / (p.stem + ".fields.json"))


def default_log_file_for_config(config_path: str) -> str:
    """<config>.yaml -> <config-dir>/logs/actions.log."""
    p = Path(config_path)
    return str(p.parent / "logs" / "actions.log")


def default_operation_log_dir_for_config(config_path: str) -> str:
    """<config>.yaml -> <config-dir>/operational/."""
    p = Path(config_path)
    return str(p.parent / "operational")


# Every directory a NEW project is born with (2026-09-24, Denis: "автоматически
# создаётся директория с нужной инфраструктурой"). Kept as a tuple NEXT TO the
# five builders above, and pinned against them by
# tests/test_project_is_a_directory.py::test_the_infra_dir_list_covers_every_derived_store,
# so adding a sixth derived store without its directory here fails a CELL instead
# of silently producing projects that are missing it.
#
# This REVERSES the 2026-09-11 decision ("every consumer makes its own directory
# on demand — the reason the Files tab was removed"), deliberately, by Denis on
# 2026-09-24, for projects created FROM NOW ON. An existing profile keeps whichever
# of these directories it happens to have: nothing here reads or writes one.
PROJECT_INFRA_DIRS = ("registry", "tracks", "logs", "overrides", "operational")
