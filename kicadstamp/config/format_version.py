# kicadstamp/config/format_version.py
"""config/format_version.py — the on-disk FORMAT number of a config file.

Every config file carries a plain whole number: the format its content is
written in. It is NOT the KiCadStamp release — most releases do not change the
format — it is the number of the grammar, and it grows by one per converter.

Where the number lives:

- s-expr: a child of the root, ``(version 2)`` — KiCad's own habit (its files
  carry ``(version 20240108)``), see
  design_2026_09_24_geometry_tree_and_user_tree.md §3.8в;
- JSON: the root key ``"version"``.

**No number at all means format 1** — every file written before the number
existed. That is the whole of the 1 -> 2 step: it only stamps the number, the
content does not change. ``CURRENT_FORMAT`` is what a freshly written file gets.

The number is TAKEN OUT of the dict by the layer that parses the file
(``sexp_format.sexp_to_dict`` for s-expr, :func:`take_version` below for JSON),
so nothing downstream can mistake it for a free-form root field and the include
merge never sees it. A `(version N)` inside an INCLUDED file used to be fatal
there ("unsupported top-level key 'version'", measured 24.09.2026 — see the
plan's §10.2); taking it out at the parse site fixes that for every reader at
once.

Converters go UP only, one step per number, and are NEVER reversed: the only way
back is the ``.bak`` the on-disk upgrade leaves next to the file.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _

# The format a freshly written file gets. Raised by one per converter; a release
# that does not change the grammar must NOT bump it.
CURRENT_FORMAT = 2

# The root key / node name carrying the number, in both formats.
VERSION_KEY = "version"

# Sentinel for "the key was not there at all" — `None` is a value a JSON file may
# legitimately hold ("version": null is malformed, not absent).
_MISSING = object()


def check_version_value(raw: Any, path: str, display: str | None = None) -> int:
    """One parsed number -> the format number, or a fatal.

    A positive whole number is the only accepted shape: `0`, a negative, a
    float, a string and a bare `true`/`false` are all fatals with the value
    shown. `display` lets the s-expr caller show a bare token readably (the
    parse layer's atom is not always a Python type a human recognises)."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        shown = repr(raw) if display is None else display
        raise ValidationError(format_fatal_error(
            _("the config format number must be a positive whole number, got {value}")
            .format(value=shown),
            [_("in {path}: the number is a plain whole number of 1 or more (like (version 2))")
             .format(path=path)]
        ))
    return raw


def take_version(data: dict[str, Any], path: str = "<config>") -> int:
    """ZAP the root format number out of a raw parsed config dict and return it.

    Absent key -> 1 (every file written before the number existed). The key is
    REMOVED, so nothing downstream — the include merge above all — can mistake
    it for a free-form root field.

    Shape is validated; being NEWER than CURRENT_FORMAT is NOT refused here, so
    this stays a pure extraction. A reader passes the result to
    :func:`upgrade_data`, which refuses; the on-disk sweep calls
    :func:`refuse_newer` explicitly."""
    raw = data.pop(VERSION_KEY, _MISSING)
    if raw is _MISSING:
        return 1
    return check_version_value(raw, path)


def refuse_newer(version: int, path: str = "<config>") -> None:
    """Fatal when the file was written by a NEWER grammar than this build knows.

    Reading it anyway is the one thing we must not do: three machines share the
    profiles over Syncthing, so a newer file arrives before the `git pull` does,
    and an older build would silently drop every field it does not know about.
    The message says what to do instead of offering to try."""
    if version <= CURRENT_FORMAT:
        return
    raise ValidationError(format_fatal_error(
        _("config {path} is written in format {found}; this version of KiCadStamp understands the format up to {supported}")
        .format(path=path, found=version, supported=CURRENT_FORMAT),
        [_("update KiCadStamp: the file is not read on purpose, so that nothing this version does not know about is lost. Once a newer KiCadStamp has lifted the profile, do not open it with an older one")]))


def _step_1_to_2(data: dict[str, Any]) -> dict[str, Any]:
    """Format 1 -> 2: an IDENTITY step, and deliberately so.

    Step 1 -> 2 only adds the number itself, and the number is written by the
    serializer (``sexp_format.dict_to_sexp``), never carried in the dict. So
    there is nothing for a converter to DO here — the step exists so the chain
    has no hole and so 2 -> 3 has a neighbour to follow. When a step really
    changes content (UUID in 2 -> 3), it returns a new dict; this one returns
    the same object, which is why `upgrade_data` is safe to run on every load."""
    return data


# version -> the converter taking that version to the next one.
STEPS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    1: _step_1_to_2,
}


def upgrade_data(data: dict[str, Any], from_version: int,
                 path: str = "<config>") -> dict[str, Any]:
    """Lift parsed config content from `from_version` to CURRENT_FORMAT, in
    memory, by running every step in order exactly once.

    Pure: the on-disk part (backup, write) is a separate step, so this can run
    inside the read path without any side effect.

    Refuses a file NEWER than CURRENT_FORMAT before doing anything (see
    :func:`refuse_newer`) — otherwise a newer file would fall through the
    `while` loop untouched and be read as if it were current, the exact failure
    the number exists to prevent. A hole in the chain is a fatal too: a missing
    converter is an internal error, never a reason to silently skip a step."""
    refuse_newer(from_version, path)
    version = from_version
    while version < CURRENT_FORMAT:
        step = STEPS.get(version)
        if step is None:
            raise ValidationError(format_fatal_error(
                _("no format converter from {from_version} to {to_version}")
                .format(from_version=version, to_version=version + 1),
                [_("the converter chain has a hole — this is an internal error, not a problem with the file")]))
        data = step(data)
        version += 1
    return data


# ── the on-disk probe: what number does THIS file carry right now? ─────────
# Keyed by (resolved path, mtime_ns), the same identity the single-file read
# cache uses, so a hand edit is a miss on its own with no explicit invalidation.
# Measured 24.09.2026 (plan §10.4, best of 5): a WARM probe is 0.137 ms for two
# files / 0.649 ms for one 213 KB file — 0.45-0.58 % of one load_config — while
# parsing every graph file in full is 11.657 / 74.632 ms, ~50 % of one
# load_config, and the GUI startup runs the load body up to 6 times. Hence the
# cache: the COLD pass pays one parse per file (the same parse the reader is
# about to do anyway), every later pass pays one os.stat per file.
_probe_lock = threading.Lock()
_probe_cache: dict[tuple[str, int], int] = {}


def read_version(path: str | Path) -> int:
    """The FORMAT number the file on disk carries right now (1 when it carries
    none).

    Authoritative, not a head-of-file guess: the file is PARSED through the same
    `sexp_to_dict` every reader uses, so a hand edit that moves the node is read
    correctly, a malformed number is a fatal here too, and a file NEWER than
    CURRENT_FORMAT is refused here too. That refusal is what lets the on-disk
    upgrade sweep prove "nothing in the graph is newer" BEFORE it writes
    anything.

    A missing file reports 1 and invents no error message of its own: the
    callers already disagree on purpose about a missing config (includes.py
    fatals, config_writer returns {}), and this probe must not paper over it."""
    p = Path(path)
    try:
        mtime_ns = os.stat(p).st_mtime_ns
    except OSError:
        return 1
    key = (str(p.resolve()), mtime_ns)
    with _probe_lock:
        hit = _probe_cache.get(key)
    if hit is not None:
        return hit
    version = _read_version_uncached(p)
    with _probe_lock:
        _probe_cache[key] = version
    return version


def _read_version_uncached(path: Path) -> int:
    """Parse one file and report its number. sexp_format is imported HERE, not
    at module level: sexp_format imports this module (for VERSION_KEY and the
    validation), so a top-level import would be circular — the same
    function-level import includes.py uses for sexp_to_dict."""
    suffix = path.suffix.lower()
    if suffix == ".sexp":
        from .sexp_format import sexp_to_dict

        found: list[int] = []
        with open(path, "r", encoding="utf-8") as f:
            sexp_to_dict(f.read(), path=str(path), version_out=found)
        version = found[0] if found else 1
    elif suffix == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        if not isinstance(data, dict):
            return 1
        version = take_version(dict(data), str(path))
    else:
        # Not a config format (the readers fatal on this on their own).
        return 1
    refuse_newer(version, str(path))
    return version
