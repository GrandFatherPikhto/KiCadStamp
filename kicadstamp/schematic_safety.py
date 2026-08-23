# kicadstamp/schematic_safety.py
"""
Pre-write safety guards for the schematic-editing tools (schematic_
set_fields.py/schematic_rename_fields.py, fieldstool_cli.py) — ported from
tools/apply_role_cluster.py (2026-08-01 fold-in). Kept dependency-free from
kipy on purpose (this tool must work with KiCad closed) — psutil is
optional, used only on non-Windows.
"""
import logging
import os
import subprocess
import unicodedata
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# tasklist's console output is in the OS's OEM/console codepage, not UTF-8 —
# cp866 is correct for a Russian-locale Windows console (verified live,
# 2026-08-03: a Cyrillic window title round-tripped correctly). A different
# locale's codepage would only degrade KicadProcessInfo.title into mojibake
# (errors="replace" prevents a crash) — pid/status stay ASCII-safe either
# way, so kill_kicad_process() is unaffected by a wrong guess here.
_TASKLIST_ENCODING = "cp866"


def find_non_ascii(value: str) -> list[tuple[int, str, int, str]]:
    bad = []
    for i, ch in enumerate(value):
        if not (0x20 <= ord(ch) <= 0x7E):
            try:
                name = unicodedata.name(ch)
            except ValueError:
                name = "UNNAMED"
            bad.append((i, ch, ord(ch), name))
    return bad


def list_kicad_pids() -> list[int]:
    """If KiCad is running, a .kicad_sch we're about to splice may be open
    in Eeschema, and an edit made around that live session risks being
    silently overwritten by KiCad's own next save."""
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
            import psutil  # optional, see requirements.txt
            # Exact image-name match (not a substring): found live
            # 2026-08-07 that launching directly as ./kicadstamp_gui.py makes
            # Linux's comm — what psutil's name() reads — the truncated
            # SCRIPT basename, "kicadstamp_gui." for a 15-char comm field,
            # which contains "kicad" just as much as real KiCad's own process
            # name does. Denis picked the one PID the "KiCad processes"
            # dialog offered, force-closed it, and it was this app killing
            # itself. An exact match can never hit our own process (or any
            # other kicadstamp-family tool), so no PID exclusion is needed.
            # Zombies are skipped: after a crash kicad lingers in state Z
            # until its parent reaps it, and psutil still reports it by name.
            pids = [p.pid for p in psutil.process_iter(["name", "status"])
                    if p.info["name"] and p.info["name"].lower() == "kicad"
                    and p.info["status"] != psutil.STATUS_ZOMBIE]
    except Exception as e:
        logger.debug("could not get kicad PIDs: %s", e)
    return sorted(pids)


@dataclass
class KicadProcessInfo:
    pid: int
    status: str | None = None  # "Running"/"Not Responding" — Windows only
    title: str | None = None   # main window title — Windows only, best-effort


def list_kicad_processes() -> list[KicadProcessInfo]:
    """Richer counterpart to list_kicad_pids(), for a human to look at and
    choose from (gui/kicad_processes_dialog.py) — adds responsiveness
    status and window title where the platform can report them (Windows
    only, via tasklist's /V columns; PID-only elsewhere, psutil has no
    portable "is this window responding" concept). NOT used by the
    write-safety gate above, and never feeds an automated decision — see
    kill_kicad_process()'s docstring for why that matters."""
    processes: list[KicadProcessInfo] = []
    try:
        if os.name == "nt":
            raw = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq kicad.exe", "/FO", "CSV", "/V", "/NH"],
                capture_output=True, timeout=10,
            ).stdout
            out = raw.decode(_TASKLIST_ENCODING, errors="replace")
            for line in out.splitlines():
                parts = [p.strip('"') for p in line.split('","')]
                # Columns (tasklist /FO CSV /V /NH, fixed order): Image Name,
                # PID, Session Name, Session#, Mem Usage, Status, User Name,
                # CPU Time, Window Title.
                if len(parts) >= 9 and parts[0].lower() == "kicad.exe":
                    title = parts[8]
                    processes.append(KicadProcessInfo(
                        pid=int(parts[1]), status=parts[5],
                        title=None if title == "N/A" else title))
        else:
            import psutil  # optional, see requirements.txt
            # Same exact-name match plus zombie filter as list_kicad_pids()
            # above — see its comment for the 2026-08-07 self-match incident
            # and why no PID exclusion is needed. This is the function
            # gui/kicad_processes_dialog.py's picker actually lists from, so
            # it's the one that let Force-close target our own process live.
            for p in psutil.process_iter(["pid", "name", "status"]):
                if (p.info["name"] and p.info["name"].lower() == "kicad"
                        and p.info["status"] != psutil.STATUS_ZOMBIE):
                    processes.append(KicadProcessInfo(pid=p.info["pid"]))
    except Exception as e:
        logger.debug("could not get kicad process details: %s", e)
    return sorted(processes, key=lambda p: p.pid)


def kill_kicad_process(pid: int) -> None:
    """Force-terminates one KiCad process by PID.

    Deliberately manual-only: called from gui/kicad_processes_dialog.py
    after a human has picked one specific PID from list_kicad_processes()
    and confirmed a destructive-action dialog — never wired to run on its
    own. This project already decided (see docs/fieldstool.md, and the
    Apply gate above) that closing/killing KiCad is always a user
    instruction: kipy 0.7.1 has no way to check ANY KiCad process for
    unsaved changes, so a "this one looks stuck, kill it" heuristic could
    silently destroy real work in what only LOOKS like a hung session
    (e.g. a genuinely long-running plot/export). This function only ever
    executes a choice a human already made.

    Raises RuntimeError with the OS's own diagnostic on failure (PID
    already gone, access denied, ...).
    """
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True, timeout=10,
            )
            if result.returncode != 0:
                message = (result.stderr or result.stdout).decode(
                    _TASKLIST_ENCODING, errors="replace").strip()
                raise RuntimeError(message or f"taskkill exited with code {result.returncode}")
        else:
            import signal
            os.kill(pid, signal.SIGKILL)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(str(e)) from e
