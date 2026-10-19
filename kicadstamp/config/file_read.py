# kicadstamp/config/file_read.py
"""The ONE reader every config writer takes its dict from (доделка Р of
plan_2026_10_15_reread_copper_uuid_and_selection_any).

Moved verbatim out of ``kicadstamp/config_writer.py`` (whose size rule is
"giants only shrink"): the read itself, its format dispatch and the private-copy
contract are a coherent unit, and ``config_writer`` keeps the public name as a
re-export (``read_data``), so every importer and every test sees the same
function as before.

The docstring of :func:`read_config_data` is the authority on WHY the copy is
here and what a broken file must raise. This module has no Qt dependency.
"""
import copy
import json
from pathlib import Path

from kicadstamp.config.aliases import normalize_section_aliases
from kicadstamp.config.format_version import lift_loaded_dict
from kicadstamp.config.sexp_format import sexp_to_dict
from kicadstamp.exceptions import (
    ValidationError,
    unknown_extension_config_error,
    yaml_removed_config_error,
)
from kicadstamp.i18n import _
from kicadstamp.utils.file_cache import cached_file_read
from kicadstamp.config_working_set import WORKING_SET


def raise_unsupported_config_format(path: Path, suffix: str) -> None:
    """Raise the fatal for a config file whose format the core no longer
    supports, wrapped in OSError with the ValidationError kept as __cause__
    (2026-08-28 — fix for a live bug: a bare ValidationError escaped the GUI
    docks' `except OSError` around read_data/write_data and would crash a Qt
    slot; _read_data's docstring documents the same class of incident as
    already hit live 2026-08-04). .yaml/.yml get the dedicated "YAML removed
    — convert with sexp_config_convert.py" message; anything else gets the
    generic unrecognized-extension message."""
    if suffix in (".yaml", ".yml"):
        cause = yaml_removed_config_error(path)
    else:
        cause = unknown_extension_config_error(path, suffix)
    raise OSError(str(cause)) from cause


def read_config_data(path: Path) -> dict:
    """Read an existing config file's YAML/JSON content (or {} when it
    doesn't exist yet). Raises OSError on read/parse errors instead of
    returning {} — the merge-write helpers are on the docks' write path,
    where a broken file must surface to the user, not be silently treated
    as empty (unlike gui/config_io.load_data). FIXED (2026-08-04): a malformed
    file used to raise the raw yaml.YAMLError/json.JSONDecodeError instead
    — neither is an OSError, so every caller's `except OSError` (e.g.
    PlacerDock._do_save's, written against exactly this docstring's
    promise) missed it, and the raw exception propagated uncaught out of a
    Qt slot, which PyQt6 aborts the whole process on by default. Found
    live: Placer's Save crashed the entire GUI over one stray character in
    an unrelated part of the target YAML file.

    The read+parse itself goes through cached_file_read (2026-08-15, see
    kicadstamp/utils/file_cache.py) so the docks' repeated read-merge-write
    cycles on the same file — and the collectors that read every graph file
    once per dock — parse it from disk ONCE, not once per call. Contract is
    UNCHANGED: {} for a missing file, OSError — never ValidationError — on
    a malformed file. Missing-file is handled here, before the cache, so a
    file that doesn't exist yet is never cached as "absent forever" and
    appears on the next call once it's created.

    Format is selected by file extension (2026-08-28, core_yaml_removal —
    YAML support was removed from the config graph entirely): .json -> JSON,
    .sexp -> s-expr, anything else (including legacy .yaml/.yml) -> fatal
    OSError (ValidationError as __cause__) — .yaml/.yml with the dedicated
    "convert with sexp_config_convert.py" message, any other extension with
    the unrecognized-extension message.

    THE RETURNED DICT IS THE CALLER'S OWN (2026-10-15,
    plan_2026_10_15_reread_copper_uuid_and_selection_any): a deepcopy is
    returned, so a caller may mutate it freely. cached_file_read hands out the
    SHARED cached object on a cache hit (its own documented contract), and the
    writers used to edit THAT object in place. The format-3 writer stamp reads
    the same cache as "the previous version of this file"
    (config/format3.py::_load_previous_file), so it saw the caller's edit and
    minted a NEW uuid for an in-place `net_traces` record — every tree node
    referencing the old uuid then dangled and the whole write was refused
    ("format 3: cannot write — N reference(s) point at a uuid that is missing
    from the graph", measured: kicadstamp/diagnostics/
    deepseek_probe_reread_uuid_cache_2026_10_15.py, warm cache needed). The copy
    lives HERE, at the ONE reader the writers use, so no caller has to remember
    it (rename.py / entity_delete.py / config_writer's own upserts used to
    deepcopy by hand; those copies are gone — the rule is stated once)."""
    # Staged mode (2026-09-01, plan project_save_model): a dirty file's content
    # lives in the working set, not on disk — this also covers to-be-created
    # (__new__) files that don't exist yet.
    if WORKING_SET.enabled:
        staged = WORKING_SET.staged_content(str(path.resolve()))
        if staged is not None:
            return copy.deepcopy(staged)
    if not path.exists():
        return {}

    def _uncached_read(p: Path) -> dict:
        suffix = p.suffix.lower()
        # EACH BRANCH LIFTS EXACTLY ONCE, and it lifts at the parse site.
        #
        # Д1 (Т2 acceptance): this used to run ONE shared parser and then wrap
        # the result in lift_loaded_dict for both formats. For .sexp the parser
        # had ALREADY taken the number out and lifted the content, so the
        # wrapper read the numberless dict as "format 1" and ran the whole chain
        # a SECOND time — a file already at the current format went through
        # 1 -> 2 and 2 -> 3 all over again (measured: diagnostics/
        # probe_format_double_lift.py). Invisible at 1 -> 2 (identity); the
        # zero-origin step would add the body angle twice and move the board —
        # and this is the GUI docks' read-merge-write path, so the doubly-lifted
        # content reaches the disk.
        if suffix == ".json":
            kind = "JSON"

            def parser(f):
                # A raw JSON dict has no parse layer of its own, so the lift
                # belongs here. normalize_section_aliases runs FIRST, like
                # includes._load_config_file does, so a step sees canonical keys
                # AND a retired section
                # (`chains:`) is refused before this read-merge-write path could
                # rewrite the file without it (2026-10-11, decision Д1).
                return lift_loaded_dict(
                    normalize_section_aliases(json.load(f) or {}, str(p)), str(p))
        elif suffix == ".sexp":
            kind = "s-expr"

            def parser(f):
                # sexp_to_dict applies the aliases (apply_aliases default True)
                # AND lifts; path= names the FILE in a "format too new"
                # refusal, and it is the step context (a step needing the
                # profile path refuses a bare "<config>").
                return sexp_to_dict(f.read(), path=str(p))
        else:
            raise_unsupported_config_format(p, suffix)
        try:
            with open(p, "r", encoding="utf-8") as f:
                return parser(f)
        except (json.JSONDecodeError, ValidationError) as e:
            raise OSError(_("{path} is not valid {kind}: {error}").format(
                path=path, kind=kind, error=e)) from e

    # The writer-side contract above: never hand out the cache's own object.
    # cached_file_read does exactly that on a HIT, and this dict routinely IS
    # that object — hence the copy here and not at the call sites.
    return copy.deepcopy(cached_file_read(path, _uncached_read))
