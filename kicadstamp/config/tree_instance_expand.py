# kicadstamp/config/tree_instance_expand.py
"""ONE `tree_instances:` declaration, from its raw dict to its materialized
records — the body of the loop of `expand_tree_instances`, moved out (giants
only shrink) when К3а of plan_2026_11_07_tree_instances_dialog_and_subtree made
EVERY per-declaration refusal tolerable.

The split is what makes the rule expressible in one place: `expand_tree_
instances` keeps the two GRAPH-WIDE refusals (the section is not a list; two
declarations share one name), and everything that belongs to ONE declaration —
a missing field, a non-mapping `params`/`anchor`, a non-numeric `rotation`, a
malformed `root`, a template that is not there, a
name already used by a hand-written tree, and every refusal of the expansion
itself — is raised HERE, where the caller can catch it per declaration and go
on.

Nothing here is lenient by itself: it always raises. Whether a refusal is fatal
or merely recorded is the CALLER's decision (`failures is None`).
"""
from __future__ import annotations

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _
from .tree_instance_root import parse_root

__all__ = ["expand_one_declaration"]


def expand_one_declaration(idx: int, inst, *, trees: list, entities: list,
                           net_traces: list, trees_by_name: dict,
                           entities_by_name: dict, net_traces_by_name: dict,
                           cells: dict) -> None:
    """Materialize declaration #idx IN PLACE: append its tree, its Entity copies
    and its net_trace copies, and register the generated names in the indexes.

    Every refusal is a `ValidationError` that belongs to THIS declaration alone —
    the caller decides (see the module docstring)."""
    # `_expand_template` stays in tree_instances (it is the expansion's own
    # body); the import is lazy because tree_instances imports THIS module.
    from .tree_instances import _expand_template

    if not isinstance(inst, dict):
        raise ValidationError(format_fatal_error(
            _("tree_instances: entry #{idx} must be a mapping").format(idx=idx + 1),
            [_("each entry is a dict with template:/name:/sheet:")]))
    template_name = inst.get('template')
    instance_name = inst.get('name')
    sheet = inst.get('sheet')
    for field_label, value in (("template", template_name),
                               ("name", instance_name),
                               ("sheet", sheet)):
        if not isinstance(value, str) or not value:
            raise ValidationError(format_fatal_error(
                _("tree_instances: entry #{idx} missing required {field}:")
                .format(idx=idx + 1, field=field_label),
                [_("every tree_instances: entry needs template:/name:/sheet: "
                   "(non-empty strings)")]))
    # v1.3 (plan tree_instances_params_override): OPTIONAL per-instance params
    # merge. Only the "is it a mapping" guard — no per-value checks, same level
    # as Entity.params elsewhere.
    params = inst.get('params')
    if params is not None and not isinstance(params, dict):
        raise ValidationError(format_fatal_error(
            _("tree_instances: entry #{idx} has a non-mapping params:")
            .format(idx=idx + 1),
            [_("params:, when present, must be a mapping of string keys to "
               "string values — omit the key entirely to inherit the "
               "template Entity's own params unchanged")]))
    # §И.2: OPTIONAL own place and angle. The anchor is only checked to BE a
    # mapping here; its grammar is validated by the TREE loader, which sees the
    # very same dict on the generated tree and reports it under that tree's
    # (== the instance's) name.
    decl_anchor = inst.get('anchor')
    if decl_anchor is not None and not isinstance(decl_anchor, dict):
        raise ValidationError(format_fatal_error(
            _("tree_instances: entry #{idx} has a non-mapping anchor:")
            .format(idx=idx + 1),
            [_("anchor:, when present, must be a mapping in the TREE anchor "
               "grammar (origin / ref / role / point / self, plus optional "
               "sheet / cluster / pad / shift) — omit the key entirely to "
               "keep the template's own place")]))
    # Н6: an OPTIONAL `root` — the template node the copy starts from. Its shape
    # is validated in the root module.
    decl_root = parse_root(inst.get('root'), instance_name)
    decl_rotation = inst.get('rotation')
    if decl_rotation is not None and (
            not isinstance(decl_rotation, (int, float))
            or isinstance(decl_rotation, bool)):
        raise ValidationError(format_fatal_error(
            _("tree_instances: entry #{idx} has a non-numeric rotation:")
            .format(idx=idx + 1),
            [_("rotation:, when present, must be a number (degrees) — omit "
               "the key entirely to inherit the template's own angle")]))
    # Э2.1: the instance's name must be FREE — it is the name of the tree this
    # declaration materializes. trees_by_name indexes every tree that exists at
    # this point (literal entries, the templates, and the trees generated by
    # earlier declarations), so a collision is caught HERE, before anything is
    # built.
    if instance_name in trees_by_name:
        raise ValidationError(format_fatal_error(
            _("duplicate tree name from tree_instance {name!r}").format(
                name=instance_name),
            [_("every tree in one config needs a unique name, and a tree "
               "with that name already exists here — a trees: entry, the "
               "template itself, or an earlier tree_instances: declaration — "
               "rename this instance, or rename/remove the tree that already "
               "carries the name")]))
    template = trees_by_name.get(template_name)
    if template is None:
        raise ValidationError(format_fatal_error(
            _("tree_instance {name!r}: template tree {template!r} not found")
            .format(name=instance_name, template=template_name),
            [_("known trees: {names}").format(
                names=", ".join(sorted(trees_by_name)) or _("(none)"))]))
    (generated_tree, generated_entities,
     generated_net_traces) = _expand_template(
        template, template_name, instance_name, sheet, entities_by_name,
        net_traces_by_name, params, decl_anchor, decl_rotation, decl_root, cells)
    trees.append(generated_tree)
    entities.extend(generated_entities)
    net_traces.extend(generated_net_traces)
    # Register generated names so a second instance of the SAME template (or a
    # later declaration) resolving by name sees a consistent index; duplicates
    # are still caught downstream by the trees/entities duplicate-name checks.
    trees_by_name[instance_name] = generated_tree
    for ent in generated_entities:
        entities_by_name[ent['name']] = ent
