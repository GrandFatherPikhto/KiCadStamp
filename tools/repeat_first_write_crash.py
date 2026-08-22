#!.venv/bin/python
"""
repeat_first_write_crash.py — runs N attempts of "first transaction after a
clean KiCad start" in a row and reports the crash percentage.

WHY: bug #24966 (checkForBusy/m_frame null) is intermittent — the original
report itself notes "on a freshly rebooted Windows it reproduces reliably;
on a warmed-up system it becomes intermittent". A single run (crash / no
crash) proves nothing statistically — neither for the bug itself nor for
testing hypothetical workarounds. Needs a series of identical attempts and
a crash rate, not a single anecdote.

Cross-platform (Windows/Linux). Per-iteration cycle:
  1. Kill KiCad if running — taskkill /IM kicad.exe /F on Windows, pkill -x
     kicad on Linux — then WAIT for the process(es) to actually disappear
     (tasklist on Windows, psutil elsewhere), not just fire the kill and
     trust a flat sleep. A hung kicad.exe can linger for tens of seconds
     even after a forced kill (live evidence 2026-08-22), and starting the
     next instance on top of a survivor silently invalidates the run.
  2. Clean up leftovers — tools/clean_kicad_crash_state.py (currently only
     actually cleans Flatpak/Linux leftovers — safe no-op with a warning on
     Windows, doesn't crash).
  3. Launch KiCad with the given project — on Windows via the explicit
     kicad_exe path (mandatory in the config/--kicad-exe, there is no
     single app id like Flatpak's), on Linux — flatpak run ... <project>.
  4. Wait for IPC readiness (ping + get_board() with retries).
  5. One no-op transaction: begin_commit -> update_items -> push_commit
     (exactly the production path of adapter.commit_with_retry, not a bare
     update_items — see diagnose_first_write_crash.py and the 2026-07-26
     analysis). An ApiError with code AS_BUSY ("KiCad is busy and cannot
     respond...") is NOT the #24966 crash — it's a separate readiness race:
     get_board() (a read) in step 4 already succeeds before pcbnew is ready
     to accept EDITING commands; this race window turned out to be
     noticeably wider on native Windows than on Linux/Flatpak, where it
     didn't show up at all (analysis 2026-07-27). Retried here with
     backoff, the same technique as commit_with_retry in production — not
     counted as a crash.
  6. Record the outcome: ok / crash (dropped connection — this one is
     actually #24966) / busy (still busy after all retries — reported
     separately, not as a crash) / zombie (previous kicad never died after
     the kill, iteration skipped — reported separately too).
  7. Kill KiCad, next iteration.

IMPORTANT: "Schematic Editor open in the session" is the precondition from
issue #24966. Whether it reopens automatically on project launch depends on
whether the PROJECT itself remembered its open windows from last time. If
not, open the Schematic Editor by hand once and save the project (File ->
Save Project) — KiCad will then reopen it automatically on every subsequent
cycle start.

Config (optional): by default reads crash_config.yaml from the repo root —
shared fields (project, boards_dir, runs, startup_wait, settle_delay) at
the top level, kicad_exe under a nested windows: key (read only if os.name
== "nt", mandatory there — no app-id launch on Windows) or under a nested
linux: key (read only if os.name != "nt", optional — a direct executable,
e.g. a nightly AppImage/wrapper script not distributed via Flatpak at all;
takes priority over flatpak_branch when both are set). flatpak_branch also
lives under linux: (Linux only, ignored if kicad_exe is set) — selects the
Flatpak branch, e.g. "beta" for org.kicad.KiCad//beta side by side with the
stable org.kicad.KiCad install; omitted/unset launches plain
org.kicad.KiCad (stable). Any CLI flag overrides the corresponding config
field.

Usage:
  python tools/repeat_first_write_crash.py                      # everything from crash_config.yaml
  python tools/repeat_first_write_crash.py --runs 20             # config + override runs
  python tools/repeat_first_write_crash.py --project <...> --runs 10 --settle-delay 30   # test hypothesis H1
  python tools/repeat_first_write_crash.py --config other_crash_config.yaml
  python tools/repeat_first_write_crash.py --kicad-exe "<path to kicad.exe>"   # Windows, outside the config
  python tools/repeat_first_write_crash.py --flatpak-branch beta # Linux, outside the config
  python tools/repeat_first_write_crash.py --kicad-exe /usr/bin/kicad-nightly # Linux, non-Flatpak build
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

# Legacy console codepages (Windows cp1251/cp866) can't encode every character
# this script prints (Cyrillic text, typographic dashes) — UTF-8 can encode
# any codepoint, so this avoids both mojibake and outright UnicodeEncodeError
# crashes regardless of the terminal (see kicadstamp_cli.py for the same fix).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

FLATPAK_APP_ID = "org.kicad.KiCad"
DEFAULT_CONFIG_PATH = "crash_config.yaml"


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def kill_kicad():
    if os.name == "nt":
        # /IM matches the exact image name kicad.exe, /F forces termination.
        # No-op (non-zero exit, swallowed) if kicad.exe isn't running.
        subprocess.run(["taskkill", "/IM", "kicad.exe", "/F"], capture_output=True)
    else:
        subprocess.run(["pkill", "-x", "kicad"], capture_output=True)


def _list_kicad_pids():
    """PID of every live kicad process: tasklist on Windows, psutil
    elsewhere. Same approach as diagnose_first_write_crash.py's
    list_kicad_pids() — that module is off-limits to edit, so this private
    copy lives here instead of being imported. A failed enumeration returns
    [] (treated as "no processes"), mirroring the diagnostics code."""
    pids = []
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq kicad.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            for line in out.splitlines():
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) >= 2 and parts[0].lower() == "kicad.exe":
                    pids.append(int(parts[1]))
        else:
            import psutil  # optional
            pids = [p.pid for p in psutil.process_iter(["name"])
                    if p.info["name"] and "kicad" in p.info["name"].lower()]
    except Exception as e:
        print(f"[предупреждение] не удалось получить список kicad-процессов: {e}")
    return sorted(pids)


def wait_until_dead(timeout_s: float = 25.0, poll_s: float = 0.5, context: str = "") -> bool:
    """Wait until no kicad process is alive, polling _list_kicad_pids()
    instead of firing taskkill/pkill and trusting a flat sleep. Returns True
    if every process is gone before the timeout, False if at least one
    survived (a "zombie" candidate). Prints progress while waiting rather
    than sleeping silently — a hung kicad.exe on Windows can take tens of
    seconds to die even after a forced kill (live evidence 2026-08-22), and
    an operator should see that this is expected, not the script hanging."""
    deadline = time.monotonic() + timeout_s
    label = f"[{context}] " if context else ""
    while True:
        pids = _list_kicad_pids()
        if not pids:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        print(f"{label}ожидание завершения kicad.exe PID {pids}, осталось ~{remaining:.0f}с")
        time.sleep(min(poll_s, remaining))
    return not _list_kicad_pids()


def clean_state(boards_dir: Path):
    subprocess.run(
        [sys.executable, "tools/clean_kicad_crash_state.py", "--boards-dir", str(boards_dir)],
        capture_output=True,
    )


def launch_kicad(project_path: str, kicad_exe: str = None, flatpak_branch: str = None):
    """Windows: there is no app-id launch, so the actual kicad.exe path is
    required explicitly (kicad_exe in the config or --kicad-exe) — no silent
    guessing across install locations/versions, same "explicit beats
    guessing" convention as the rest of the project (e.g.
    --net-template-role). Linux: by default launched by Flatpak app id, no
    path needed — optionally a specific branch (flatpak_branch, e.g. "beta")
    to pick between multiple Flatpak installs of org.kicad.KiCad side by
    side (stable vs beta); unset launches plain org.kicad.KiCad (stable).
    If kicad_exe is also given on Linux (e.g. a nightly AppImage/wrapper
    script not distributed via Flatpak at all — flatpak_branch only ever
    offers stable/beta, never nightly), it takes priority over
    flatpak_branch and is launched directly, exactly like Windows."""
    if os.name == "nt":
        if not kicad_exe:
            sys.exit("[error] Windows requires kicad_exe (path to kicad.exe) in the "
                      "config or via --kicad-exe, e.g.:\n"
                      r'  kicad_exe: "C:\Users\<you>\AppData\Local\Programs\KiCad\10.0\bin\kicad.exe"'
                      "\n  (or wherever your KiCad 10 install actually lives — there can be "
                      "more than one KiCad version installed side by side, double-check "
                      "which one you mean)")
        subprocess.Popen(
            [kicad_exe, project_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    elif kicad_exe:
        subprocess.Popen(
            [kicad_exe, project_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        app_ref = f"{FLATPAK_APP_ID}//{flatpak_branch}" if flatpak_branch else FLATPAK_APP_ID
        subprocess.Popen(
            ["flatpak", "run", app_ref, project_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def wait_for_ipc(timeout_s: float) -> bool:
    """
    IMPORTANT: ping() alone isn't enough — it answers as soon as the KiCad
    process itself comes up with the base API server, well BEFORE pcbnew
    actually loads the board and registers its handler. Knocking earlier
    makes get_board() fail with 'ApiError: no handler available for request
    of type ... GetOpenDocuments' — this is NOT bug #24966 (that one drops
    the connection, not a polite ApiError), just a readiness race. So we
    wait for get_board() specifically, not just ping().
    """
    import kipy
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            k = kipy.KiCad(timeout_ms=2000)
            k.ping()
            k.get_board()
            return True
        except Exception:
            time.sleep(1.0)
    return False


def try_commit_once(busy_retries: int = 5, busy_backoff_s: float = 2.0) -> str:
    """Returns "ok" / "crash" / "busy". "busy" (ApiError with code AS_BUSY,
    "KiCad is busy and cannot respond...") is NOT the #24966 crash (that one
    drops the connection, ConnectionError) — it's a separate race:
    get_board() in wait_for_ipc() already succeeds while pcbnew still isn't
    ready to accept EDITING commands (see the 2026-07-27 analysis — this
    window turned out wider on Windows than it had time to show up on
    Linux). Retried with backoff, the same technique as _mutating_call in
    kicadstamp/kicad/adapter.py (production code). If busy never clears
    within all retries — counted as a separate outcome, not mixed into the
    real-crash statistics."""
    import kipy
    from kipy.errors import ApiError, ApiStatusCode
    for attempt in range(1 + busy_retries):
        try:
            k = kipy.KiCad(timeout_ms=15000)
            board = k.get_board()
            fps = list(board.get_footprints())
            if not fps:
                print("  [предупреждение] на плате нет футпринтов — тест невозможен, считаю OK")
                return "ok"
            fp = fps[0]
            commit = board.begin_commit()
            board.update_items([fp])
            board.push_commit(commit, "repeat_first_write_crash: no-op")
            return "ok"
        except ApiError as e:
            if e.code == ApiStatusCode.AS_BUSY and attempt < busy_retries:
                wait = busy_backoff_s * (attempt + 1)
                print(f"  [занято] KiCad ещё не готов принимать запись, "
                      f"жду {wait:.1f}с [{attempt + 1}/{busy_retries}]")
                time.sleep(wait)
                continue
            if e.code == ApiStatusCode.AS_BUSY:
                print(f"  -> так и не отпустило busy за {busy_retries} ретраев: {e}")
                return "busy"
            print(f"  -> упало: {type(e).__name__}: {e}")
            return "crash"
        except Exception as e:
            print(f"  -> упало: {type(e).__name__}: {e}")
            return "crash"


def main():
    ap = argparse.ArgumentParser(description="Серия попыток first-write краша для оценки доли падений")
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                     help=f"Путь к YAML-конфигу (по умолчанию {DEFAULT_CONFIG_PATH})")
    ap.add_argument("--project", default=None, help="Путь к .kicad_pro (переопределяет config)")
    ap.add_argument("--runs", type=int, default=None, help="Сколько итераций (переопределяет config)")
    ap.add_argument("--boards-dir", default=None, help="Для clean_kicad_crash_state.py (переопределяет config)")
    ap.add_argument("--startup-wait", type=float, default=None,
                     help="Таймаут ожидания готовности IPC, с (переопределяет config)")
    ap.add_argument("--settle-delay", type=float, default=None,
                     help="Пауза после готовности IPC перед транзакцией, тест гипотезы H1 (переопределяет config)")
    ap.add_argument("--kicad-exe", default=None,
                     help="Путь к исполняемому файлу KiCad (переопределяет config). "
                          "На Windows обязателен — там нет запуска по app id. На Linux "
                          "опционален: если задан — запускается напрямую (например, "
                          "nightly-обёртка/AppImage, которых нет во Flatpak), в обход "
                          "Flatpak/--flatpak-branch; не задан -> обычный flatpak run")
    ap.add_argument("--flatpak-branch", default=None,
                     help="Flatpak-ветка org.kicad.KiCad, например beta (переопределяет config; "
                          "только Linux, на Windows игнорируется). Не задано -> обычный "
                          "org.kicad.KiCad (stable)")
    args = ap.parse_args()

    config = load_config(args.config)

    project = args.project or config.get("project")
    if not project:
        ap.error(f"--project не задан ни в CLI, ни в {args.config}")
    runs = args.runs if args.runs is not None else config.get("runs", 10)
    boards_dir = args.boards_dir or config.get("boards_dir", "test_boards")
    startup_wait = args.startup_wait if args.startup_wait is not None else config.get("startup_wait", 30.0)
    settle_delay = args.settle_delay if args.settle_delay is not None else config.get("settle_delay", 0.0)
    if os.name == "nt":
        kicad_exe = args.kicad_exe or config.get("windows", {}).get("kicad_exe")
    else:
        kicad_exe = args.kicad_exe or config.get("linux", {}).get("kicad_exe")
    flatpak_branch = args.flatpak_branch or config.get("linux", {}).get("flatpak_branch")

    # Clean session start: kill whatever kicad is still alive (leftover from a
    # previous run of this script, or left open by hand) and CONFIRM it actually
    # died before trusting any iteration — a survivor would silently invalidate
    # every "clean, fresh instance" attempt below (live evidence 2026-08-22). If
    # it won't die, there is no way to guarantee a clean session, so abort rather
    # than launch on top of a zombie.
    kill_kicad()
    if not wait_until_dead(context="начало сессии"):
        sys.exit("[зомби] kicad.exe всё ещё жив после kill и ожидания — не могу "
                 "гарантировать чистую сессию. Завершите процесс вручную "
                 "(Диспетчер задач / taskkill /PID <pid> /F) и запустите скрипт заново.")

    results = []
    for i in range(1, runs + 1):
        print(f"=== попытка {i}/{runs} ===")
        kill_kicad()
        if not wait_until_dead(context=f"попытка {i}"):
            print("  -> [зомби] kicad.exe не завершился за отведённое время — "
                  "пропускаю попытку, не запускаю новый экземпляр поверх старого")
            results.append("zombie")
            continue
        clean_state(Path(boards_dir))
        launch_kicad(project, kicad_exe, flatpak_branch)

        if not wait_for_ipc(startup_wait):
            print("  -> KiCad не поднялся за отведённое время, пропуск")
            results.append("timeout")
            continue

        if settle_delay > 0:
            time.sleep(settle_delay)

        outcome = try_commit_once()
        results.append(outcome)
        print(f"  -> {outcome.upper()}")

    kill_kicad()

    # "busy" (never cleared within all retries), "timeout" (never came up at
    # all) and "zombie" (previous instance never died, iteration skipped) are
    # NOT #24966 crashes — separate outcomes, kept out of the crash rate so
    # they don't add noise to it.
    ok = results.count("ok")
    crashes = results.count("crash")
    busy = results.count("busy")
    timeout = results.count("timeout")
    zombie = results.count("zombie")
    total = ok + crashes
    print()
    print("===== ИТОГ =====")
    print(f"Прогонов: {total} (не поднялся: {timeout}, не отпустило busy: {busy}, "
          f"зомби: {zombie})")
    if total:
        print(f"Падений: {crashes} ({crashes / total * 100:.0f}%)")
    else:
        print("Падений: н/д (ни один прогон не завершился ok/crash)")


if __name__ == "__main__":
    main()
