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


def default_log_file_for_config(config_path: str) -> str:
    """<config>.yaml -> <config-dir>/logs/actions.log."""
    p = Path(config_path)
    return str(p.parent / "logs" / "actions.log")


def default_operation_log_dir_for_config(config_path: str) -> str:
    """<config>.yaml -> <config-dir>/operational/."""
    p = Path(config_path)
    return str(p.parent / "operational")
