# kicadstamp/diagnostics/probe_document_switch.py
"""probe_document_switch.py — does a connected adapter NOTICE that the OPEN BOARD changed?

Input        — nothing; a live KiCad with a board open, and one human action in the
               middle: switch the active PCB editor to a DIFFERENT .kicad_pcb. READ ONLY:
               the probe writes nothing, selects nothing, and touches no profile.
Expected     — three readings of the SAME adapter: before the switch, after it without a
               refresh, and after a refresh; plus, in each one, the live document list it
               is judged against. A refusal to conclude ("NO SWITCH OBSERVED") when the
               live list never changed — a probe that cannot see its own stimulus reports
               a miss, never a kill (deepseek.md §38, second fuse).
Live KiCad   — YES, read only, plus one human action in the KiCad window.
Run          — .venv/bin/python -m kicadstamp.diagnostics.probe_document_switch
               (it prints the first reading, then WATCHES the live document list and
               continues by itself as soon as you switch the board — no keypress: the
               terminal it is run from has no stdin, measured with the first version)

WHY this probe exists (plan_2026_09_24_mcp_stale_board §8). `_ensure_open` recreates the
adapter when `get_board_filename()` returns None, and the open question is what that getter
returns when the human opens a DIFFERENT board — a question
`KiCadBoardAdapter.get_board_filename`'s own docstring marks as "empirical, to verify live".

The STATIC reading, to be confirmed or refuted here rather than assumed:
`kipy.board.Board` stores the `DocumentSpecifier` it was constructed with
(`board.py:258-261`) and `Board.name` returns that stored field (`board.py:280-283`), while
`KiCad.get_board()` is the only thing that asks KiCad for the live list
(`kicad.py:225-230`, `get_open_documents(DOCTYPE_PCB)[0]`). So the expectation is: the
getter keeps returning the OLD name until something calls `refresh_board()`, i.e. the
staleness check in `_ensure_open` cannot see a document switch at all — and the fix under
test reaches the new board only because it refreshes on every call.

Each phase prints BOTH the getter and the live list on purpose: a getter that agreed with
the live list in every phase would say the expectation is wrong (or that no switch
happened), and this probe must say so instead of picking the flattering half.

THE ANSWER, measured live on 24.09.2026 (this probe, third machine, KiCad 10.0.6, switching
3CH-AWG-TIA-v103.kicad_pcb -> HiPiMS-v099.kicad_pcb, 332 -> 414 footprints), and the static
expectation held exactly:

  * the frozen getter kept the OLD name ('3CH-AWG-TIA-v103.kicad_pcb') after the switch,
    and `get_footprints()` kept serving the OLD board's 332 footprints — an adapter that
    was not refreshed answers about a board nobody has open;
  * `refresh_board()` followed the switch on the first try (new name, 414 footprints), so
    the per-call refresh of the MCP seam does reach the new document;
  * `_ensure_open`'s re-creation trigger (`get_board_filename() is None`) would NOT have
    fired: it cannot see a document switch at all, because the getter returns the OLD name,
    never None. That is the second hole of the same family — named, not fixed.

TWO THINGS MEASURED WHILE WRITING IT, kept because they are part of the answer's
conditions: (a) `get_board_filename()` is None until the first `refresh_board()` — a plain
adapter has no board object, which is exactly what `_ensure_open`'s re-creation trigger
tests; (b) `KiCad.get_open_documents` can refuse with kipy's ApiError ("no handler available
for request of type ...") while the editor is switching documents AND while the PCB editor
has no document open at all (12 refusals over ~24 s at one point, taken before any of our
code ran), so a probe that polls it must survive a refusal and say what it saw, or it dies
exactly at the moment it should be looking.

Not covered here, named on purpose: whether KiCad keeps the OLD document's handle usable
(`get_footprints` on the pre-switch handle) is reported as data, never judged — an exception
there is a reply from KiCad, not a verdict. Nor is `docs[0]` ordering with SEVERAL pcbs
open: the whole list is printed so the answer is readable either way.
"""
import argparse
import logging
import time

from kicadstamp.constants import DEFAULT_TIMEOUT_MS


def _load_board_riding_out_the_gap(adapter, timeout_s: int = 60) -> tuple:
    """``refresh_board()`` with a retry, because KiCad answers "no handler available for
    request of type ...GetOpenDocuments" while the PCB editor has no frame to serve it —
    measured live on 24.09.2026, first while a board was being switched and then at the
    connect load itself, i.e. this state is reachable BEFORE any of our code runs.

    Returns ``(ok, last_error)``. The retry is here rather than in the shipping code on
    purpose: this is a probe waiting for a human, and `ConnectionManager` must keep failing
    loudly (what that family of failures costs the MCP host is a FINDING for the report,
    not something a diagnostics script may paper over)."""
    deadline = time.monotonic() + timeout_s
    last_error = None
    while True:
        try:
            adapter.refresh_board()
            return True, last_error
        except Exception as exc:  # noqa: BLE001 — a reply from KiCad, reported verbatim
            last_error = f"{type(exc).__name__}: {exc}"
            if time.monotonic() > deadline:
                return False, last_error
            print(f"    (the connect load was refused: {last_error} — retrying)",
                  flush=True)
            time.sleep(2.0)


def _live_documents(adapter) -> tuple:
    """``(names, error)`` for the live list of open PCB documents.

    Reaches into the adapter's kipy client on purpose: this probe is about the DOCUMENT
    SPECIFIER's identity, which no shipped method exposes (the door's rule about private
    handles has exactly one exception — kicadstamp/diagnostics/, where this file lives).
    A refusal is returned, never raised: the editor answers this one with an ApiError while
    it is switching documents, which is precisely when the probe is looking."""
    from kipy.proto.common.types import DocumentType

    try:
        documents = adapter._kicad.get_open_documents(DocumentType.DOCTYPE_PCB)
    except Exception as exc:  # noqa: BLE001 — a reply from KiCad, reported verbatim
        return None, f"{type(exc).__name__}: {exc}"
    return [doc.board_filename for doc in documents], None


def _wait_for_switch(adapter, before: list, timeout_s: int) -> tuple:
    """Block until the live document list changes; ``(documents, error)`` when giving up.

    A human keypress is not used ON PURPOSE (the terminal has no stdin — measured), and a
    fixed sleep would either waste the operator's patience or lie. The probe waits for its
    own STIMULUS instead, which also makes the vacuity fuse exact."""
    deadline = time.monotonic() + timeout_s
    last_error = None
    while True:
        current, error = _live_documents(adapter)
        if error is None:
            last_error = None
            if current != before:
                return current, None
        else:
            last_error = error
        if time.monotonic() > deadline:
            return before, last_error
        time.sleep(2.0)


def _fingerprint(adapter) -> str:
    """What the adapter believes the board HOLDS, or the error it replies with."""
    try:
        return f"{len(adapter.get_footprints())} footprints (cached read)"
    except Exception as exc:  # noqa: BLE001 — a reply from KiCad, reported verbatim
        return f"{type(exc).__name__}: {exc}"


def _reading(adapter, label: str) -> dict:
    getter = adapter.get_board_filename()
    documents, error = _live_documents(adapter)
    print(f"[{label}]")
    print(f"    get_board_filename()  : {getter!r}")
    print(f"    open PCB documents    : "
          f"{documents!r}" + (f"   (query refused: {error})" if error else ""))
    print(f"    what the adapter holds: {_fingerprint(adapter)}")
    return {"getter": getter, "documents": documents}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS,
                        help="IPC timeout handed to the adapter factory")
    parser.add_argument("--wait-switch-s", type=int, default=180,
                        help="how long to wait for the open-document list to change")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)

    from kicadstamp.adapter_factory import create_board_adapter

    # The plain adapter, not the MCP's store-wrapped one: the store layer DELEGATES
    # get_board_filename/refresh_board, and this probe is about identity, so the plainest
    # object that answers the question is the honest one.
    adapter = create_board_adapter(timeout_ms=args.timeout_ms, use_store=False)
    try:
        # The connect load, exactly as `_ensure_open` performs it when it creates an
        # adapter. Without it `get_board_filename()` answers None because `_board` is
        # still None — measured on the first run of this probe, which is why reading 1 is
        # taken AFTER this line and not before.
        loaded, load_error = _load_board_riding_out_the_gap(adapter)
        if not loaded:
            print(f"!! the adapter could not load a board at all: {load_error}\n"
                  "   That is the editor-has-no-frame state, not a result about document "
                  "switching — fix the KiCad side and re-run.")
            return 4
        before = _reading(adapter, "1. BEFORE — as the MCP manager leaves it after connect")

        print(f"\nNow switch the active PCB editor to a DIFFERENT .kicad_pcb. This probe is "
              f"watching the live document list and continues by itself (up to "
              f"{args.wait_switch_s} s)...")
        print("waiting for the switch", end="", flush=True)
        switched_documents, wait_error = _wait_for_switch(adapter, before["documents"],
                                                          args.wait_switch_s)
        changed = switched_documents != before["documents"]
        print(" — the list changed" if changed else
              " — TIMED OUT, the list never changed"
              + (f" (last refusal: {wait_error})" if wait_error else ""))

        switched = _reading(adapter, "2. AFTER the switch, WITHOUT a refresh")
        adapter.refresh_board()
        refreshed = _reading(adapter, "3. AFTER refresh_board()")

        print("\n--- what the readings say ---")
        if not changed:
            print("NO SWITCH OBSERVED: the live document list did not change, so the "
                  "readings above prove nothing — this is a MISS, not a result. Re-run and "
                  "switch the board while the probe waits.")
        else:
            print(f"the live list changed: {before['documents']!r} -> "
                  f"{switched['documents']!r}")
        print(f"frozen getter followed the switch: "
              f"{'YES' if switched['getter'] != before['getter'] else 'NO'}")
        print(f"refresh_board() followed it      : "
              f"{'YES' if refreshed['getter'] != switched['getter'] else 'NO'}")
        print(f"the manager's re-creation trigger (`get_board_filename() is None`) would "
              f"have fired on the switched adapter: "
              f"{'YES' if switched['getter'] is None else 'NO'}")
        return 0 if changed else 3
    finally:
        adapter.close()


if __name__ == "__main__":
    raise SystemExit(main())
