from collections.abc import Callable
# kicadstamp/cli_common.py
"""
cli_common.py — the single owner of CLI exit codes.

Every CLI front-end (kicadstamp_cli.py's ``main()`` and ``author_cli.cli_main()``
used by ``boards/*/scripts/*.py``) must translate exceptions into process
exit codes. That translation used to be copy-pasted in two places; this
module is the one source of truth so every front-end reports errors
identically and a change to the contract lands in exactly one file.

This is deliberately the ONLY module that owns exit codes. Library code must
never ``sys.exit()`` — it should raise ``PlacerError``/``ValidationError``/
``ApiError`` and let the CLI front-end (via :func:`run_cli`) decide the
exit code and the message.
"""
import json
import logging
from pathlib import Path

from .exceptions import PlacerError
from .i18n import _
from .utils.file_cache import cached_file_read
from .utils.paths import resolve_config_relative_path


def api_error_message(e) -> str:
    """Human-readable message for a KiCad IPC :class:`ApiError`.

    ``AS_BUSY`` gets the long "finish the tool in KiCad" explanation — the
    most common real-world cause and the easiest to misread as a hang. Every
    other code gets a straightforward "KiCad returned API error: ...".

    ``ApiStatusCode`` is imported lazily: non-IPC commands (``flatten``) never
    raise an IPC error, so they never pay for the kipy import chain.
    """
    from kipy.errors import ApiStatusCode
    if e.code == ApiStatusCode.AS_BUSY:
        return _(
            "KiCad is busy and cannot respond right now. Usually this means an unfinished "
            "tool is running in the GUI (dimensioning, interactive routing, move tool, etc.) — "
            "finish it (Esc or right-click -> Cancel) and run the command again. "
            "The board was not modified."
        )
    return _("KiCad returned API error: {e}").format(e=e)


def run_cli(main_fn: Callable[[], None]) -> int:
    """Run a CLI body and translate exceptions into a process exit code.

    *main_fn* — a zero-arg callable with the command's real work. It must
    NOT ``sys.exit()`` itself for its own errors; ``run_cli`` does the
    translation. A ``SystemExit`` raised *inside* ``main_fn`` (e.g. by
    argparse on bad flags, or a deliberately aborted run) is a
    ``BaseException`` and propagates unchanged with its own exit code.

    Returns the exit code:
      - ``0`` on success;
      - ``1`` for ``PlacerError``/``ValidationError`` (expected user-facing
        fatal: bad config, ambiguous role, unknown ``--only``/``--cluster``)
        and for ``ApiError`` (KiCad IPC failure; ``AS_BUSY`` gets a
        dedicated message);
      - ``2`` for any other ``Exception`` (unexpected bug — full traceback).

    The caller decides how to propagate it — ``sys.exit(run_cli(fn))`` or
    ``return run_cli(fn)`` from its own ``main()``.
    """
    try:
        main_fn()
    except PlacerError as e:
        logging.error(_("Error: {e}").format(e=e))
        return 1
    except Exception as e:
        # ApiError is imported lazily: non-IPC commands (flatten) never raise
        # an IPC error, so they never pay for the kipy import chain.
        from kipy.errors import ApiError
        if isinstance(e, ApiError):
            logging.error(api_error_message(e))
            return 1
        logging.exception(_("Unexpected error"))
        return 2
    return 0


def _read_root_yaml(path: Path) -> dict:
    """Raw root-config read for peek_log_file — kept separate so it can be
    passed to cached_file_read as the miss loader. Dispatches on file
    extension exactly like the rest of the core config graph (2026-08-28,
    core_yaml_removal): .sexp -> s-expr, .json -> JSON. A legacy .yaml/.yml
    root (or any other extension) is no longer a config format the core reads
    — returns {} so peek_log_file's never-raise contract turns it into a
    warning + None, never a silent YAML read."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    if suffix == ".sexp":
        from .config.sexp_format import sexp_to_dict

        with open(path, "r", encoding="utf-8") as f:
            return sexp_to_dict(f.read()) or {}
    return {}


def peek_log_file(config_path: str) -> str | None:
    """Cheap, non-raising read of ONLY the config's ``log_file`` key.

    The CLI needs ``log_file`` BEFORE
    :func:`~kicadstamp.logging_setup.setup_logging` runs, but the full validated
    :func:`~kicadstamp.config.load_config` belongs to the apply pipeline (one
    load, errors surfaced properly there). This reads just the root config's
    ``log_file`` scalar (s-expr/.json by extension, like every core config
    reader since core_yaml_removal) — resolved relative to the config file's
    directory exactly like ``load_config`` does — and never raises: a
    missing/unreadable/broken/legacy-.yaml config simply logs a warning and
    returns ``None``.

    ``log_file`` is a root-file top-level key (``include:`` never contributes
    it), so a root-only read is faithful. Returns the resolved log path or
    ``None``.

    Routed through :func:`kicadstamp.utils.file_cache.cached_file_read` so the
    GUI startup doesn't re-parse the root YAML a second time: DockHub's
    _on_root_file_changed_for_logging calls this right after RootMetadataDock's
    set_target_file() already parsed the same bytes through yaml_io.load_data()
    (same bug class as the 2026-08-21 yaml_io fix). The never-raise contract is
    unchanged — a missing/broken file still logs a warning and returns None.
    """
    try:
        data = cached_file_read(Path(config_path), _read_root_yaml)
        raw = data.get("log_file") if isinstance(data, dict) else None
        if not raw:
            return None
        return resolve_config_relative_path(Path(config_path).parent, raw)
    except Exception as e:
        logging.warning(_("Could not read log_file from config {path}: {e}")
                        .format(path=config_path, e=e))
        return None
