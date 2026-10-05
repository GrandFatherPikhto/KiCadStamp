# The board door — how GUI code may touch the KiCad board

**Russian version:** [board_door_ru.md](board_door_ru.md)

The GUI talks to KiCad over ONE IPC socket, from a Qt application whose UI thread must never block. The
**board door** is the single way GUI code reaches the board, plus the rules for using it. Every feature
that reads or writes the board follows it — whether or not its task description mentions it.

## 1. What the door is

- **`connection.board`** — a property of `BoardConnection` (`gui/connection.py`). It is the ONLY way GUI
  code gets the board (and its `adapter`). Do not cache the handle in a dock, and do not pass `board` /
  `adapter` down a chain to dodge the getter.
- **The guard lives in that getter.** A read on the UI thread without a *sign* (§3) is a violation.
- **Two refusal modes**, chosen where the guard is armed (`set_ui_thread_predicate(…, refusal=…)`):
  - `raise` — tests and diagnostics harnesses: the read raises `UiThreadBoardReadRefused`;
  - `log` — the production GUI (`kicadstamp/gui_main.py`): one red ERROR line in the Log per call site
    per session, and the read goes on. **`raise` is forbidden in production**: an exception inside a Qt
    slot aborts PyQt6 (measured, `EXIT=134`). That is also why the production guard does not *stop*
    anything — it testifies. The order of means (§2) is kept by people and by the census (§5).

## 2. On the UI thread — the order of means

Use the first one that answers the question:

1. **Presence** — ask the connection: `connection.is_connected`, never `connection.board is not None`.
2. **Data** — answer from `connection.snapshot`, the synchronous cache the polling thread builds. An empty
   snapshot is not a snapshot: write `if snapshot:`, never `is not None` (before the first poll it is `[]`).
   Identify things from the snapshot; ask "where is it NOW" from the live adapter.
3. **Work** — send it to the worker: `start_long_op(connection, widgets, fn, on_success, on_error, *args)`
   (`gui/worker.py`). The worker function takes the board itself, on the worker thread.
4. **Only then** — a signed UI-thread read, with a measurement written next to it:

   ```python
   with ui_thread_board_read(reason="hand the adapter to a worker; measured 0.2 ms"):
       board = connection.board
   ```
   `reason` is required and says WHY the read stays on the UI thread, not what it does.

## 3. The socket has one owner

- Everything on the shared socket goes through `start_long_op`. `LongOpController` sets
  `connection.long_op_active` before the worker starts and clears it after; the polling ticks skip while it
  is set.
- `socket_busy(connection)` (`gui/worker.py`) is the ONLY definition of "busy". A direct call must ask it
  first and refuse or defer (`defer_while_socket_busy`, one retry, no queue) — never silently. The symptom
  of a violation: *"Error receiving reply from KiCad: Operation canceled"*.
- An operation that builds its OWN adapter (apply pipeline, redraw, CLI — `create_board_adapter`) closes
  it on both paths, success and exception (`finally`). Measured: 23 unclosed sockets per click gave a
  six-second hang.
- Worker timeouts come from `worker_timeout_ms(connection)`, never a constant.
- Only the adapter's own methods touch the board: a new kind of read is a new method on the
  `IBoardAdapter` seam (`kicadstamp/kicad/interfaces.py`) plus its implementation, never `adapter._board`.

## 4. A failed board read is not an answer

A read that FAILED (busy socket, KiCad thinking, identity unknown) must not be turned into a "no" that
changes state. Example: a feature whose state depends on a file keyed by the board's path must leave the
state untouched when the path cannot be read — reading "unknown board" as "no such file" would silently
drop a lock or a journal.

## 5. Guards

| guard | catches |
|---|---|
| `tests/repo/test_board_access_door.py` | anyone reaching `adapter._board` (also via `getattr(…, "_board")`) |
| `tests/repo/test_board_door_guard.py` | the getter's guard itself: predicate, sign, both modes, one line per site |
| `tools/door_lint.py` + `tests/repo/test_door_lint.py` | the static census below |

**Static census — `tools/door_lint.py`.** Reads all of `gui/` and sorts every `.board` read into:

- `worker` — inside a function handed to the worker (`fn` of `start_long_op` / `PollWorkerHandle.submit` /
  `<controller>.start`, a lambda or a `partial` passed there, or a helper called ONLY from such functions);
- `signed` — inside `with ui_thread_board_read(...)`;
- `suspect` — anything else: a UI-thread read, or a helper both threads call.

```sh
.venv/bin/python tools/door_lint.py                   # suspects + ratchet check
.venv/bin/python tools/door_lint.py --all             # every read with its bucket
.venv/bin/python tools/door_lint.py --update-baseline # lower the ratchet after a fix
```

The ratchet (`tools/door_lint_baseline.py`) holds today's suspect count per file: a file may not grow, a
new file must have none. It is a census, not a proof — a static tool cannot see which thread really calls a
helper — so a fix lowers the baseline in the same commit.

**A new feature's own guard is written on a REAL `BoardConnection`** with the predicate armed in `raise`
mode (the `ui_thread` fixture in `tests/repo/test_board_door_guard.py`): drive the feature's UI paths and
expect no `UiThreadBoardReadRefused`. A guard on a fake without the door proves nothing.

## 6. Never run an armed GUI by hand in `raise` mode

An exception inside a Qt slot kills PyQt6 (SIGABRT, measured twice). Live sessions run in `log` mode; the
`raise` mode belongs to tests and offscreen harnesses.
