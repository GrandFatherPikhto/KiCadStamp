# KiCad crash hunting toolkit (issue #24966 / #24970)

## Purpose

This document describes the toolkit KiCadStamp uses to catch and localize two distinct, confirmed KiCad 10
crashes (Windows and Linux/Flatpak), and the recommended order to run them in. One of the two (#24970) is
fixed upstream as of 2026-08-03 — see its own section below — the toolkit itself stays relevant for #24966
and any future hunt. The tools live in two places:
`kicadstamp/diagnostics/` (detailed step-by-step diagnosis via `python -m`) and `tools/` (operational scripts —
state cleanup and crash-rate statistics across repeated runs). It also covers, at the end, how to capture and
symbolize an actual core dump on either OS once one of the tools has reproduced a crash.

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

### 3. `tools/repeat_first_write_crash.py` — repeated runs and crash rate

Bug #24966 is intermittent (the original report itself notes: "on a freshly rebooted Windows it reproduces
reliably; on a warmed-up system it becomes intermittent") — a single run (crash / no crash) proves nothing
statistically. This script runs N identical iterations in a row and reports the crash percentage, turning an
anecdote into a measured rate.

**Cross-platform (Windows/Linux)** — was Linux/Flatpak-only until 2026-07-27; `kill_kicad()`/`launch_kicad()`
now branch on `os.name`, same convention as `list_kicad_pids()` in `diagnose_first_write_crash.py`.

Per-iteration cycle:

1. Kill KiCad if running — `taskkill /IM kicad.exe /F` on Windows, `pkill -x kicad` on Linux — then **wait
   until it actually disappears** (`wait_until_dead()`, tasklist on Windows / psutil elsewhere, up to ~25s).
   Since 2026-08-22 a forced kill is no longer trusted blindly: a hung `kicad.exe` on this machine was observed
   lingering for tens of seconds even after `taskkill /F`, and starting the next attempt on top of a survivor
   silently corrupted the reported crash rate. The same check also runs once at session start — a leftover
   zombie that won't die aborts the run instead of polluting it.
2. Clean up leftovers — invokes `tools/clean_kicad_crash_state.py` as a subprocess. **Still Linux/Flatpak-only
   under the hood** (see below) — on Windows this step is a safe no-op (prints a warning, doesn't crash), it
   just doesn't actually clean anything yet.
3. Launch KiCad with the given project — on Linux, `flatpak run org.kicad.KiCad <project>` (app id, no path
   needed); on Windows there's no equivalent app-id launch, so it runs `<kicad_exe> <project>` with an
   explicit, mandatory path from the config/`--kicad-exe` — deliberately not auto-detected across install
   locations/versions (same "explicit beats silent guessing" convention as the rest of the project).
4. Wait for IPC readiness — **not just `ping()`**, but also a successful `get_board()` (otherwise there's a
   race: `ping()` answers as soon as the base API server comes up, well before pcbnew actually loads the board
   and registers its handler — this used to produce false `ApiError: no handler available` results that were
   mistaken for a crash).
5. One no-op transaction (`begin_commit → update_items → push_commit`) — the same production path as step 9
   of `diagnose_first_write_crash.py`. An `ApiError` with code `AS_BUSY` ("KiCad is busy and cannot
   respond...") is **not** the #24966 crash — it's a separate readiness race: `get_board()` (a read) in step
   4 already succeeds before pcbnew is ready to accept **editing** commands, and this window turned out to be
   noticeably wider on native Windows than on Linux/Flatpak, where it didn't show up at all (found
   2026-07-27, chasing a run that reported 100% "crash" on Windows while the same board was a clean 100% OK
   on Linux). Retried here with backoff, the same technique as `commit_with_retry` in production
   (`kicadstamp/kicad/adapter.py`) — not counted as a crash.
6. Record the outcome: `ok` / `crash` (dropped connection — this one is actually #24966) / `busy` (still busy
   after all retries — reported separately, not as a crash) / `zombie` (the previous `kicad.exe` never died
   within `wait_until_dead()`'s timeout — the attempt is skipped, no new instance is launched on top of it;
   reported separately, not as a crash).
7. Kill KiCad, next iteration.

**Config:** by default reads `crash_config.yaml` from the repo root — no need to type `--project`/
`--kicad-exe` by hand every time. Shared fields (`project`, `boards_dir`, `runs`, `startup_wait`,
`settle_delay`) live at the top level, one value for both OSes; the Windows-only `kicad_exe` lives under a
nested `windows:` key, read only when `os.name == "nt"` — Linux has no equivalent field at all (launch is by
Flatpak app id, no path needed). KiCad projects live under `test_boards/`, which is entirely gitignored (test
boards are local/per-machine), but `crash_config.yaml` itself is an ordinary tracked file — it only holds
path strings, not the project itself:

```yaml
# crash_config.yaml
project: test_boards/3CH-AWG-TIA/3CH-AWG-TIA.kicad_pro
boards_dir: test_boards
runs: 10
startup_wait: 30.0
settle_delay: 0.0

windows:
  kicad_exe: "C:\\Users\\<you>\\AppData\\Local\\Programs\\KiCad\\10.0\\bin\\kicad.exe"
```

Any CLI flag overrides the corresponding config field.

```bash
python tools/repeat_first_write_crash.py                       # everything from crash_config.yaml
python tools/repeat_first_write_crash.py --runs 20               # config + override runs

# Testing hypothesis H1 (race) — pause before the transaction once IPC is ready
python tools/repeat_first_write_crash.py --settle-delay 30

# A different config (e.g. for another test board)
python tools/repeat_first_write_crash.py --config crash_config_power_board.yaml

# Windows, kicad_exe outside the config (e.g. a second KiCad version installed side by side)
python tools/repeat_first_write_crash.py --kicad-exe "C:\Program Files\KiCad\9.0\bin\kicad.exe"
```

**Parameters:**
- `--config` — path to the YAML config (default `crash_config.yaml`).
- `--project` — path to the `.kicad_pro` to launch (overrides `project` from the config; errors out if
  neither is set).
- `--runs` — number of iterations (default 10 / from config).
- `--boards-dir` — forwarded to `clean_kicad_crash_state.py` (default `test_boards` / from config).
- `--startup-wait` — timeout waiting for IPC readiness, seconds (default 30 / from config).
- `--settle-delay` — pause after IPC readiness before the transaction, seconds (tests hypothesis H1).
- `--kicad-exe` — path to `kicad.exe` (overrides `kicad_exe` from the config; **mandatory on Windows**,
  fatal with a usage hint if missing there; ignored on Linux).

**Output:** a line of `OK`/`CRASH`/`BUSY`/`ZOMBIE` per attempt, followed by a summary (total runs counted
towards the crash rate, how many never came up in time, how many stayed busy through all retries, how many were
skipped as zombies, crash percentage — the crash percentage is computed over `ok + crash` only, `busy`/timed-out/
zombie runs are excluded so they don't dilute or inflate the #24966 statistic with unrelated noise).

**Important note on the "Schematic Editor open" condition:** whether the schematic editor reopens
automatically on project launch depends on whether the `.kicad_pro` project itself remembered its open
windows from last time. If it doesn't, open the Schematic Editor by hand once and save the project
(File → Save Project) — KiCad will then reopen it automatically on every subsequent cycle start. Without this
step, a run of this script silently tests a **different** scenario (PCB Editor only), where the bug does not
reproduce.

**Dependencies:** on Linux, `flatpak`, `pkill`/`pgrep` on `PATH`; on Windows, nothing beyond the standard
library (`taskkill` ships with Windows) plus a correct `kicad_exe` path. Invokes
`tools/clean_kicad_crash_state.py` as a subprocess (the path is hardcoded relative to the repo root — run
from the repo root) — that script itself stays Linux/Flatpak-only for now (see above).

---

## Recommended workflow

1. **Clean state** — `tools/clean_kicad_crash_state.py`, if the previous session crashed.
2. **One precise run** — `diagnose_first_write_crash.py` without `--until 8`, to see exactly which step it
   dies on this time and under which hypothesis (H1/H2/H3).
3. **A batch of runs** — `repeat_first_write_crash.py --runs N`, to get a crash rate under a specific
   condition (e.g. "Schematic Editor open" vs. "PCB Editor only"), not a single anecdote.
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
