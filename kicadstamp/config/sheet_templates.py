# kicadstamp/config/sheet_templates.py
"""
config/sheet_templates.py — `sheet_templates:` expansion.

Pure dict/string work that runs BEFORE any per-entry loader (_load_clone_placement/
_load_coordinate_placement) and BEFORE validation — see
techdocs/handoff/plan_2026_08_16_sheet_templates.md (Этап 0 of the unified
implementation plan).

A `sheet_templates:` block declares a group of clone_placements /
coordinate_placements ONCE and instantiates it once per reused sheet instance
(Channel_0/1/2 are three instances of the same hierarchical sheet pair — the
Cluster/Role tags are identical across instances, only sheet:/anchor_sheet:
tells two physical copies apart). expand_sheet_templates() consumes the block
entirely: generated entries are appended to data['clone_placements'] /
data['coordinate_placements'] and become indistinguishable from hand-written
ones, so downstream loaders and duplicate-name checks never need to know this
mechanism exists.

Reserved tokens, ONLY inside sheet_templates: blocks (nowhere else in the
schema):
  - `anchor_sheet: self` -> the literal name of the sheet currently being
    generated. Deliberately NOT auto-filling every omitted anchor_sheet: —
    an anchor_role: FPGA entry must NOT gain an anchor_sheet it never asked
    for (FPGA is a single, non-sheet-scoped instance); the substitution
    happens only where the template author explicitly wrote `self`. An
    omitted anchor_sheet: stays omitted after expansion.
  - `sheet` (own-identity) is NOT a token: it is ALWAYS auto-filled from the
    current loop sheet name, at any list length (2026-08-21, anchor
    dependency tree plan §1.0) — the (Sheet, Cluster, Role) convention
    completed for templated entries. A template's own `sheet:` value
    (including the historical `self` marker, which equals the loop name) is
    overwritten; the identity string (name:/cluster:) is an INDEPENDENT
    result of the same loop and is unaffected.
  - `$SHEET` inside string values (params:/nets:/net_overrides: hierarchical
    net paths like /$SHEET/DAC/+3V3_AVDD) -> the same sheet name, textual
    substring replacement, not a general-purpose templating engine.

Identity (name: for clone_placements, name: for coordinate_placements —
NOT clone_placements' own cluster:, which is the Cluster tag and is NEVER
touched), clarified 2026-08-16 after an Architect-mode review caught that the
original "respect an explicit value, else auto-generate" wording was ambiguous
AND broke this module's byte-identical regression contract:
  - len(sheets) == 1 -> identity is taken LITERALLY from the template (or left
    to the same default the non-templated path uses, f"{cluster}/{role}"), with
    NO prefix at all — this is what makes the single-sheet regression test a
    true zero diff.
  - len(sheets) >= 2 -> identity is ALWAYS generated as f"{sheet}_{base}"
    (base = the explicitly-set template value, or the same default), OVERWRITING
    whatever the template wrote, no exceptions — a per-sheet-unique identity is
    structurally guaranteed, since _check_duplicate_names keys off the
    effective name which does NOT incorporate sheet.
"""
import copy
import logging

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _

logger = logging.getLogger(__name__)

_TEMPLATE_SECTIONS = ('clone_placements', 'coordinate_placements')
_TEMPLATE_KEYS = ('sheets',) + _TEMPLATE_SECTIONS

_SELF_FIELDS = ('anchor_sheet',)
_SHEET_TOKEN = '$SHEET'


def _substitute_self_fields(entry: dict, sheet: str) -> None:
    """anchor_sheet: self -> the literal generated sheet name. Anything else
    (including absence) is left exactly as written — only the explicit 'self'
    marker is special. (The own-identity `sheet` is no longer a 'self' token:
    the expansion loop auto-fills it unconditionally, see the module
    docstring.)"""
    for key in _SELF_FIELDS:
        if entry.get(key) == 'self':
            entry[key] = sheet


def _substitute_sheet_token(value, sheet: str):
    """Recursively replace $SHEET in every string leaf (net paths in
    params:/nets:/net_overrides: are the real use case; applied uniformly so
    no string-carrying field can be missed)."""
    if isinstance(value, dict):
        return {k: _substitute_sheet_token(v, sheet) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_sheet_token(v, sheet) for v in value]
    if isinstance(value, str):
        return value.replace(_SHEET_TOKEN, sheet)
    return value


def _identity_base(entry: dict, section: str) -> str:
    """The `base` in the multi-sheet identity f"{sheet}_{base}".

    clone_placements: name if the template set it, else the Cluster tag
    `cluster` (the non-templated default identity for a clone_placement IS
    name-or-cluster, see clone_placement_effective_name).
    coordinate_placements: explicit name, else the same default the
    non-templated path uses (coordinate_placement_effective_name's
    f"{cluster}/{role}")."""
    if section == 'clone_placements':
        return entry.get('name') or entry.get('cluster') or ''
    return entry.get('name') or f"{entry.get('cluster', '')}/{entry.get('role', '')}"


def expand_sheet_templates(data: dict) -> dict:
    """Pops 'sheet_templates:' from a copy of `data`, appends one expanded copy
    per sheet in each template's sheets: list to the returned dict's
    'clone_placements'/'coordinate_placements', and returns the new dict.

    The input dict is never mutated; nothing named 'sheet_templates' survives
    into the returned dict.
    """
    result = dict(data)
    templates = result.pop('sheet_templates', None)
    if not templates:
        return result
    if not isinstance(templates, dict):
        raise ValidationError(format_fatal_error(
            _("'sheet_templates' must be a mapping (template name -> {{sheets, ...}})"),
            [_("each entry defines sheets: [<sheet names>] plus clone_placements:/"
               "coordinate_placements: entries to instantiate per sheet")]))

    result['clone_placements'] = list(data.get('clone_placements') or [])
    result['coordinate_placements'] = list(data.get('coordinate_placements') or [])

    for tpl_name, tpl in templates.items():
        if not isinstance(tpl, dict):
            raise ValidationError(format_fatal_error(
                _("sheet_template {name!r} must be a mapping").format(name=tpl_name),
                [_("expected 'sheets:' plus clone_placements:/coordinate_placements: entries")]))
        unsupported = sorted(k for k in tpl if k not in _TEMPLATE_KEYS)
        if unsupported:
            raise ValidationError(format_fatal_error(
                _("sheet_template {name!r} has unsupported key(s): {keys}")
                .format(name=tpl_name, keys=unsupported),
                [_("supported: {keys} — rules: is deliberately out of scope "
                   "(sheet_templates instantiates clone_placements/"
                   "coordinate_placements only)").format(keys=_TEMPLATE_KEYS)]))
        sheets = tpl.get('sheets')
        if not isinstance(sheets, list) or not sheets:
            raise ValidationError(format_fatal_error(
                _("sheet_template {name!r}: 'sheets' must be a non-empty list").format(name=tpl_name),
                [_("e.g. sheets: [Channel_0, Channel_1, Channel_2]")]))
        multi = len(sheets) >= 2
        for section in _TEMPLATE_SECTIONS:
            for entry in tpl.get(section) or []:
                if not isinstance(entry, dict):
                    raise ValidationError(format_fatal_error(
                        _("sheet_template {name!r}: {section} entries must be mappings")
                        .format(name=tpl_name, section=section),
                        [_("got {type}").format(type=type(entry).__name__)]))
                for sheet in sheets:
                    gen = copy.deepcopy(entry)
                    _substitute_self_fields(gen, sheet)
                    gen = _substitute_sheet_token(gen, sheet)
                    # §1.0 (anchor dependency tree): the own-identity sheet is
                    # ALWAYS auto-filled from the loop sheet name, at any list
                    # length — no special case for a single sheet. Overwrites
                    # whatever the template wrote (including 'self', equal to
                    # `sheet`); the identity string below is independent.
                    gen['sheet'] = sheet
                    if multi:
                        if section == 'clone_placements':
                            base = _identity_base(entry, section)
                            if not base:
                                raise ValidationError(format_fatal_error(
                                    _("sheet_template {name!r}: multi-sheet clone_placement "
                                      "needs a cluster: (Cluster tag) to derive name:")
                                    .format(name=tpl_name)))
                            gen['name'] = f"{sheet}_{base}"
                        else:
                            gen['name'] = f"{sheet}_{_identity_base(entry, section)}"
                    result[section].append(gen)

    logger.info(_("Expanded {count} sheet_templates: blocks into "
                  "clone_placements/coordinate_placements").format(count=len(templates)))
    return result
