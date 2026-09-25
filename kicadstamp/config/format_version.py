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

**Taking the number out and LIFTING the content are the same act**, and both
happen by default (decision A, Т2, 24.09.2026): the reader hands back content of
the CURRENT format, so a caller cannot silently receive format-2 content and
take it for current. Discipline was the alternative and it was rejected — a
structural cell can check the callers that exist today, but every NEW reader
would have to remember, and that is exactly how the trap appeared. Parse and
write are symmetric now: the writer stamps the current number itself (Т3), the
reader lifts itself. Neither can be forgotten.

A caller whose job is the file's OWN bytes (a format converter) says
`upgrade=False` out loud, and then it is that caller's duty to put the number it
read back into the file it rewrites — never the current one, or format-1 content
would be stamped as current.

Converters go UP only, one step per number, and are NEVER reversed: the only way
back is the ``.bak`` the on-disk upgrade leaves next to the file.
"""
from __future__ import annotations

import dataclasses
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


def current_format() -> int:
    """The format THIS build writes — CURRENT_FORMAT, read at CALL time.

    Every other module must ask through here rather than `from .format_version
    import CURRENT_FORMAT`: a from-import binds the VALUE at import time, and a
    module first imported while the constant is temporarily substituted (a test
    swapping the current format to exercise a future step) would freeze the
    substituted number forever. Measured 24.09.2026: `config_writer` was first
    imported inside a cell that had patched CURRENT_FORMAT to 3, and went on
    writing `"version": 3` for the rest of the session while `format_version`
    itself was back at 2 — the two sides of one file disagreeing about its
    format, silently. Nothing substitutes the constant in production, but that
    is exactly the class of failure this заход exists to remove."""
    return CURRENT_FORMAT

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


# ── steps, and what a step is allowed to demand ────────────────────────────

@dataclasses.dataclass(frozen=True)
class UpgradeContext:
    """What a converter step is told about WHERE the lift is happening.

    Two facts, both load-bearing for the steps that come after the identity one:

    - `path` is the file being lifted, and it may be ``<config>`` — which means
      the text came from a bare string, not from a file on disk. The UUID step
      (2 -> 3) builds an identity, and an identity needs a STABLE seed, which is
      the profile path; a step must therefore refuse ``<config>`` instead of
      inventing a seed. Two machines seeding differently would produce two
      different UUIDs for the same record.
    - `at_parse_time` says the lift is running inside a read. The zero-origin
      step (Р38) changes the MEANING of zero and needs the live board, so it
      cannot run inside a parse at all.

    Both cases are refusals, never silent skips: a skipped step hands out
    content of an older format as if it were current, which is the one failure
    the number exists to prevent. A step that cannot run here calls
    :func:`refuse_step`."""
    path: str
    at_parse_time: bool


def refuse_step(from_version: int, requirement: str, path: str = "<config>") -> None:
    """A converter step that cannot run where it was asked to. Loud, always.

    `requirement` names what the step needs ("the profile path", "the live
    board"), so the message tells the reader which of the two situations above
    they hit instead of looking like a corrupt file."""
    raise ValidationError(format_fatal_error(
        _("the converter from format {from_version} to {to_version} cannot run here: it needs {requirement}")
        .format(from_version=from_version, to_version=from_version + 1,
                requirement=requirement),
        [_("in {path}: this step needs {requirement}, which parsing the file alone does not give — the file can only be lifted where that data exists. Refusing loudly is on purpose: skipping the step would hand out content of an older format as if it were current")
         .format(path=path, requirement=requirement)]))


def _step_1_to_2(data: dict[str, Any], ctx: UpgradeContext) -> dict[str, Any]:
    """Format 1 -> 2: an IDENTITY step, and deliberately so.

    Step 1 -> 2 only adds the number itself, and the number is written by the
    serializer (``sexp_format.dict_to_sexp``), never carried in the dict. So
    there is nothing for a converter to DO here — the step exists so the chain
    has no hole and so 2 -> 3 has a neighbour to follow. When a step really
    changes content (UUID in 2 -> 3), it returns a new dict and may consult
    `ctx`; this one ignores it, which is why it is safe to run on every load."""
    return data


# version -> the converter taking that version to the next one.
STEPS: dict[int, Callable[[dict[str, Any], UpgradeContext], dict[str, Any]]] = {
    1: _step_1_to_2,
}


def upgrade_data(data: dict[str, Any], from_version: int,
                 path: str = "<config>", *, at_parse_time: bool = False
                 ) -> dict[str, Any]:
    """Lift parsed config content from `from_version` to CURRENT_FORMAT, in
    memory, by running every step in order exactly once.

    Pure: the on-disk part (backup, write) is a separate step, so this can run
    inside the read path without any side effect.

    Refuses a file NEWER than CURRENT_FORMAT before doing anything (see
    :func:`refuse_newer`) — otherwise a newer file would fall through the
    `while` loop untouched and be read as if it were current, the exact failure
    the number exists to prevent. A hole in the chain is a fatal too: a missing
    converter is an internal error, never a reason to silently skip a step. A
    step that cannot run in this context refuses through :func:`refuse_step`.
    """
    refuse_newer(from_version, path)
    ctx = UpgradeContext(path=path, at_parse_time=at_parse_time)
    version = from_version
    while version < CURRENT_FORMAT:
        step = STEPS.get(version)
        if step is None:
            raise ValidationError(format_fatal_error(
                _("no format converter from {from_version} to {to_version}")
                .format(from_version=version, to_version=version + 1),
                [_("the converter chain has a hole — this is an internal error, not a problem with the file")]))
        data = step(data, ctx)
        version += 1
    return data


def lift_loaded_dict(data: dict[str, Any], path: str = "<config>",
                     upgrade: bool = True) -> dict[str, Any]:
    """The one in-memory normalization a RAW parsed JSON config dict passes:
    take the root number out, refuse a file newer than this build, and — unless
    the caller is a raw converter — lift the content to CURRENT_FORMAT.

    Every JSON reader calls this; the s-expr readers get the same treatment
    inside ``sexp_to_dict``, so the two formats cannot drift apart. The DEFAULT
    lifts, which is the point: a caller that never asks for the number still
    receives content of the current format (see the module docstring on why
    discipline was rejected).

    `upgrade=False` is for a caller whose job is the file's OWN bytes (a format
    converter). It still TAKES the number out — leaving it in would put a
    `version` free-form root key back into the dict, and an included file
    carrying it is a fatal in the include merge — and it still refuses a newer
    file. The number is lost to such a caller unless it asks for it (see
    :func:`take_version`), and it must put that same number back when it writes,
    never the current one."""
    version = take_version(data, path)
    refuse_newer(version, path)
    if not upgrade:
        return data
    return upgrade_data(data, version, path, at_parse_time=True)


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

    The probe asks for the number WITHOUT lifting (`upgrade=False`): it wants to
    know what is on disk, not to transform it, and the on-disk sweep (Т4) is
    what decides whether to write.

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


def parse_raw_text(text: str, suffix: str,
                   path: str = "<config>") -> tuple[dict[str, Any], int]:
    """Parse config TEXT as the file's OWN bytes: no lift, plus the number it
    carried.

    `suffix` picks the grammar the way every reader does — by file extension —
    so the two cannot drift. Section ALIASES are normalized (that is part of
    reading a file at all, `sexp_to_dict(apply_aliases=True)` /
    `normalize_section_aliases`), which is what lets the on-disk sweep write
    canonical section names (`rules` -> `chains`) instead of carrying the old
    ones along (У1).

    Used by :func:`parse_raw_file` and by the on-disk sweep's pre-write check,
    which must parse the exact text it is about to write, through the same
    grammar. A non-config suffix reports `({}, 1)` and invents no error of its
    own: the readers fatal on that themselves, and this probe must not paper
    over it (same rule as `read_version`)."""
    if suffix == ".sexp":
        from .sexp_format import sexp_to_dict

        found: list[int] = []
        data = sexp_to_dict(text, path=path, version_out=found,
                            upgrade=False) or {}
        return data, (found[0] if found else 1)
    if suffix == ".json":
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}, 1
        data = dict(data)
        return data, take_version(data, path)
    return {}, 1


def parse_raw_file(path: str | Path) -> tuple[dict[str, Any], int]:
    """`:func:`parse_raw_text` for a file on disk — one read, both facts.

    The on-disk sweep needs the number (to decide whether to write) and the raw
    dict (to lift and rebuild) from the SAME parse: asking twice would parse
    every file twice for nothing."""
    p = Path(path)
    with open(p, "r", encoding="utf-8") as f:
        return parse_raw_text(f.read(), p.suffix.lower(), str(p))


def _read_version_uncached(path: Path) -> int:
    """Parse one file and report its number. sexp_format is imported (inside
    parse_raw_text) rather than at module level: sexp_format imports this module
    for VERSION_KEY and the validation, so a top-level import would be circular —
    the same function-level import includes.py uses for sexp_to_dict."""
    version = parse_raw_file(path)[1]
    refuse_newer(version, str(path))
    return version
