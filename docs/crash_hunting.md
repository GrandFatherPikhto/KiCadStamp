# KiCad crash hunting toolkit (issue #24966 / #24970)

## Purpose

This document describes the toolkit KiCadStamp uses to catch and localize two distinct, confirmed KiCad 10
crashes (Windows and Linux/Flatpak), and the recommended order to run them in. One of the two (#24970) is
fixed upstream as of 2026-08-03 — see its own section below — the toolkit itself stays relevant for #24966
and any future hunt. The tools live in two places:
`kicadstamp/diagnostics/` (detailed step-by-step diagnosis via `python -m`) and `tools/` (operational scripts —
state cleanup). It also covers, at the end, how to capture and symbolize an actual core dump on either OS once
one of the tools has reproduced a crash.

**2026-08-22: the crash-rate stress harness moved out.** The #24966 investigation grew its own toolkit
(a Windows crash-catching daemon, dump triage, an upstream bug report — GitLab
[work item #25322](https://gitlab.com/kicad/code/kicad/-/work_items/25322)) and earned a dedicated repo,
[`KiCadTestIPCrash`](https://github.com/GrandFatherPikhto/KiCadTestIPCrash) (`D:\Projects\Python\KiCadTestIPCrash\`), instead of
staying a side-quest inside KiCadStamp. `repeat_first_write_crash.py`, its test, and `crash_config.yaml` now
live there — see that project's README for the script itself and a much longer list of practical gotchas
around catching this crash reliably (readiness races, false "zombie" hangs, symbol resolution, etc.) than fit
in this doc.

This isn't an abstract future risk — both bugs were caught live on the `3CH-AWG-TIA` production board; see
`techdocs/issues/` (internal, gitignored, full write-ups and dumps).

---

## Two distinct bugs — do not conflate

### #24966 — IPC API crash on the first write of a session

A null dereference in `API_HANDLER_EDITOR::checkForBusy()` (`m_frame == nullptr`), reachable from several
editor-mutating IPC entry points. The symptom on the KiCadStamp side is a dropped connection
(`ConnectionError`), not a polite `ApiError`.

**Exact trigger (refined 2026-07-26):** not any mutating call, but specifically the session's **first
`begin_commit()`/`push_commit()` transaction**. Interactive GUI actions such as flip
(`run_action("pcbnew.InteractiveEdit.flip")`) go through a separate API path that bypasses commit — they do
not "warm up" the vulnerable state, so a series of flips before the first real transaction does not help.

**Empirically confirmed condition (direct observation, reproduced repeatedly):** the crash only occurs if the
**Schematic Editor** is open in the session. If only the PCB Editor is open, the same scenario (clean start →
one no-op transaction) completes without crashing. This matches the precondition documented in the original
GitLab report.

**Practical corollary (direct observation):** the vulnerable window is specifically the session's first write,
not "Schematic Editor open" as a standing condition. If `apply` is first run with only the PCB Editor open (so
the session's first `begin_commit()`/`push_commit()` succeeds cleanly), opening the Schematic Editor
*afterwards* is usually safe — KiCad typically does not crash. This gives a practical workflow: do the first
IPC placement run PCB-Editor-only, then open the Schematic Editor for the rest of the session.

Status: reproduced on KiCad 10.0.4 and 10.0.5, on both Windows and Linux (Flatpak). The findings that "10.0.5
still crashes" and that the exact trigger is `begin_commit()` (not any write) are queued for a follow-up post
to the GitLab issue; we're deliberately accumulating more data before posting (see `techdocs/status/`).

**Measured data point (2026-07-27, native Windows, KiCad 10.0, `repeat_first_write_crash.py` after the
Windows port + `AS_BUSY` fix above):** 10 runs with both Schematic Editor and PCB Editor open — 1/10 crashed
(10%), the rest `ok`. A separate run with only the PCB Editor open — 10/10 `ok`, 0% — a clean confirmation of
the Schematic-Editor precondition above, this time on native Windows rather than Linux/Flatpak.

### #24970 — crash in `LIB_BUFFER::GetDerivedSymbolNames` — **FIXED**

A separate null dereference, unrelated to the IPC path: it fires inside a `SELECTION_TOOL` coroutine while
re-evaluating a conditional context-menu entry (`SYMBOL_EDITOR_CONTROL`) during **interactive** bulk editing
of custom symbol fields (`Role`) across many symbols at once. It was originally attached to the #24966 thread
by mistake — these are two distinct `_eeschema.dll` null derefs at different addresses, later split into
separate tickets. Full write-up: `techdocs/issues/issue_24970_description.md`.

**Status (2026-08-03): fixed upstream.** Closed `Done`/`fix-committed` by Seth Hillbrand, commit
`dbb096e4`, milestone 10.0.6, labeled `needs-cherry-pick` (so it should also land in a stable
backport, not just the 10.0.6 milestone release). This crash only ever fired inside KiCad's own
Symbol Editor UI during a manual interactive bulk-edit — never something KiCadStamp's own IPC calls
triggered — so no code/workaround here needs to change; once a KiCad build with this fix is in use,
this specific crash class is simply gone.

---

## The toolkit

### 1. `kicadstamp/diagnostics/diagnose_first_write_crash.py` — step-by-step ladder (single run)

Diagnoses **exactly where** in the read→write chain the process dies: connect → ping → version →
open_documents → get_board → read footprints → re-read → **no-op transaction**
(`begin_commit()` → `update_items([fp])` → `push_commit()`, the exact path used in production by
`adapter.commit_with_retry()`). Distinguishes three hypotheses (H1 — lazy-init race, H2 — zombie instance from
a previous session, H3 — dies already on read).

Full description, parameters (`--until`, `--delay`, `--repeat`, `--log`, `--timeout-ms`), output and
dependencies — standalone in [docs/diagnose_first_write_crash.md](diagnose_first_write_crash.md), not
duplicated here.

```bash
python -m kicadstamp.diagnostics.diagnose_first_write_crash            # full ladder (may crash KiCad)
python -m kicadstamp.diagnostics.diagnose_first_write_crash --until 8   # reads only, safe
```

### 2. `tools/clean_kicad_crash_state.py` — clean up a crashed session's leftovers

After a segfault, the Flatpak build of KiCad doesn't always clean up its IPC socket/lock and project lock
files — this stops the next launch from starting with a clean slate (the next attempt hits "busy" instead of
an honest reproduction). The script cleans up:

1. A stale Flatpak IPC socket/lock (`~/.var/app/org.kicad.KiCad/cache/tmp/kicad/{api.sock,api.lock}`).
2. Stale entries under `cache/tmp/org.kicad.kicad/instances/`.
3. Project lock files (`*.lck`) under a given boards directory (default `test_boards/`).

**Safety:** checks `pgrep -x kicad` before doing anything and **refuses to run if KiCad is currently
running** — otherwise it could pull the socket out from under a live session. (Note: `-x`, exact process
name, not `-f` — a full-command-line match would match the script's own filename, which also contains
"kicad".)

```bash
python tools/clean_kicad_crash_state.py                          # clean
python tools/clean_kicad_crash_state.py --dry-run                # show what would be removed
python tools/clean_kicad_crash_state.py --boards-dir path/to/boards
```

**Dependencies:** standard library only, plus `pgrep` on `PATH` (Linux). Linux/Flatpak-specific.

### 3. `repeat_first_write_crash.py` — repeated runs and crash rate (relocated)

Moved out entirely — see the note at the top of this document. Full description, parameters, and the current
list of gotchas live in [`KiCadTestIPCrash`](https://github.com/GrandFatherPikhto/KiCadTestIPCrash)'s own
README, not here.

---

## Recommended workflow

1. **Clean state** — `tools/clean_kicad_crash_state.py`, if the previous session crashed.
2. **One precise run** — `diagnose_first_write_crash.py` without `--until 8`, to see exactly which step it
   dies on this time and under which hypothesis (H1/H2/H3).
3. **A batch of runs** — `repeat_first_write_crash.py --runs N` in
   [`KiCadTestIPCrash`](https://github.com/GrandFatherPikhto/KiCadTestIPCrash), to get a crash rate under a specific condition
   (e.g. "Schematic Editor open" vs. "PCB Editor only"), not a single anecdote.
4. **If it crashes and you need a full root-cause analysis** — capture a core dump and get a symbolized
   backtrace, see the [next section](#how-to-catch-and-analyze-a-core-dump-windows--linux).

---

## How to catch and analyze a core dump (Windows + Linux)

KiCadStamp drives a live KiCad instance through its IPC API (`kipy`), so KiCad crashing isn't an abstract
risk — it's something you'll actually have to debug (see `techdocs/issues/` for the history of the two
bugs above). What follows is not general theory — only what actually worked during real crash hunts on
both OSes.

### Windows

Tooling — [HuntProc](https://github.com/GrandFatherPikhto/HuntProc) on top of ProcDump + WER.

1. **Catching the dump**: `ProcDump` in first-chance monitoring mode against `kicad.exe`, or let WER
   write a full dump on crash (see `%LOCALAPPDATA%\CrashDumps` if `LocalDumps` is enabled in the
   registry). HuntProc automates exactly this — it sits and waits, grabbing the dump right after the
   crash.
2. **Symbols** — KiCad's public symbol server:
   `SRV*<local cache>*https://symbols.kicad.org/kicad-stable`.
3. **Analysis** — `cdb`/WinDbg:
   ```
   cdb -z <dump.zip\dump.dmp> -y SRV*C:\symbols*https://symbols.kicad.org/kicad-stable -c "!analyze -v; q"
   ```
   `!analyze -v` gives a verdict (`FAILURE_BUCKET_ID`), the exception address, and a symbolized stack —
   usually enough for a bug report, no manual disassembly needed.

### Linux

Based on a real hunt (KiCad 10.0.5, Flatpak `org.kicad.KiCad`, Ubuntu). Three genuine gotchas below —
all of them actually hit along the way.

#### 0. Find out how KiCad is installed

```bash
flatpak list --all | grep -i kicad   # Flatpak?
dpkg -l | grep -i kicad               # or a native apt package?
```
A system can easily have both installed at once. The rest of this guide covers Flatpak; for an apt
package everything is simpler (symbols via `apt install kicad-dbgsym` or debuginfod, no sandbox
juggling).

#### 1. Symbols

For Flatpak — a separate `.Debug` extension on the same branch. **Important**: it does NOT show up in
a plain `flatpak remote-ls` (Flatpak hides debug extensions from the listing by default) — install it
by exact name, don't rely on `remote-ls | grep debug` finding nothing as proof it's unavailable:

```bash
flatpak install <remote> org.kicad.KiCad.Debug//stable   # remote name is yours, see `flatpak remotes`
```

```bash
# Check what's actually on the remote for kicad (including debug extensions)
flatpak remote-ls <remote> | grep -i kicad
```

If the extension truly doesn't exist (or ~2 GB is too much to install) — fall back to `debuginfod`
(Flathub runs its own server; `gdb` fetches by build-id automatically, nothing to install):
```bash
export DEBUGINFOD_URLS="https://debuginfod.flathub.org/"
```

#### 2. Catching the core itself

The first, free check (always works, no setup needed) — the kernel logs the segfault to the journal
even if no core file ever lands anywhere:
```bash
journalctl -k | grep -i segfault
# ... kernel: kicad[26482]: segfault at 0 ip ... in _eeschema.kiface[...] ...
```
Already gives you an address and module — useful as a quick "yes, it really is crashing" signal, but
without symbols.

For a full core with a backtrace: on Ubuntu, crashes go through **apport** by default
(`cat /proc/sys/kernel/core_pattern` — you'll see `.../apport ...`). Apport often fails to handle a
Flatpak-sandboxed process (the crashing process's namespace differs from what the handler sees) — you
can end up with nothing in `/var/crash/` even when `journalctl -k` honestly showed a segfault. Workaround:
point `core_pattern` at a plain absolute path (the kernel writes it using the crashing process's own
filesystem view, no external pipe handler involved — the sandbox doesn't get in the way as long as the
path is visible to the process, e.g. somewhere under `$HOME`):

```bash
mkdir -p ~/coredumps
echo "$HOME/coredumps/core.%e.%p.%t" | sudo tee /proc/sys/kernel/core_pattern
```

**Critical**: set `ulimit -c unlimited` in the SAME terminal you launch KiCad from — if you launch it
from a desktop icon/gnome-shell instead, the process inherits the systemd session's limits, not your
shell's, and the common default `ulimit -c 0` will silently forbid writing a core at all:

```bash
ulimit -c unlimited
flatpak run org.kicad.KiCad
```

Then reproduce the crash as usual and check `ls -la ~/coredumps/`.

#### 3. Analysis

For a Flatpak app, run gdb INSIDE the same sandbox — binary/library paths then resolve themselves, no
need to manually hunt through `/var/lib/flatpak/app/...`:

```bash
flatpak run --command=gdb org.kicad.KiCad -batch -ex "bt full" -ex quit -c ~/coredumps/<file>
```
For a native (apt) install — plain `gdb /usr/bin/kicad <corefile>`.

#### Do we need a dedicated hunter daemon (a HuntProc equivalent)?

No — catching the crash itself on Linux is synchronous and built into the kernel (`core_pattern` fires
at the moment of the crash, no service needs to be kept running, unlike Windows where WER/ProcDump *is*
a separate service). The one thing that genuinely saves time on frequent repeat hunts is a small
watcher (`inotifywait` on the core directory + auto-running `gdb -batch -ex "bt full"` on any new file),
so step 3 doesn't need to be run by hand every time. For a one-off or rare hunt, that's overengineering
— the manual steps above are enough.

---

## License

These tools are distributed under the MIT license, same as the main project.
