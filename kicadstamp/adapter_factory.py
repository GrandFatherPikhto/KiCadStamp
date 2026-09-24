# kicadstamp/adapter_factory.py
"""The ONE place a board adapter is born (plan Т2), with the field-override layer.

Design: `design_2026_09_18_field_overrides_store.md` §1/§2.3; plan
`plan_2026_09_18_field_overrides_store.md`, tasks Т2/Т2а/Т3.

Why a factory at all: the door to the FIELDS was already single
(`IBoardAdapter.get_field_value` — every Role/Cluster read in the project goes
through it), which is what makes the store's priority a one-point change. The
door to the ADAPTER, though, was not: the shipping code created
`KiCadBoardAdapter` in thirteen places, plus twenty-seven probes. This module is
the next honest step of that same door — now every adapter in the shipping code,
in `kicadstamp/diagnostics/` and in `tools/` is created here, and the guard С15
(structural ast scan) keeps it that way.

The MODE is an argument, never a missing edit:

    create_board_adapter(timeout_ms)                        # the profile decides
    create_board_adapter(timeout_ms, config_path=path)      # the profile decides
    create_board_adapter(timeout_ms, use_store=False)       # NAMED bare mode
    create_board_adapter(timeout_ms, use_store=True)        # always layered

`use_store=False` is what a probe about the BOARD writes in its own code when it
must show what physically lies on the board rather than the effective value
(Т2а). Spelling that out is the whole point: "not translated" would have been an
absence of an edit — invisible, and indistinguishable from an oversight six
months later.

The store is a property of the PROFILE, the adapter is not: whoever knows the
config path passes it. With no config path there is no store to consult, and the
adapter comes back bare — which is the only truthful thing to do (that is the
case for `undo`, and for the CLI commands whose addressing is refdes/selection
based and which have no profile at all).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .constants import (DEFAULT_TIMEOUT_MS, ROLE_CLUSTER_SOURCE_REGISTRY,
                        ROLE_CLUSTER_SOURCES)
from .field_override_adapter import FieldOverrideAdapter
from .field_overrides import load_field_overrides
from .i18n import _
from .utils.file_cache import cached_file_read
from .utils.paths import overrides_path_for_config

logger = logging.getLogger(__name__)


def create_board_adapter(timeout_ms: int = DEFAULT_TIMEOUT_MS, *,
                         config_path=None, store=None, use_store=None,
                         source=None):
    """Create a live-board adapter, layered with the override store when it
    applies.

    :param timeout_ms: the IPC timeout, as before.
    :param config_path: the profile whose store and switch to use. Optional —
        without it (undo, the config-less CLI commands, probes about the board)
        the adapter is bare.
    :param store: an EXPLICIT store (tests, the CLI's own tooling). Bypasses the
        path-derived one, so a caller can hand over a store it already holds.
    :param use_store: ``False`` — the NAMED bare mode: no layer, ever (a probe
        asking for the physical board). ``True`` — always layer, even when the
        store is empty (the MCP server, which installs the store per call with
        ``bind_store``). ``None`` (the default) — decide from the profile: layer
        only when the switch says "registry" AND the store actually has records.
    :param source: an explicit switch value, overriding the profile's.

    With an EMPTY store and no explicit ``use_store=True`` the layer is NOT
    installed: there is nothing to override, so the adapter behaves exactly like
    the one from before this change — structurally, not just by luck. That is
    what makes the golden guard (an empty store / the "board" switch reproduce
    the pre-store behaviour to the last byte) cheap and true by construction.
    """
    # Lazy: importing the factory must not drag kipy into a process that only
    # wants a path helper (the pre-existing pattern for every adapter import).
    from .kicad.adapter import KiCadBoardAdapter

    adapter = KiCadBoardAdapter(timeout_ms=timeout_ms)

    if use_store is False:
        return adapter
    if use_store is True:
        return FieldOverrideAdapter(
            adapter, store, source=source or ROLE_CLUSTER_SOURCE_REGISTRY)

    resolved_store, resolved_source = _resolve_store(config_path, store, source)
    if (resolved_source != ROLE_CLUSTER_SOURCE_REGISTRY
            or resolved_store is None or not resolved_store.has_any()):
        if resolved_source != ROLE_CLUSTER_SOURCE_REGISTRY and store is None:
            logger.debug(
                "role_cluster_source is %r for %r — the override store is not "
                "applied to this adapter", resolved_source, str(config_path))
        return adapter
    return FieldOverrideAdapter(adapter, resolved_store, source=resolved_source)


def store_for_config(config_path):
    """(store | None, source) for a profile — the SAME resolution
    ``create_board_adapter`` does for its layered mode, for a caller that must
    REBIND a layer that already exists: the GUI's poll adapter when the project
    changes (plan Т5г), exactly as the MCP server binds a store per call (Т3/С19).

    ``(None, source)`` means the profile itself answers "board" — the caller must
    then leave the layer inert, which is the pre-store behaviour by construction."""
    return _resolve_store(config_path, None, None)


def _resolve_store(config_path, store, source):
    """(store | None, source) — the explicit arguments win over the profile."""
    if store is not None:
        return store, (source or role_cluster_source_for_config(config_path))
    if not config_path:
        return None, source or ROLE_CLUSTER_SOURCE_REGISTRY
    resolved_source = source or role_cluster_source_for_config(config_path)
    if resolved_source != ROLE_CLUSTER_SOURCE_REGISTRY:
        return None, resolved_source
    return (load_field_overrides(overrides_path_for_config(str(config_path))),
            resolved_source)


def role_cluster_source_for_config(config_path) -> str:
    """The profile's Role/Cluster source, read from the RAW root file.

    Cheap ON PURPOSE: one cached parse of the root file, no `load_config`. Every
    adapter creation goes through here, and cascade redraws create an adapter per
    click while an ApplyPipeline per name loads the config anyway — dragging the
    whole config pipeline (validators, dataclasses, sheet map) into adapter
    creation for ONE scalar would be paid on every one of them.

    The loader stays the authority: it FATALS on an unknown value. Here an
    unknown or unreadable value falls back to the documented default with a
    warning — refusing to hand out an adapter over a typo would break every
    command at once, and the pipeline that resolves anything loads the same file
    through the loader anyway."""
    if not config_path:
        return ROLE_CLUSTER_SOURCE_REGISTRY
    try:
        raw = _read_root_dict(Path(config_path))
    except Exception as e:  # noqa: BLE001 — a switch read must not kill a run
        logger.warning(_("Could not read role_cluster_source from {path}: "
                         "{type}: {e} — using the default ({default})").format(
            path=str(config_path), type=type(e).__name__, e=e,
            default=ROLE_CLUSTER_SOURCE_REGISTRY))
        return ROLE_CLUSTER_SOURCE_REGISTRY
    value = raw.get("role_cluster_source")
    if value is None:
        return ROLE_CLUSTER_SOURCE_REGISTRY
    if value not in ROLE_CLUSTER_SOURCES:
        logger.warning(
            _("Unknown role_cluster_source {value!r} in {path} — using the "
              "default ({default}); config/loader.py fatals on it for every "
              "command that reads the profile").format(
                value=value, path=str(config_path),
                default=ROLE_CLUSTER_SOURCE_REGISTRY))
        return ROLE_CLUSTER_SOURCE_REGISTRY
    return value


def _read_root_dict(path: Path) -> dict:
    """The root config as a plain dict — .sexp/.json only (the config formats
    since 2026-08-28), through the shared mtime file cache so repeated adapter
    creations do not re-parse a multi-thousand-line profile."""
    def _uncached(p: Path) -> dict:
        if p.suffix.lower() == ".json":
            # Local import, same reason as the s-expr one below.
            from .config.format_version import lift_loaded_dict

            with open(p, "r", encoding="utf-8") as handle:
                return lift_loaded_dict(json.load(handle) or {}, str(p))
        # Local import: `.config.sexp_format` pulls the config dataclasses, and
        # a bare adapter (no config path) must not pay for them.
        from .config.sexp_format import sexp_to_dict
        with open(p, "r", encoding="utf-8") as handle:
            return sexp_to_dict(handle.read(), path=str(p)) or {}

    return cached_file_read(path, _uncached)
