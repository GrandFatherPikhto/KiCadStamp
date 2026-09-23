# kicadstamp/diagnostics/probe_document_switch.py
"""probe_document_switch.py — does a connected adapter NOTICE that the OPEN BOARD changed?

Input        — nothing; a live KiCad with a board open, and one human action in the
               middle: open or switch to a DIFFERENT .kicad_pcb. READ ONLY: the probe
               writes nothing, selects nothing, and touches no profile.
Expected     — three readings of the same adapter, one before the switch and two after:
               what the frozen getter says, what the live document list says, and what a
               refresh makes it say. A refusal to conclude ("NO SWITCH OBSERVED") when the
               live list did not actually change — a probe that cannot see its own
               stimulus reports a miss, never a kill (deepseek.md §38, second fuse).
Live KiCad   — YES, read only, plus one human action in the KiCad window.
Run          — .venv/bin/python -m kicadstamp.diagnostics.probe_document_switch
               (it prints the first reading, then waits for Enter)

WHY this probe exists (plan_2026_09_24_mcp_stale_board §8). `_ensure_open` recreates the
adapter when `get_board_filename()` returns None, and the question is what that getter
returns when the human opens a DIFFERENT board — a question the docstring of
`KiCadBoardAdapter.get_board_filename` itself marks as "empirical, to verify live".

The STATIC reading, to be confirmed or refuted here rather than assumed:
`kipy.board.Board` stores the `DocumentSpecifier` it was constructed with
(`board.py:258-261`) and `Board.name` returns that stored field (`board.py:280-283`), while
`KiCad.get_board()` is the only thing that asks KiCad for the live list
(`kicad.py:225-230`, `get_open_documents(DOCTYPE_PCB)[0]`). So the expectation is: the
getter keeps returning the OLD name until something calls `refresh_board()`, i.e. the
staleness check in `_ensure_open` cannot see a document switch at all — and the fix under
test reaches the new board only because it refreshes on every call.

That expectation is what makes the reading below falsifiable, and it is why each phase
prints BOTH the getter and the live list: a getter that agrees with the live list in every
phase would say the expectation is wrong (or that no switch happened), and this probe says
so instead of picking the flattering half.

Not covered here, named on purpose: whether KiCad keeps the OLD document's handle usable
(`get_footprints` on the pre-switch handle) is reported as data, not judged — an exception
there is a reply from KiCad, not a verdict. Nor is `docs[0]` ordering with SEVERAL pcbs
open: the probe prints the whole list so the answer is readable either way.
"""
import argparse
import logging

from kicadstamp.constants import DEFAULT_TIMEOUT_MS


def _open_documents(adapter) -> list[str]:
    """The live list of open PCB documents, straight from KiCad.

    Reaches into the adapter's kipy client on purpose: this probe is about the DOCUMENT
    SPECIFIER's identity, which no shipped method exposes (the door's rule about private
    handles has exactly one exception — kicadstamp/diagnostics/, which is where this
    file lives)."""
    from kipy.proto.common.types import DocumentType

    return [doc.board_filename for doc in
            adapter._kicad.get_open_documents(DocumentType.DOCTYPE_PCB)]


def _fingerprint(adapter) -> str:
    """What the adapter believes the board HOLDS, or the error it replies with."""
    try:
        return f"{len(adapter.get_footprints())} footprints (cached read)"
    except Exception as exc:  # noqa: BLE001 — a reply from KiCad, reported verbatim
        return f"{type(exc).__name__}: {exc}"


def _reading(adapter, label: str) -> dict:
    getter = adapter.get_board_filename()
    documents = _open_documents(adapter)
    print(f"[{label}]")
    print(f"    get_board_filename()  : {getter!r}")
    print(f"    open PCB documents    : {documents!r}")
    print(f"    what the adapter holds: {_fingerprint(adapter)}")
    return {"getter": getter, "documents": documents}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS,
                        help="IPC timeout handed to the adapter factory")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)

    from kicadstamp.adapter_factory import create_board_adapter

    # The plain adapter, not the MCP's store-wrapped one: the store layer DELEGATES
    # get_board_filename/refresh_board, and this probe is about identity, so the plainest
    # object that answers the question is the honest one.
    adapter = create_board_adapter(timeout_ms=args.timeout_ms, use_store=False)
    try:
        before = _reading(adapter, "1. BEFORE — as connected")
        print("\nNow OPEN (or switch to) a DIFFERENT .kicad_pcb in KiCad, leaving the "
              "adapter's process alone.")
        input("Press Enter HERE when the other board is the active one: ")

        switched = _reading(adapter, "2. AFTER the switch, WITHOUT a refresh")
        adapter.refresh_board()
        refreshed = _reading(adapter, "3. AFTER refresh_board()")

        print("\n--- what the readings say ---")
        same_document = (switched["documents"] == before["documents"])
        if same_document:
            print("NO SWITCH OBSERVED: the live document list did not change, so the two "
                  "readings below prove nothing — this is a MISS, not a result. Re-run "
                  "and switch the board while the prompt waits.")
        else:
            print(f"the live list changed: {before['documents']!r} -> "
                  f"{switched['documents']!r}")
        print(f"frozen getter followed the switch: "
              f"{'YES' if switched['getter'] != before['getter'] else 'NO'}")
        print(f"refresh_board() followed it      : "
              f"{'YES' if refreshed['getter'] != switched['getter'] else 'NO'}")
        print(f"the manager's re-creation trigger (`get_board_filename() is None`) would "
              f"have fired: {'YES' if switched['getter'] is None else 'NO'}")
        return 0 if not same_document else 3
    finally:
        adapter.close()


if __name__ == "__main__":
    raise SystemExit(main())
