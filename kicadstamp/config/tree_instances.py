# kicadstamp/config/tree_instances.py
"""
config/tree_instances.py — `tree_instances:` expansion.

Pure dict work that runs AFTER resolve_includes() + expand_sheet_templates()
and BEFORE any per-entry loader (_load_entity/_load_tree) — see
techdocs/handoff/deepseek/plan_2026_09_02_tree_instances.md (P0, 2026-09-02
revision: dict-level expansion).

A `tree_instances:` declaration is a SHORT reference to a template tree:

    tree_instances:
      - template: dac_buf      # name of an existing trees: entry
        name: ch1_dac_buf      # the generated tree's name
        sheet: Channel_1       # substituted into the copies

expand_tree_instances() materializes, for each declaration, ONE full Tree dict
(deep copy of the template: nodes get `ref += __{instance.name}` recursively,
the role anchor's sheet becomes the instance's) plus one Entity dict per
template node (deep copy of the referenced Entity, renamed to the new ref,
sheet = instance's sheet), and appends them to `trees:`/`entities:`.

The materialized dicts then flow through the SAME _load_entity/_load_tree path
as hand-written entries — duplicate-name checks, rule 2 (shared seen_refs),
unknown-key checks and the layer/mirror cross-validation apply to them for
free, with zero duplicated validation logic (the reason this is dict-level and
NOT post-dataclass: see the plan revision).

The raw `tree_instances:` key is deliberately LEFT INTACT in the returned
dict — the loader parses it into cfg.tree_instances, the persistence source
and the GUI's read-only-instance index. Materialized trees/entities are never
persisted as such (the GUI's TreesDock save path excludes them).

v1 template constraints (each is a hard fatal, never a silent skip):
  - the template tree's anchor must be `role`-based (origin/ref/point/auto
    anchors are not parameterized by sheet yet);
  - every template node must be kind=placement (or unset/auto) and must
    reference an existing entities: entry (chain/coordinate/clone/module/
    net_trace nodes inside a template are not instantiated yet);
  - a referenced template Entity must NOT carry its own `sheet` (Q2) — sheet
    comes from the instance; a template entity with a sheet is a usage error.
"""
import copy
import logging

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _

logger = logging.getLogger(__name__)


def _expand_node(node: dict, instance_name: str, sheet: str,
                 entities_by_name: dict, generated_entities: list,
                 template_name: str) -> dict:
    """Deep-copy one template node dict into the instance shape: the node's
    ref is suffixed with __{instance_name} (recursively through children), its
    matching template Entity is copied into generated_entities under the new
    ref with the instance sheet. v1: only kind=placement (or unset/auto) nodes
    whose ref names an existing Entity are allowed inside a template."""
    orig_ref = node.get('ref')
    if orig_ref is None:
        raise ValidationError(format_fatal_error(
            _("tree_instance: template {template!r} has a node without a ref")
            .format(template=template_name),
            [_("every template node needs a ref naming an entities: entry")]))
    kind = node.get('kind')
    if kind is not None and kind != 'placement':
        raise ValidationError(format_fatal_error(
            _("tree_instance: template {template!r} has unsupported node kind "
              "{kind!r} (ref {ref!r})").format(template=template_name, kind=kind,
                                               ref=orig_ref),
            [_("v1 tree templates support only kind=placement (Entity) nodes — "
               "chain/coordinate/clone/module/net_trace nodes inside a template "
               "are not instantiated yet")]))
    entity = entities_by_name.get(orig_ref)
    if entity is None:
        raise ValidationError(format_fatal_error(
            _("tree_instance: template {template!r} node {ref!r} has no matching "
              "entities: record").format(template=template_name, ref=orig_ref),
            [_("every node of a tree template must reference an entities: entry "
               "(v1 templates are entity placements only)")]))
    if entity.get('sheet') is not None:
        raise ValidationError(format_fatal_error(
            _("tree_instance: template entity {ref!r} must not have its own "
              "sheet — sheet comes from the instance").format(ref=orig_ref),
            [_("a tree template's entities are the geometry master with no sheet "
               "of their own; every instance substitutes its own sheet at load "
               "— remove the sheet: from the template entity, or do not use "
               "this tree as a template")]))

    new_ref = f"{orig_ref}__{instance_name}"
    gen = copy.deepcopy(node)
    gen['ref'] = new_ref
    children = node.get('children') or []
    if children:
        gen['children'] = [_expand_node(c, instance_name, sheet, entities_by_name,
                                        generated_entities, template_name)
                           for c in children]
    ent = copy.deepcopy(entity)
    ent['name'] = new_ref
    ent['sheet'] = sheet
    generated_entities.append(ent)
    return gen


def _expand_template(template: dict, template_name: str, instance_name: str,
                     sheet: str, entities_by_name: dict) -> tuple[dict, list]:
    """Materialize ONE instance from a template Tree dict: returns (tree dict,
    [entity dicts]). The template dict is never mutated — deep copies only."""
    anchor = template.get('anchor')
    if not (isinstance(anchor, dict) and isinstance(anchor.get('role'), str)
            and anchor.get('role')):
        raise ValidationError(format_fatal_error(
            _("tree_instance: template {template!r} must be role-anchored (v1)")
            .format(template=template_name),
            [_("only an (anchor (role ...)) template is supported — the instance "
               "sheet substitutes the role anchor's sheet at load; origin/ref/"
               "point/auto anchors are not parameterized by sheet yet")]))

    gen = copy.deepcopy(template)
    gen['name'] = instance_name
    gen['anchor']['sheet'] = sheet
    generated_entities: list = []
    gen['nodes'] = [_expand_node(n, instance_name, sheet, entities_by_name,
                                 generated_entities, template_name)
                    for n in (template.get('nodes') or [])]
    return gen, generated_entities


def expand_tree_instances(data: dict) -> dict:
    """Append one materialized Tree dict + its Entity dicts per tree_instances:
    declaration to a COPY of `data`'s 'trees'/'entities' and return the copy.
    The input dict is never mutated and the raw 'tree_instances:' key survives
    untouched (the loader still parses it into cfg.tree_instances).

    Returns `data` unchanged when there are no tree_instances: declarations."""
    result = dict(data)
    instances = data.get('tree_instances')
    if not instances:
        return result
    if not isinstance(instances, list):
        raise ValidationError(format_fatal_error(
            _("'tree_instances' must be a list of template/name/sheet "
              "declarations"),
            [_("got {type} — expected a list of declarations, each with "
               "template:/name:/sheet: (e.g. template: dac_buf, "
               "name: ch1_dac_buf, sheet: Channel_1)")
             .format(type=type(instances).__name__)]))

    trees = list(data.get('trees') or [])
    entities = list(data.get('entities') or [])
    entities_by_name: dict = {}
    for ent in entities:
        if isinstance(ent, dict) and isinstance(ent.get('name'), str):
            entities_by_name[ent['name']] = ent
    trees_by_name: dict = {}
    for t in trees:
        if isinstance(t, dict) and isinstance(t.get('name'), str):
            trees_by_name[t['name']] = t

    for idx, inst in enumerate(instances):
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
        template = trees_by_name.get(template_name)
        if template is None:
            raise ValidationError(format_fatal_error(
                _("tree_instance {name!r}: template tree {template!r} not found")
                .format(name=instance_name, template=template_name),
                [_("known trees: {names}").format(
                    names=", ".join(sorted(trees_by_name)) or _("(none)"))]))
        generated_tree, generated_entities = _expand_template(
            template, template_name, instance_name, sheet, entities_by_name)
        trees.append(generated_tree)
        entities.extend(generated_entities)
        # Register generated names so a second instance of the SAME template
        # (or a later declaration) resolving by name sees a consistent index;
        # duplicates are still caught downstream by the trees/entities
        # duplicate-name checks.
        trees_by_name[instance_name] = generated_tree
        for ent in generated_entities:
            entities_by_name[ent['name']] = ent

    result['trees'] = trees
    result['entities'] = entities
    logger.info(_("Expanded {count} tree_instances: declarations into "
                  "trees/entities").format(count=len(instances)))
    return result
