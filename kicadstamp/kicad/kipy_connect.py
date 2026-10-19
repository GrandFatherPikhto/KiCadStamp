# kicadstamp/kicad/kipy_connect.py
"""Connection-version reporting seam over kipy (Ч3 of
plan_2026_10_15_reread_copper_uuid_and_selection_any).

WHY IT IS A MODULE OF ITS OWN. The GUI's poll timer creates a fresh adapter every
reconnect (~5 s while KiCad is up but no PCB editor is open), and the adapter's
`__init__` used to log "Connected to KiCad …" at INFO plus kipy's
"version is newer than the API version" warning at WARNING on EVERY attempt —
636 connect lines in one day of the profile log, with the real reason (no handler
for `GetOpenDocuments`) buried at DEBUG. The pair below makes both lines ONE PER
VERSION PAIR per process; repeats stay in the file log at DEBUG, so nothing is
silenced (plan Ч3 п.3).

A version CHANGE (a different KiCad or kipy build) reports again: the state is
the last reported PAIR, not a set — switching back and forth keeps reporting the
change, which is what a reader needs.

`_reset_version_reporting()` exists for the cells: the state is process-wide by
design, and a test that depended on what an earlier test (or another `-n auto`
worker) already reported would be green or red by accident.

Not in `adapter.py` on purpose: the adapter is a size-capped giant (доделка Р),
and this is not adapter business.
"""
import logging

from kipy.errors import FutureVersionError

from ..i18n import _

logger = logging.getLogger(__name__)

# (KiCad version as reported by the live instance, kipy's own API version) of
# the last pair whose lines were written at INFO/WARNING. None before the first
# successful version read.
_last_reported_version_pair: tuple[str, str] | None = None


def _reset_version_reporting() -> None:
    """Forget the last reported pair (tests only — see the module docstring)."""
    global _last_reported_version_pair
    _last_reported_version_pair = None


def report_connected_version(kicad) -> None:
    """Log the connected version and kipy's newer-than-API warning once per pair.

    Behaviour preserved from the adapter's own block: the live version is read
    first (a failure there is DEBUG, with the traceback, and non-fatal),
    `check_version()` still runs on every connect — the version comparison is
    kipy's rule, never copied here — and its `FutureVersionError` text is what
    the WARNING carries, verbatim. What changes is only the LEVEL of repeats.
    """
    global _last_reported_version_pair
    try:
        live_version = kicad.get_version()
        pair = (str(live_version), str(kicad.get_api_version()))
        first = pair != _last_reported_version_pair
        _last_reported_version_pair = pair
        if first:
            logger.info(_("Connected to KiCad {version}").format(version=live_version))
        else:
            logger.debug(_("Connected to KiCad {version}").format(version=live_version))
        try:
            kicad.check_version()
        except FutureVersionError as exc:
            if first:
                logger.warning(str(exc))
            else:
                logger.debug(str(exc))
    except Exception:
        logger.debug(_("Could not query KiCad version after connect"), exc_info=True)
