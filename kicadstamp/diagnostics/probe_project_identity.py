# kicadstamp/diagnostics/probe_project_identity.py
"""probe_project_identity.py — does the DocumentSpecifier carry the PROJECT (name + dir),
and what does reading it cost the board?

Input        — nothing; a live KiCad with a board open. READ ONLY: nothing is written,
               nothing is selected, no raw-write tool is reached, no profile is read.
               RUN WITH EXACTLY ONE KiCad INSTANCE (М9 of design_2026_09_24_project_
               identity_and_kicad_pro): the IPC socket is glued to the instance that
               started FIRST (`kipy._default_socket_path()` returns
               `ipc:///tmp/kicad/api.sock`), so with two editors up the probe may be
               talking to a different one than the window you switch — and then phase
               (г) reports a miss that has nothing to do with the question.
Expected     — (а) `project.name`/`project.path` non-empty and the directory exists on
               disk; (б) reading them costs ZERO kipy round trips, WITH A POSITIVE
               CONTROL proving the counter can see traffic at all; (в) the `.kicad_pro`
               file assembles from the two fields, and its `sheets` count; (г) the stored
               specifier is FROZEN across a document switch, exactly like
               `board_filename`, and catches up only on `refresh_board()`; (д) what an
               open board with NO project reports.
Live KiCad   — YES, read only, plus human actions: switch the active PCB editor to a
               different board (phase г), and — for (д) — open a standalone .kicad_pcb.
Run          — .venv/bin/python -m kicadstamp.diagnostics.probe_project_identity
               .venv/bin/python -m kicadstamp.diagnostics.probe_project_identity --no-wait

WHY this probe exists, and why it comes FIRST (plan_2026_09_24_project_identity_from_ipc
§2). The whole entry stands on the claim "`project` is always there and reads for free".
The claim is PLAUSIBLE — `kipy.board.Board.document` is a public property
(`board.py:271-274`) returning the stored `DocumentSpecifier`, and `Board.get_project()`
turns out to be LOCAL as well (`board.py:276-278` → `kipy/project.py:32-39`: the
constructor only copies the specifier and rewrites its own `type`, it sends nothing —
verified statically 24.09.2026, which is why the probe counts ROUND TRIPS rather than
calls to `get_project`). Plausible is not measured: if the claim is false in any point
below, the shape of the shipping getter changes, and that must be known BEFORE the edit.

The two reasons this probe reports a MISS instead of a verdict when it cannot see its own
stimulus, and never an inference from a silent zero:

  * if the live document list does not change, phase (г) prints "NO SWITCH OBSERVED" and
    draws no conclusion — a probe that cannot see its stimulus reports a miss, never a
    kill (deepseek.md §38, second fuse);
  * the cost column carries a POSITIVE CONTROL (`refresh_board()` must register ≥ 1 round
    trip). A counter that reads zero on everything proves nothing, and a zero produced by
    a broken patch would look exactly like the success this probe is here to confirm
    (deepseek.md §39: a check that fires on a healthy sample is empty).

Reaching into `adapter._board` below is deliberate and lawful: the door's rule about the
private handle has exactly ONE exception — `kicadstamp/diagnostics/` — and this file lives
there, for the same reason `probe_document_switch.py` does: the question is about the
`DocumentSpecifier`, which no shipped method exposes (yet).

The `.kicad_pro` file is READ here and NOWHERE else in this entry: plan §8 forbids shipping
code from touching it, and this probe is the only place that answers (в).
"""
import argparse
import json
import logging
import time
from pathlib import Path

from kicadstamp.constants import DEFAULT_TIMEOUT_MS


def _kipy_error_classes() -> tuple:
    """The TWO refusal classes, imported lazily and named together.

    They are NOT relatives (measured cold, 24.09.2026: `issubclass(kipy's
    ConnectionError, the built-in one)` is False, and so is `issubclass(..., OSError)`),
    so catching only one leaves the other arriving as a crash — that was the previous
    entry's defect, and it is quoted in `mcp_server/connection.py`'s own docstring.
    """
    from kipy.errors import ApiError
    from kipy.errors import ConnectionError as KipyConnectionError

    return (ApiError, KipyConnectionError)


def _load_board_riding_out_the_gap(adapter, timeout_s: int = 60) -> tuple:
    """`refresh_board()` with a retry, because KiCad answers "no handler available for
    request of type ...GetOpenDocuments" while the PCB editor has no frame to serve it —
    measured live on 24.09.2026 by `probe_document_switch.py`, i.e. this state is
    reachable BEFORE any of our code runs.

    Returns `(ok, last_error)`. The retry lives in the probe, never in shipping code:
    `ConnectionManager` must keep failing loudly, and what that refusal family costs the
    MCP host is a FINDING for the report, not something a diagnostics script papers over.
    """
    classes = _kipy_error_classes()
    deadline = time.monotonic() + timeout_s
    last_error = None
    while True:
        try:
            adapter.refresh_board()
            return True, last_error
        except classes as exc:  # the expected refusals, both named above
            last_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 — reported as UNEXPECTED, not swallowed
            last_error = f"UNEXPECTED {type(exc).__name__}: {exc}"
        if time.monotonic() > deadline:
            return False, last_error
        print(f"    (the connect load was refused: {last_error} — retrying)", flush=True)
        time.sleep(2.0)


def _raw_project(document) -> tuple:
    """`(name, path)` straight off a DocumentSpecifier's `project` field.

    No None-guard on purpose: an UNSET `project` reads as empty strings rather than
    raising (proto3), and that empty string is exactly the evidence case (д) is about —
    it must survive to the printout instead of being collapsed into `None`.
    """
    return (document.project.name, document.project.path)


def _stored_identity(adapter) -> tuple:
    """What the adapter's OWN stored specifier says, or the reason it cannot be read.

    This is what the shipping getter will read (plan §3), so the probe measures the very
    source the edit is planned against rather than a look-alike.
    """
    board = getattr(adapter, "_board", None)
    if board is None:
        return None, "no board handle on the adapter (refresh_board() has not run)"
    document = getattr(board, "document", None)
    if document is None:
        return None, "the board handle has no `document` attribute"
    try:
        name, path = _raw_project(document)
        filename = board.name
    except Exception as exc:  # noqa: BLE001 — a reply from KiCad/the wrapper, verbatim
        return None, f"{type(exc).__name__}: {exc}"
    return {"filename": filename, "project_name": name, "project_path": path}, None


def _live_documents(adapter) -> tuple:
    """`(rows, error)` for the live list of open PCB documents, project included.

    Reaches into the adapter's kipy client on purpose (see the module docstring). A
    refusal is RETURNED, never raised: the editor answers this with an ApiError while it
    is switching documents, which is precisely when the probe is looking.
    """
    from kipy.proto.common.types import DocumentType

    try:
        documents = adapter._kicad.get_open_documents(DocumentType.DOCTYPE_PCB)
    except Exception as exc:  # noqa: BLE001 — a reply from KiCad, reported verbatim
        return None, f"{type(exc).__name__}: {exc}"
    rows = []
    for doc in documents:
        name, path = _raw_project(doc)
        rows.append({"filename": doc.board_filename,
                     "project_name": name, "project_path": path})
    return rows, None


def _fingerprint(rows) -> tuple:
    """A comparable shape for the live list, so "did the list change" is one comparison."""
    if rows is None:
        return ()
    return tuple(sorted((r["filename"], r["project_name"], r["project_path"])
                        for r in rows))


def _kicad_pro_report(project_name: str, project_path: str) -> dict:
    """(в): assemble `.kicad_pro` from the two fields and count its sheets.

    Read-only, and the ONLY place in this entry that touches the file (plan §8).
    """
    if not project_name or not project_path:
        return {"path": None, "exists": False, "sheets": None, "unique_sheets": None,
                "note": "the project fields are empty — there is no .kicad_pro to assemble"}
    path = Path(project_path) / (project_name + ".kicad_pro")
    if not path.exists():
        return {"path": str(path), "exists": False, "sheets": None,
                "unique_sheets": None, "note": "assembled path does not exist"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — a reply from the filesystem, verbatim
        return {"path": str(path), "exists": True, "sheets": None, "unique_sheets": None,
                "note": f"could not be parsed: {type(exc).__name__}: {exc}"}
    sheets = data.get("sheets", [])
    uuids = {str(pair[0]) for pair in sheets if isinstance(pair, (list, tuple)) and pair}
    return {"path": str(path), "exists": True, "sheets": len(sheets),
            "unique_sheets": len(uuids), "note": None}


class _KipyBoundaryCounter:
    """Counts the round trips where they really happen — at kipy, not at the adapter.

    Two patches, installed for a `with` block and always removed: the client's document
    query (the round trip behind `get_board`/`refresh_board`) and kipy's own footprint
    read (the expensive one). A counter on the ADAPTER cannot answer this question at
    all: the adapter CACHES, so its call count shows zero where the board was read — the
    Ш1 lesson, quoted in `refresh_board_before_live_read`'s own docstring.
    """

    def __init__(self):
        self.document_queries = 0
        self.footprint_reads = 0
        self._originals = []

    @property
    def total(self) -> int:
        return self.document_queries + self.footprint_reads

    def __enter__(self):
        import kipy
        from kipy.board import Board

        original_docs = kipy.KiCad.get_open_documents
        original_board_read = Board.get_footprints
        counter = self

        def counting_docs(kicad_self, doc_type):
            counter.document_queries += 1
            return original_docs(kicad_self, doc_type)

        def counting_board_read(board_self, *args, **kwargs):
            counter.footprint_reads += 1
            return original_board_read(board_self, *args, **kwargs)

        kipy.KiCad.get_open_documents = counting_docs
        Board.get_footprints = counting_board_read
        self._originals = [(kipy.KiCad, "get_open_documents", original_docs),
                           (Board, "get_footprints", original_board_read)]
        return self

    def __exit__(self, *exc_info):
        for owner, name, original in self._originals:
            setattr(owner, name, original)
        self._originals = []
        return False


def _row(label: str, counter: "_KipyBoundaryCounter") -> str:
    return (f"    {label:44} documents={counter.document_queries:<3} "
            f"footprints={counter.footprint_reads:<3} total={counter.total}")


def _reading(adapter, label: str) -> dict:
    """One phase of (г): the stored specifier AND the live list, printed together.

    Both are printed on purpose: a "stored" value that agreed with the live list in every
    phase would say the expectation is wrong (or that no switch happened) — and this probe
    must say so instead of picking the flattering half.
    """
    stored, stored_error = _stored_identity(adapter)
    documents, documents_error = _live_documents(adapter)
    print(f"[{label}]")
    if stored is None:
        print(f"    stored specifier      : unreadable — {stored_error}")
    else:
        print(f"    stored board_filename : {stored['filename']!r}")
        print(f"    stored project        : name={stored['project_name']!r} "
              f"path={stored['project_path']!r}")
    if documents is None:
        print(f"    open PCB documents    : query refused — {documents_error}")
    else:
        print(f"    open PCB documents    : {documents!r}")
    return {"stored": stored, "documents": documents, "live": _fingerprint(documents)}


def _wait_for_switch(adapter, before: tuple, timeout_s: int) -> tuple:
    """Block until the live document list changes; `(rows, error)` when giving up.

    No keypress: the terminal the probe runs from has no stdin (measured by
    `probe_document_switch.py`), and a fixed sleep would either waste the operator's
    patience or lie. Waiting for the probe's own STIMULUS is also what makes the vacuity
    fuse exact.

    THE POLL IS NOT SILENT, and that is a FIX, not a nicety. Measured live 24.09.2026 on
    the first run of this probe: the operator switched the board, said so, and the probe
    sat at "waiting for the switch" with nothing else on screen — because
    `get_open_documents` refuses with kipy's ApiError for seconds at a time while the
    editor is switching (the same behaviour `probe_document_switch.py` measured: 12
    refusals over ~24 s). A silent loop cannot tell a refusal storm from a slow change,
    so every state change is now printed WITH the poll's own elapsed time, plus a 15 s
    heartbeat: a miss has to be READABLE, not merely long.

    An UNKNOWN baseline is handled explicitly. When reading 1 could not query the live
    list, `before` is empty — and taking the first successful poll as a change would
    report a switch that never happened, i.e. a false verdict about freezing. The first
    successful poll becomes the baseline instead, and says so.
    """
    started_at = time.monotonic()
    deadline = started_at + timeout_s
    last_error = None
    last_reported = None
    last_heartbeat = started_at
    polls = 0
    if not before:
        print("    (the baseline list is UNKNOWN — the first successful poll becomes it)")
    while True:
        tick = time.monotonic()
        rows, error = _live_documents(adapter)
        took_ms = (time.monotonic() - tick) * 1000.0
        polls += 1
        if error is None:
            current = _fingerprint(rows)
            if current != last_reported:
                if not before:
                    label = "BASELINE (was unknown)"
                elif current != before:
                    label = "CHANGED"
                else:
                    label = "unchanged"
                print(f"\n    poll {polls}: {len(rows)} document(s) in {took_ms:.0f} ms — "
                      f"{label}")
                for row in rows:
                    print(f"        {row}")
                last_reported = current
                if not before:
                    before = current   # adopted, never mistaken for a switch
            if before and current != before:
                return rows, None
        else:
            last_error = error
            if error != last_reported:
                print(f"\n    poll {polls}: query refused after {took_ms:.0f} ms — {error}")
                last_reported = error
        now = time.monotonic()
        if now - last_heartbeat >= 15.0:
            print(f"    ... still waiting ({now - started_at:.0f} s of {timeout_s} s, "
                  f"{polls} polls)", flush=True)
            last_heartbeat = now
        if now > deadline:
            return None, last_error
        time.sleep(2.0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS,
                        help="IPC timeout handed to the adapter factory")
    parser.add_argument("--load-timeout-s", type=int, default=60,
                        help="how long to ride out a refused connect load")
    parser.add_argument("--wait-switch-s", type=int, default=180,
                        help="how long to wait for the open-document list to change")
    parser.add_argument("--no-wait", action="store_true",
                        help="skip phase (г) — the run for case (д), where a standalone "
                             "board is open and there is nothing to switch")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)

    from kicadstamp.adapter_factory import create_board_adapter

    # The plain adapter, not the MCP's store-wrapped one: the store layer DELEGATES
    # every other method, and this probe is about the DocumentSpecifier, so the plainest
    # object that answers the question is the honest one (use_store=False is the NAMED
    # bare mode — an explicit spelling, not an absence of an edit).
    adapter = create_board_adapter(timeout_ms=args.timeout_ms, use_store=False)
    answers = {"a": None, "b": None, "c": None, "g": None, "d": None}
    try:
        # The connect load, exactly as `_ensure_open` performs it when it creates an
        # adapter. Without it there is no board handle at all and every reading below
        # would report "no board handle" instead of the truth about `project`.
        loaded, load_error = _load_board_riding_out_the_gap(adapter, args.load_timeout_s)
        if not loaded:
            print(f"!! the adapter could not load a board at all: {load_error}\n"
                  "   That is the editor-has-no-frame state, not a result about the "
                  "project field — fix the KiCad side and re-run.")
            return 4

        print(f"KiCad version            : {adapter.get_version()!r}")
        print(f"get_board_filename()     : {adapter.get_board_filename()!r}\n")

        # ── (а) are the fields there, and does the path exist ────────────────────────
        first = _reading(adapter, "1. BEFORE — as the MCP manager leaves it after connect")
        stored = first["stored"] or {}
        name = stored.get("project_name", "")
        path = stored.get("project_path", "")
        directory_exists = bool(path) and Path(path).is_dir()
        print("\n(а) FIELDS PRESENT?")
        print(f"    project.name : {name!r}  (non-empty: {bool(name)})")
        print(f"    project.path : {path!r}  (non-empty: {bool(path)}, "
              f"directory exists: {directory_exists})")
        answers["a"] = {"name": name, "path": path,
                        "both_non_empty": bool(name) and bool(path),
                        "directory_exists": directory_exists}

        # ── (б) what it costs, counted at the kipy boundary, with a positive control ──
        print("\n(б) WHAT DOES IT COST? (counted at the kipy boundary, not at the adapter)")
        counter = _KipyBoundaryCounter()
        with counter:
            # The calls are made FOR their cost, not for their values: one identity read
            # twice plus the existing getter is the exact work the shipping getter adds to
            # every tool call, so what is counted here is the price of the edit itself.
            _stored_identity(adapter)
            adapter.get_board_filename()
            _stored_identity(adapter)
        print(_row("identity read x2 + get_board_filename()", counter))
        cost = counter.total
        print(f"    → the identity read cost {cost} kipy round trips (expected: 0)")

        control = _KipyBoundaryCounter()
        control_refusal = None
        try:
            with control:
                adapter.refresh_board()
        except _kipy_error_classes() as exc:
            # A refusal is NEITHER a broken counter NOR a zero: it means "the control could
            # not run". Reporting it as either one would be the §39 mistake — turning a
            # missing measurement into a fact about something else.
            control_refusal = f"{type(exc).__name__}: {exc}"
        print(_row("POSITIVE CONTROL: refresh_board()", control))
        if control_refusal:
            print(f"    !! the control could NOT run: {control_refusal}\n"
                  f"       — so the zero above is UNVERIFIED, not confirmed. Re-run when "
                  f"the socket is free.")
            control_ok = None
        else:
            control_ok = control.total >= 1
            if not control_ok:
                print("    !! the control registered NOTHING, so the counter cannot see "
                      "traffic at all —\n       the zero above is a broken check, NOT a "
                      "result.")
        answers["b"] = {"cost": cost, "control": control.total, "control_ok": control_ok,
                        "control_refusal": control_refusal}

        # ── (в) does the .kicad_pro assemble, and how many sheets does it hold ────────
        report = _kicad_pro_report(name, path)
        print("\n(в) .kicad_pro ASSEMBLES?")
        print(f"    path         : {report['path']!r}")
        print(f"    exists       : {report['exists']}")
        print(f"    sheets       : {report['sheets']}"
              + (f"  (unique uuids: {report['unique_sheets']})"
                 if report["sheets"] is not None else ""))
        if report["note"]:
            print(f"    note         : {report['note']}")
        answers["c"] = report

        # ── (г) three phases across a document switch ────────────────────────────────
        print()
        if args.no_wait:
            print("(г) DOCUMENT SWITCH: SKIPPED by --no-wait (this is the run for (д))")
            answers["g"] = {"skipped": True}
        else:
            print(f"Now switch the active PCB editor to a DIFFERENT .kicad_pcb. This probe "
                  f"is watching the live document list and continues by itself (up to "
                  f"{args.wait_switch_s} s)...")
            print("waiting for the switch", end="", flush=True)
            switched_documents, wait_error = _wait_for_switch(adapter, first["live"],
                                                             args.wait_switch_s)
            changed = switched_documents is not None
            print(" — the list changed" if changed else
                  " — TIMED OUT, the list never changed"
                  + (f" (last refusal: {wait_error})" if wait_error else ""))

            after = _reading(adapter, "2. AFTER the switch, WITHOUT a refresh")
            # The refresh goes through the SAME retrying loader as the connect load: this
            # socket is shared with a running GUI, and a probe that dies here dies exactly
            # at the moment it should be looking. A refusal leaves the third reading
            # UNAVAILABLE — which is a miss, never a contradiction of the expectation.
            refreshed_ok, refresh_error = _load_board_riding_out_the_gap(adapter, 20)
            if refreshed_ok:
                refreshed = _reading(adapter, "3. AFTER refresh_board()")
            else:
                print("[3. AFTER refresh_board()]")
                print(f"    the refresh was REFUSED: {refresh_error}")
                print("    → the third reading is UNAVAILABLE, so the catch-up half of (г) "
                      "is UNMEASURED.")
                refreshed = {"stored": None}

            print("\n--- what the readings say about (г) ---")
            if not changed:
                print("NO SWITCH OBSERVED: the live document list did not change, so the "
                      "readings above prove NOTHING — this is a MISS, not a result.\n"
                      "Check that only ONE KiCad instance is running (the socket is glued "
                      "to the one that started first — М9), then re-run and switch the "
                      "board while the probe waits.")
            else:
                old_stored = (first["stored"] or {}).get("project_name")
                new_stored = (after["stored"] or {}).get("project_name")
                new_live = (switched_documents or [{}])[0].get("project_name")
                after_refresh = (refreshed["stored"] or {}).get(
                    "project_name", "<unavailable — the refresh was refused>")
                print(f"    stored project before switch : {old_stored!r}")
                print(f"    stored project right after   : {new_stored!r}"
                      f"   (live list says: {new_live!r})")
                print(f"    stored project after refresh : {after_refresh!r}")
                if not refreshed_ok:
                    print("    → INCONCLUSIVE: the stored specifier's catch-up half is "
                          "unmeasured, so nothing about it can be claimed either way.")
                elif new_stored == old_stored and new_live != old_stored:
                    print("    → FROZEN as expected: the stored specifier kept the old "
                          "project until refresh_board() — the new getter inherits the "
                          "same staleness as get_board_filename().")
                elif new_stored != old_stored:
                    print("    → NOT frozen: the stored specifier followed the switch by "
                          "itself. That CONTRADICTS the expectation — report it.")
            answers["g"] = {"skipped": False, "changed": changed,
                            "refreshed_ok": refreshed_ok,
                            "after": after["stored"], "refreshed": refreshed["stored"]}

        # ── (д) the case the design leaves open: a board with no project ──────────────
        print("\n(д) THE CASE WITHOUT A PROJECT")
        empty = (not name) and (not path)
        partial = bool(name) != bool(path)
        if empty:
            print("    REPRODUCED: `project.name` and `project.path` are BOTH EMPTY on the "
                  "open board.\n    The defensive contract (empty → None) matches what the "
                  "IPC really says.")
            answers["d"] = {"reproduced": True, "kind": "both-empty",
                            "name": name, "path": path}
        elif partial:
            print(f"    REPRODUCED (PARTIAL): only one of the two is empty — "
                  f"name={name!r} path={path!r}.\n    The shipping contract must treat a "
                  f"half-empty project as 'no' too.")
            answers["d"] = {"reproduced": True, "kind": "partial",
                            "name": name, "path": path}
        else:
            print("    NOT REPRODUCED: this board is open INSIDE a project, so nothing here "
                  "says what\n    the field holds without one. Open a standalone "
                  ".kicad_pcb (File > Open Board,\n    not through a project) and re-run "
                  "with --no-wait. Until then the shipping\n    getter must stay "
                  "DEFENSIVE and its docstring must say 'not verified live'.")
            answers["d"] = {"reproduced": False, "name": name, "path": path}

        # ── the five answers, in words, for the report ───────────────────────────────
        print("\n=== THE FIVE ANSWERS (copy these into the done-report) ===")
        print(f"(а) fields on the live board : name={name!r} path={path!r} "
              f"dir_exists={directory_exists}")
        print(f"(б) cost of reading them     : {cost} kipy round trips "
              f"(positive control refresh_board() = {control.total})")
        print(f"(в) .kicad_pro               : {report['path']!r} exists={report['exists']} "
              f"sheets={report['sheets']} unique={report['unique_sheets']}")
        if answers["g"] and answers["g"].get("skipped"):
            print("(г) document switch          : NOT MEASURED (--no-wait)")
        else:
            print(f"(г) document switch          : changed={answers['g']['changed']}")
        print(f"(д) board without a project  : {answers['d']}")
        return 0
    finally:
        # A probe that builds its own adapter closes its own socket (door.md rule 4) —
        # and it closes it on BOTH paths, the success one and the exception one.
        adapter.close()


if __name__ == "__main__":
    raise SystemExit(main())
