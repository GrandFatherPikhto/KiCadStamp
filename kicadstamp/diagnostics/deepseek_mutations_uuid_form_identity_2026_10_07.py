# kicadstamp/diagnostics/deepseek_mutations_uuid_form_identity_2026_10_07.py
"""Acceptance rows for part 0 of plan_2026_10_05_uuid_tails (DeepSeek,
2026-10-07): «Перерисовать» from a dock's form must carry the identity of the
record it replaces — the ONE rule `kicadstamp/config/form_identity.identify`.

  * M1 the record's own uuid is not transferred      -> the registry key refuses
  * M2 references are not resolved                   -> anchor_point_uuid/cell_uuid
                                                        stay unset
  * M3 a fresh uuid4 on every call                   -> a rename/new record loses
                                                        its identity
  * M4 one of the five sites skips the rule          -> the thermal redraw loses it
  * K1 a cosmetic comment                            -> MUST survive

Guards: tests/config/test_form_identity.py (the pure rule) and
tests/gui/docks/test_form_identity_redraw.py (the docks' real collection half).

Grown from the machinery of deepseek_mutations_subtract_selection_2026_10_06.py
(rule 38): basename-resolved guards under tests/, `_drop_pyc`, the
`original.count(old) != 1` refusal, a ПРОМАХ verdict on zero reds, and a control
that MUST survive.

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_uuid_form_identity_2026_10_07.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

FORM = "kicadstamp/config/form_identity.py"
THERMAL = "gui/docks/thermal_via.py"
G = ["test_form_identity.py", "test_form_identity_redraw.py"]

ROWS = [
    # M1 — the whole point: without the inherited uuid the spliced record has no
    # identity and the registry key refuses (Р-У5.7).
    ("M1 the record's own uuid is not transferred", FORM,
     "    if not entry.get(\"uuid\"):\n"
     "        entry[\"uuid\"] = _own_uuid(cfg, section, identity, identity_of,\n"
     "                                  remembered_uuid, draft_uuid)",
     "    if not entry.get(\"uuid\"):\n"
     "        entry[\"uuid\"] = None  # MUTATION",
     "die", G, ()),
    # M2 — the record keeps its own uuid but every REFERENCE is left unresolved:
    # the anchor point's uuid / a spoke's cell uuid never reach the record.
    ("M2 references are not resolved", FORM,
     "    for ref in _f3_refs(pseudo):\n"
     "        hint = ref.holder.get(ref.name_field)",
     "    return  # MUTATION\n"
     "    for ref in _f3_refs(pseudo):\n"
     "        hint = ref.holder.get(ref.name_field)",
     "die", G, ()),
    # M3 — the identity is not looked up at all: a rename and a brand-new record
    # each get a different uuid (the preview/save split the plan forbids).
    ("M3 a fresh uuid4 on every call", FORM,
     "    if identity is not None:\n"
     "        for rec in _records(cfg, section):\n"
     "            if identity_of(rec) == identity:\n"
     "                uuid = getattr(rec, \"uuid\", None)\n"
     "                if uuid:\n"
     "                    return uuid\n"
     "    if remembered_uuid:\n"
     "        return remembered_uuid\n"
     "    return draft_uuid or str(uuid4())",
     "    return str(uuid4())  # MUTATION",
     "die", G, ()),
    # M4 — one of the five call sites stops calling the rule (the plan's own
    # "одно из пяти мест не зовёт общую функцию").
    ("M4 the thermal redraw skips the rule", THERMAL,
     "        try:\n"
     "            entry = identify(entry, \"thermal_via_arrays\", cfg=cfg,\n"
     "                             remembered_uuid=self._loaded_uuid,\n"
     "                             draft_uuid=self._draft_uuid)\n"
     "        except ValidationError as e:\n"
     "            self._show_message(str(e), _ERROR_STYLE)\n"
     "            return None\n"
     "        self._draft_uuid = entry.get(\"uuid\")\n"
     "        tva = load_thermal_via_array(entry)",
     "        tva = load_thermal_via_array(entry)  # MUTATION",
     "die", G, ()),
    # K1 — cosmetic comment (control): must survive.
    ("K1 cosmetic comment (control)", FORM,
     "__all__ = [\"FormReferenceMissing\", \"section_uuids\", \"identify\"]",
     "__all__ = [\"FormReferenceMissing\", \"section_uuids\", \"identify\"]  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
