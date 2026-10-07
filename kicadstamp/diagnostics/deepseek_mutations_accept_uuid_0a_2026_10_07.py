# kicadstamp/diagnostics/deepseek_mutations_accept_uuid_0a_2026_10_07.py
"""Acceptance rows for the 0а-1/0а-2 dodelka of plan_2026_10_05_uuid_tails
(DeepSeek, 2026-10-07): «Сохранить» after a rename in the form edits the record
IN PLACE.

  * M1 matching by NAME again (the uuid is ignored)  -> the rename APPENDS a
                                                        second record
  * M2 the name-taken conflict is not refused        -> a foreign record is
                                                        overwritten / the writer
                                                        stamp fatals
  * M3 the chain form drops the uuid again           -> a chain rename appends
  * K1 a cosmetic comment                            -> MUST survive

Guards: tests/config/test_upsert_identity.py (the write primitive) and
tests/gui/docks/test_rename_save_in_place.py (the five Save paths).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_accept_uuid_0a_2026_10_07.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

WRITER = "kicadstamp/config_writer.py"
CHAIN = "gui/docks/chain.py"
G = ["test_upsert_identity.py", "test_rename_save_in_place.py"]

ROWS = [
    # M1 — the pre-0а behaviour: only the NAME can match, so a rename finds no
    # record and appends a second one (same uuid) -> un-saveable.
    ("M1 matching by name again (uuid ignored)", WRITER,
     "        if uuid and existing_entry.get(\"uuid\") == uuid:",
     "        if False:  # MUTATION",
     "die", G, ()),
    # M2 — the conflict is not refused: the rename lands on a name that belongs
    # to another record and that record is silently replaced.
    ("M2 the name-taken conflict is not refused", WRITER,
     "    if taken is not None:",
     "    if False:  # MUTATION",
     "die", G, ()),
    # M3 — the chain-mode form stops carrying the loaded chain's uuid, so its
    # rename appends again (the defect the chain cell found).
    ("M3 the chain form drops the uuid", CHAIN,
     "            if self._chain_entry.get(\"uuid\"):\n"
     "                entry[\"uuid\"] = self._chain_entry[\"uuid\"]",
     "            if False:  # MUTATION\n"
     "                entry[\"uuid\"] = self._chain_entry[\"uuid\"]",
     "die", G, ()),
    # K1 — cosmetic comment (control): must survive.
    ("K1 cosmetic comment (control)", WRITER,
     "logger = logging.getLogger(__name__)",
     "logger = logging.getLogger(__name__)  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
