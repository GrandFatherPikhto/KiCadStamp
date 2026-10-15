# kicadstamp/config/form_identity.py
"""The ONE rule that gives a FORM-BUILT record the identity of the record it
replaces (plan_2026_10_05_uuid_tails, part 0).

A dock's Redraw («Перерисовать») builds a record from its WIDGETS and splices it
into the loaded Config in place of the saved record with the same identity. The
widgets carry no `uuid` and no reference `*_uuid` (they are human hints: a name, a
point name, a cell name), so the spliced record loses the identity the registry
keys and the reference resolution depend on — under format 3 identity is the
`uuid`, and `registry.record_key_part` refuses to fall back to a name (Р-У5.7).

This is the same resolution the writer's stamp (`format3.stamp_format3`) performs
on the dict about to be written; here it is available BEFORE a write, against the
already-LOADED `Config`. The rule:

  * the record's OWN `uuid` is the uuid the graph already holds for a record of
    this section under the same identity — so an in-place edit, including a rename
    from the form, keeps the record's identity;
  * every REFERENCE (`cell` / `imprint` / `anchor_point`) gets its `<field>_uuid`
    from the TARGET record's uuid, matched by the
    name hint the form carries. The reference forms come from the ONE table
    `format3._F3_REF_TARGET` (`format3._f3_refs` walks them), never a copy;
  * a reference whose name hint matches no record in the graph is a REFUSAL
    (`ValidationError`, the same wording the writer uses) — never "carry on
    without a uuid".

A brand-new record (nothing in the graph, nothing remembered) is given a `uuid4`
that the caller MUST reuse on Save (pass it in as `draft_uuid`): otherwise the
preview places copper under one key while Save writes another and the next apply
prunes the preview's copper as foreign.

`node_ref_uuid` is the SAME rule for a TREE NODE: a node's `ref` is a name hint
beside an authoritative `ref_uuid` (the `_F3_NODE_KIND_TARGET` kinds), and a Ref
edit in the node form must move that uuid with the name — otherwise the writer
stamp (and the loader's `_normalize_format3_refs`) puts the OLD name back and the
edit is silently rolled back. A kind that references NO record carries no uuid at
all, and the caller must CLEAR a stale one: the loader fatals on a `ref_uuid`
beside a local kind (`trees._LOCAL_REF_KINDS`).

`keep_identity` is the DICT-level twin of the rule, for a writer that already has
the replaced record's dict in hand instead of the loaded Config: it carries the
keys that live only in the FILE (see `STORAGE_ONLY_KEYS`) from the old dict onto
the one built from the model. `identify` answers "which uuid is this", it
answers "this dict replaces that one — keep what the file knows".

Qt-free; it never touches the board.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Optional
from uuid import uuid4

from ..exceptions import ValidationError
from ..i18n import _

__all__ = ["FormReferenceMissing", "STORAGE_ONLY_KEYS", "section_uuids",
           "identify", "keep_identity", "node_ref_uuid", "replace_entry"]


class FormReferenceMissing(ValidationError):
    """A reference name in the form matches no record in the loaded graph."""


def _records(cfg, section: str):
    """The records of ONE section of a loaded Config — a dict's VALUES for the
    dict sections (`cells` / `points`), the list itself otherwise."""
    container = getattr(cfg, section, None)
    if isinstance(container, dict):
        return list(container.values())
    return list(container or ())


def section_uuids(cfg, section: str) -> dict:
    """``{full name -> uuid}`` for ONE section of a loaded Config.

    Reference TARGETS are matched by full name (`cells` / `points` / `imprints`
    all carry one) — the same key the writer's stamp resolves a new reference by.
    A record with NO uuid (a format-2 graph, a test fake) maps to None: the name
    RESOLVES, there is simply no uuid to carry across."""
    out: dict = {}
    container = getattr(cfg, section, None)
    if isinstance(container, dict):
        for key, rec in container.items():
            out[str(key)] = getattr(rec, "uuid", None)
        return out
    for rec in container or ():
        name = getattr(rec, "name", None)
        if name is not None:
            out[str(name)] = getattr(rec, "uuid", None)
    return out


def node_ref_uuid(cfg, kind, ref):
    """The `ref_uuid` a TREE NODE of `kind` naming the record `ref` must carry,
    or None when that kind references no record at all.

    The section comes from the ONE table `format3._F3_NODE_KIND_TARGET` (the same
    table the loader's normalization AND the writer stamp walk, so a new form
    cannot enter one and be missed by the other) and the name -> uuid map from
    `section_uuids` (the same key the writer stamp resolves a new reference by).

      * a kind WITH a record section (`placement` / `clone` / `coordinate` /
        `net_trace` / `point`): the uuid of the record `ref` names. A `ref`
        naming NO record of that section raises
        `FormReferenceMissing` — the refusal the writer would reach only at write
        time, here at Apply;
      * any other kind (`module` / `mount` / `copper` / `component`, `external`,
        an unset "auto" kind): None. The caller MUST apply that (clear a stale
        uuid): the loader fatals on a `ref_uuid` beside a local kind
        (`trees._LOCAL_REF_KINDS`).

    A target record with no uuid (a format-2 graph, a test fake) resolves to
    None: the name is real, there is simply no uuid to carry across."""
    from .format3 import _F3_NODE_KIND_TARGET  # lazy: same reason as _resolve_references

    section = _F3_NODE_KIND_TARGET.get(kind)
    if section is None:
        return None
    names = section_uuids(cfg, section)
    if str(ref) not in names:
        raise FormReferenceMissing(_(
            "format 3: tree node {name!r} names no existing {target} record")
            .format(name=ref, target=section))
    return names[str(ref)] or None


def _own_uuid(cfg, section: str, identity, identity_of, remembered_uuid,
              draft_uuid) -> str:
    """The uuid of the record this form entry replaces: the graph record with the
    same identity, else the REMEMBERED uuid of the record loaded into the form (a
    rename from the form), else a stable draft for a brand-new record."""
    if identity is not None:
        for rec in _records(cfg, section):
            if identity_of(rec) == identity:
                uuid = getattr(rec, "uuid", None)
                if uuid:
                    return uuid
    if remembered_uuid:
        return remembered_uuid
    return draft_uuid or str(uuid4())


def _resolve_references(entry: dict, section: str, cfg) -> None:
    """Fill every `<field>_uuid` on `entry` from its name hint, through the
    writer's own reference table/walk (`format3._f3_refs`).

    A hint that resolves sets the uuid; a hint with no target is refused. A
    reference with no name hint (a bare uuid already in the entry) is left alone.
    """
    from .format3 import _f3_refs  # lazy: same reason format3 imports lazily

    # A reference's set of FORMS depends on its section (nested placements, …)
    # and lives in format3's table; a one-record pseudo-file lets
    # that walk run unchanged. Dict sections are keyed by name, list sections are
    # lists of entries — exactly what _f3_refs expects.
    name = entry.get("name")
    if section in ("cells", "points") and name is not None:
        pseudo = {section: {str(name): entry}}
    else:
        pseudo = {section: [entry]}

    for ref in _f3_refs(pseudo):
        hint = ref.holder.get(ref.name_field)
        if hint is None:
            continue
        target = section_uuids(cfg, ref.target)
        if str(hint) not in target:
            raise FormReferenceMissing(_(
                "format 3: {label} names no existing {target} record ({name!r})")
                .format(label=ref.label, target=ref.target, name=hint))
        uuid = target[str(hint)]
        if uuid:  # a target without a uuid carries nothing across (format 2)
            ref.holder[ref.uuid_field] = uuid


def identify(entry: dict, section: str, *, cfg,
             identity=None, identity_of: Optional[Callable[[Any], Any]] = None,
             remembered_uuid: Optional[str] = None,
             draft_uuid: Optional[str] = None) -> dict:
    """Return a COPY of the form-built `entry` carrying the identity of the record
    it replaces — see the module docstring.

    entry — one record dict built from the form's widgets (no uuid yet);
    section — its config section (`thermal_via_arrays`, `net_traces`, …);
    cfg — the LOADED graph the entry is spliced into (working-set aware);
    identity — the value that identifies the record (default `entry["name"]`);
    identity_of — model -> that same value, for `clone_placements` /
        `coordinate_placements`, whose identity is an EFFECTIVE name
        (default: model's `name`);
    remembered_uuid — the uuid of the record the form was loaded from (so a rename
        in the form still edits THAT record);
    draft_uuid — the stable uuid of a brand-new record (mint once and remember).

    Raises FormReferenceMissing when a reference name has no target record.
    """
    entry = copy.deepcopy(entry)
    if identity_of is None:
        identity_of = lambda rec: getattr(rec, "name", None)  # noqa: E731
    if identity is None:
        identity = entry.get("name")

    if not entry.get("uuid"):
        entry["uuid"] = _own_uuid(cfg, section, identity, identity_of,
                                  remembered_uuid, draft_uuid)
    _resolve_references(entry, section, cfg)
    return entry


# The keys a record carries in the FILE only: an in-place edit replaces the whole
# dict with one built from the MODEL, and this build does not emit them. `uuid` is
# the record's identity under format 3 — `format3._f3_records` reads exactly this
# key, and `net_trace_to_dict` deliberately does not write it (a nameless record
# still serializes byte-identically to before). Kept as a tuple so the next such
# key lands HERE and not in one writer of the three.
STORAGE_ONLY_KEYS = ("uuid",)


def keep_identity(old_entry: dict, new_entry: dict) -> dict:
    """Return a COPY of `new_entry` carrying the STORED identity of `old_entry` —
    the record `new_entry` replaces in place.

    The dict-level twin of `identify` (see the module docstring): the copper
    writers (Reread inter-node copper, Extract tree, `write_net_trace`) rebuild a
    `net_traces:` record from a live CAPTURE, so its dict never carries a `uuid`;
    replacing the old dict WHOLE dropped it, the writer stamp then minted a new
    one and every tree node referencing the record dangled — the whole write was
    refused ("format 3: cannot write — N reference(s) point at a uuid that is
    missing from the graph", plan_2026_10_15_reread_copper_uuid_and_selection_any).

    Only `STORAGE_ONLY_KEYS` are carried, and only when the new entry has nothing
    of its own there: a uuid the caller ALREADY set (the form path calls
    `identify` for exactly that) is never overwritten, and no uuid is ever
    invented here — a brand-new record is the writer stamp's business
    (`format3.stamp_format3` п.1), which also inherits by identity on disk."""
    new_entry = copy.deepcopy(new_entry)
    for key in STORAGE_ONLY_KEYS:
        if new_entry.get(key) is None and old_entry.get(key) is not None:
            new_entry[key] = old_entry[key]
    return new_entry


def replace_entry(entries: list, new_entry: dict, *, identity,
                  identity_of: Callable[[dict], Any]) -> bool:
    """Replace the entry of `entries` whose identity is `identity` with
    `new_entry` — carrying the replaced record's stored identity across
    (`keep_identity`) — and APPEND it when the list holds no such record.
    Returns True when a record was replaced.

    The whole in-place-edit step of a list section, in ONE place. The three
    copper writers spelled it out each (walk, match by identity, `entries[i] =
    entry`), and that bare assignment is exactly what dropped the record's uuid:
    the writer stamp then minted a new one and every node referencing the record
    dangled (plan_2026_10_15_reread_copper_uuid_and_selection_any).

    `identity_of` is the caller's own identity spelling for its section (for
    `net_traces:` the raw-dict mirror of `net_trace_effective_name`, i.e.
    `name:`, else the legacy `net:`): passing it in keeps the ONE owner of the
    identity formula where that formula already lives, instead of growing a
    second copy here. A non-dict entry is never a match (`isinstance`), so a
    hand-edited list with a stray item cannot be silently replaced."""
    for i, entry in enumerate(entries):
        if isinstance(entry, dict) and identity_of(entry) == identity:
            entries[i] = keep_identity(entry, new_entry)
            return True
    entries.append(new_entry)
    return False
