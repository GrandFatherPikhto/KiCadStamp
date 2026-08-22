# `diagnose_first_write_crash.py` — first-write crash ladder (issue #24966)

## Purpose

A reproducible "ladder" for localizing where KiCad dies on the first IPC write after a session starts
(issue #24966). It performs a sequence of reads via kipy (connect → ping → version → open_documents →
get_board → read footprints → re-read), and on the last step — a **no-op transaction**:
`begin_commit()` → `update_items([fp])` with no actual changes → `push_commit()` — the exact path used
in production by `adapter.commit_with_retry()`, not a bare `update_items()`. After every step it takes a
"pulse" (`ping` + a `kicad.exe` PID snapshot) so you can see precisely which step, and at what moment,
the process died. The log is written line-by-line with a flush, so the last line is trustworthy even if
KiCad dies instantly.

For the full picture — the bug itself (#24966), the related bug (#24970), and the rest of the
crash-hunting toolkit — see [crash_hunting.md](crash_hunting.md). This document covers only the
diagnostic script itself.

**Important fix (2026-07-26):** step 9 used to do a bare `update_items()` without `begin_commit`/
`push_commit`, and passed cleanly in exactly the scenario where a real `apply` run crashed live on
Linux/KiCad 10.0.5 — right on `begin_commit()` for the session's first transaction (flips before that
point go through a separate API path, `run_action("...InteractiveEdit.flip")`, bypassing commit — they
don't "warm up" the vulnerable state). Step 9 now honestly wraps the no-op write in the same kind of
transaction used in production, logging each sub-call separately.

## Three hypotheses

Distinguishes three hypotheses: **H1** — a first-write race with lazy initialization (reads survive,
only the write dies; mitigated/shifted with `--delay`); **H2** — a zombie instance from a previous
session (the environment snapshot shows more than one `kicad.exe`, or stale `KICAD_API_SOCKET`/
`KICAD_API_TOKEN` are still set); **H3** — the crash isn't tied to the write at all, it already dies on
read.

## Usage

```bash
# Full ladder: reads + no-op write (step 9) — may crash KiCad, that's the point of the test
python -m kicadstamp.diagnostics.diagnose_first_write_crash

# Reads only (steps 1-8), no write — safe if KiCad is already open
python -m kicadstamp.diagnostics.diagnose_first_write_crash --until 8

# Pause before the write — tests hypothesis H1 (race)
python -m kicadstamp.diagnostics.diagnose_first_write_crash --delay 30

# Repeat the no-op write 3 times in a row — check whether the write stays stable after the first success
python -m kicadstamp.diagnostics.diagnose_first_write_crash --repeat 3

# Custom log path and IPC timeout (default log is diag_<timestamp>.log, default timeout 15000 ms)
python -m kicadstamp.diagnostics.diagnose_first_write_crash --log diag.log --timeout-ms 20000
```

## Parameters

- `--until` – run steps up to and including N (default `9`; `8` = reads only).
- `--delay` – pause in seconds before the first write (tests hypothesis H1).
- `--repeat` – how many times to repeat the no-op write (default `1`).
- `--log` – path to the log file (default `diag_<timestamp>.log` in the current directory).
- `--timeout-ms` – IPC timeout (default `15000`).

## Output

A line-by-line log with an OK/FAIL verdict and timing for each step, an environment snapshot
(`kicad.exe` PIDs, `KICAD_API_SOCKET`/`KICAD_API_TOKEN`, candidate API sockets) before and after the
ladder, and a final summary across all steps.

## Dependencies

`kipy` directly (not through `kicadstamp.kicad.adapter`). The `kicad.exe` PID snapshot uses `tasklist`
on Windows, and the optional `psutil` on other OSes (not in `requirements.txt`; without it, zombie-
instance detection — hypothesis H2 — silently turns off, but the read/write ladder itself works as
usual).

## Caution

Doesn't mutate the board (the write is a no-op), but on a vulnerable session (see issue #24966) the
write attempt itself can **crash the KiCad process entirely**. Save any open files before running the
full ladder (i.e. without `--until 8`).

The crash needs the **Schematic Editor** to be open at the moment of the session's first write — with
only the PCB Editor open, the same ladder completes cleanly. It's specifically the *first* write that's
vulnerable: run the ladder (or a real `apply`) once with only the PCB Editor open first, and opening the
Schematic Editor afterwards is usually safe. See
[crash_hunting.md](crash_hunting.md#24966--ipc-api-crash-on-the-first-write-of-a-session) for the full
writeup of this condition.

## See also

- [crash_hunting.md](crash_hunting.md) — descriptions of both bugs (#24966/#24970), `clean_kicad_crash_state.py`,
  and the recommended workflow. `repeat_first_write_crash.py` itself has moved to its own project,
  [`KiCadTestIPCrash`](https://github.com/GrandFatherPikhto/KiCadTestIPCrash).
- [crash_hunting.md#how-to-catch-and-analyze-a-core-dump-windows--linux](crash_hunting.md) — what to do
  if the ladder does crash KiCad and you need a full symbolized backtrace.
