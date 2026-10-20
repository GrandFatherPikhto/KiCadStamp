# kicadstamp/kicad/kipy_board_identity.py
"""The ONE cheap IPC request that answers "WHICH board does the PCB editor have
open RIGHT NOW" — the kipy seam of item В, plan_2026_10_15_honest_live_read.

Why it exists at all (Denis's correction to Ч0, 20.10): `adapter.get_board_filename()`
reads `Board.name` off the descriptor captured by `connect()` / the last
`refresh_board()`. Switching the board inside KiCad does NOT touch that
descriptor, so comparing the stored name with itself notices nothing, ever. A
request is unavoidable — and it must be the CHEAPEST one that can answer: kipy's
`KiCad.get_open_documents(DOCTYPE_PCB)` is a `GetOpenDocuments` round trip that
carries no board data at all (no items, no fields, no selection).

The name it returns is `DocumentSpecifier.board_filename` — the SAME field
`Board.name` is built from (kicadstamp.diagnostics.probe_project_identity
measured both on 24.09.2026: the BARE filename, e.g.
'3CH-AWG-TIA-v103.kicad_pcb', never a path). Comparison nevertheless goes
through `os.path.basename`, because the shape belongs to KiCad and a path-shaped
answer must not read as "a different board".

Lives next to `kipy_selection.py` / `kipy_connect.py` on purpose: the adapter
keeps NO new code (Denis's "гиганты только худеют" rule), and this module is
importable without Qt.
"""
import os

# The same import kipy itself uses (kipy/kicad.py) — DocumentType is a Python
# enum in `kipy.proto.common.types`, not a generated *_pb2 module attribute.
from kipy.proto.common.types import DocumentType


def read_open_board_name(kicad) -> str | None:
    """The bare filename of the PCB document open in the editor, or None when
    there is no PCB document open (or no `kicad` handle at all).

    ONE `GetOpenDocuments` request. Raises whatever kipy raises when the PCB
    editor has no frame to serve it (kipy's ApiError, "no handler available") —
    the caller of an AUTOMATIC tick must treat that as "nothing to say", not as
    a lost connection (see gui/board_switch_watch.py)."""
    if kicad is None:
        return None
    documents = kicad.get_open_documents(DocumentType.DOCTYPE_PCB)
    if not documents:
        return None
    name = getattr(documents[0], "board_filename", None)
    return name or None


def board_name_changed(live_name: str | None, known_name: str | None) -> bool:
    """Whether the LIVE board differs from the one the connection knows.

    None on either side is NOT a change: "the editor reports no PCB document"
    and "the connection never learned a name" are both unknown, and an
    automatic tick must never turn an unknown into a full board re-read."""
    if not live_name or not known_name:
        return False
    return os.path.basename(live_name) != os.path.basename(known_name)
