# gui/docks/sheet_narrowing_warning.py
"""The V.3 Log line — "sheet names cannot be resolved" — once per root, only with a live board.

Moved out of `gui/dock_hub.py` on 22.10.2026 (plan_2026_10_22_checks_tab, Ч1-Т3): the hub only
shrinks, and the Checks tab's wiring must not make it grow. The wording does NOT live here and
never did: the cause comes from `kicadstamp.sheet_names.sheet_narrowing_disabled_line`, the ONE
host this line shares with the config check, so the two doors can never tell the user two
different stories.

Reading the board is not done here either: "is there a board?" is `connection.is_connected` —
the cheap presence check the board door allows on the UI thread. The function is pure apart from
that and a `load_config` call, so a cell can drive it without a board.
"""
import logging


def warn_once(path, connection, checked: set) -> None:
    """Print the informational line for `path`, at most once per root (`checked` remembers).

    An EMPTY sheet-name map means no name can be resolved, so sheet-based narrowing silently
    does nothing. The map is empty for TWO reasons — nothing configured, or a single-page
    schematic (the live Test04) — and `sheet_narrowing_disabled_line` names the right one. The
    root sheet "/" needs no map at all, so a config that never needs sheet names stays silent.

    `checked` is the CALLER's set (DockHub's `_sheet_dir_checked`), so "once" survives as long
    as the dock does; the key is added BEFORE the load, exactly as before the move: a broken
    config must not be re-read on every selection tick.
    """
    if path is None:
        return
    key = str(path)
    if key in checked:
        return
    if not getattr(connection, "is_connected", False):
        return
    checked.add(key)
    try:
        from kicadstamp.config import load_config
        cfg, ctx = load_config(key)
        # len() materialises the lazy RuntimeContext map (its own __bool__ is deliberately
        # always True — see sheet_names.LazySheetNameMap).
        has_sheets = len(ctx.sheet_names) > 0
    except Exception:  # noqa: BLE001 — a broken config is reported by the docks
        return
    if has_sheets:
        return
    from kicadstamp.sheet_names import sheet_narrowing_disabled_line
    logging.info(sheet_narrowing_disabled_line(
        getattr(cfg, "schematic_dir", None),
        getattr(cfg, "schematic_files", None)))
