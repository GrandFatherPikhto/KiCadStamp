# kicadstamp/diagnostics/diagnose_first_write_crash.py
"""
diagnose_first_write_crash.py — reproducible ladder for localising KiCad crashes
on the first write after startup.

Hypotheses distinguished by the script (see conversation 2026-07-17):
  H1. Race of the first write with lazy KiCad initialisation:
      reads (steps 1-8) survive, exactly WRITE (step 9) dies,
      and only on a fresh instance; --delay N shifts/heals.
  H2. Zombie instance from previous session: snapshot shows >1
      kicad.exe OR KICAD_API_TOKEN/KICAD_API_SOCKET leftover from previous
      session; after "our" counterpart dies, another PID remains alive.
  H3. Crash not tied to write: dies already on reads.

FIXED (2026-07-26): step 9 previously did a bare board.update_items()
without begin_commit()/push_commit() — and 8/8 runs passed cleanly. The same
day a real `apply` crashed KiCad on Linux/KiCad 10.0.5 exactly on begin_commit()
for Move batch 1 (the first transaction of the session; flips before that went
through a separate API path run_action("...InteractiveEdit.flip"), not through
commit — see kicadstamp/kicad/adapter.py:flip_selected). Step 9 now honestly
repeats the combat path adapter.commit_with_retry(): begin_commit -> update_items
-> push_commit, one transaction, with logging of each sub‑call separately
(so even on breakage it is visible how far it got).

Run (KiCad open, board loaded):
  python -m kicadstamp.diagnostics.diagnose_first_write_crash
  python -m kicadstamp.diagnostics.diagnose_first_write_crash --until 8   # reads only
  python -m kicadstamp.diagnostics.diagnose_first_write_crash --delay 30  # pause before write
  python -m kicadstamp.diagnostics.diagnose_first_write_crash --repeat 3  # repeat write

Each step: time measurement, OK/FAIL verdict, then ping and comparison of
kicad.exe PID list. Log is written TO FILE LINE BY LINE WITH FLUSH — even
if everything dies, the last line honestly says where exactly.
"""

import argparse
import datetime
import logging
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from kicadstamp.i18n import _

logger = logging.getLogger("diagnose")


# ---------------------------------------------------------------- utilities

class FlushingFileHandler(logging.FileHandler):
    """Flush after every record — the log must survive death of anything."""
    def emit(self, record):
        super().emit(record)
        self.flush()


def setup_logging(log_path: Path):
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d [%(levelname)-5s] %(name)s: %(message)s",
                            datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fh = FlushingFileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(ch)
    # pynng provides useful noise: Pipe callback event 2 = pipe break = server death
    logging.getLogger("pynng").setLevel(logging.DEBUG)
    logging.getLogger("kipy").setLevel(logging.DEBUG)


def list_kicad_pids():
    """PID of all kicad.exe (Windows: tasklist; otherwise psutil if available)."""
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
            # Exact image-name match (not a substring) so this diagnostic's
            # own helper scripts and any other kicadstamp-family tool are
            # never mistaken for a live kicad. Zombies are skipped: after a
            # crash kicad lingers in state Z until its parent reaps it, and
            # psutil still reports it by name — counting it would misreport
            # a dead session as alive in the snapshot/pulse.
            pids = [p.pid for p in psutil.process_iter(["name", "status"])
                    if p.info["name"] and p.info["name"].lower() == "kicad"
                    and p.info["status"] != psutil.STATUS_ZOMBIE]
    except Exception as e:
        logger.debug(_("Failed to get kicad PIDs: {e}").format(e=e))
    return sorted(pids)


def list_socket_candidates():
    """
    Candidates for API sockets. On Windows the socket lives under %TEMP%;
    first instance uses api.sock, subsequent ones api-<PID>.sock —
    multiple files = trace of zombie instances.
    """
    found = []
    roots = [Path(tempfile.gettempdir())]
    env_sock = os.environ.get("KICAD_API_SOCKET")
    if env_sock:
        p = env_sock.replace("ipc://", "")
        found.append(_("(from env) {p} exists={exists}").format(p=p, exists=Path(p).exists()))
    for root in roots:
        try:
            for p in root.rglob("api*.sock"):
                st = p.stat()
                age = time.time() - st.st_mtime
                found.append(_("{p} (mtime {age:.1f} min ago)").format(p=p, age=age/60))
        except Exception as e:
            logger.debug(_("scanning {root}: {e}").format(root=root, e=e))
    return found


def snapshot_environment(tag: str):
    logger.info(_("--- environment snapshot [{tag}] ---").format(tag=tag))
    for var in ("KICAD_API_SOCKET", "KICAD_API_TOKEN"):
        val = os.environ.get(var)
        if val:
            logger.info(_("  {var} = {val!r}  <-- LINGERS IN ENVIRONMENT (candidate for stale!)")
                        .format(var=var, val=val))
        else:
            logger.info(_("  {var} = {val!r}").format(var=var, val=val))
    pids = list_kicad_pids()
    if pids:
        if len(pids) > 1:
            logger.info(_("  kicad.exe PID: {pids}  <-- MORE THAN ONE INSTANCE (hypothesis H2: zombie!)")
                        .format(pids=pids))
        else:
            logger.info(_("  kicad.exe PID: {pids}").format(pids=pids))
    else:
        logger.info(_("  kicad.exe PID: not found"))
    for s in list_socket_candidates():
        logger.info(_("  socket: {s}").format(s=s))
    return pids


# ---------------------------------------------------------------- ladder

class Ladder:
    def __init__(self, baseline_pids):
        self.kicad = None
        self.board = None
        self.fp = None
        self.baseline_pids = baseline_pids
        self.results = []  # (number, name, verdict, duration)

    def step(self, num, name, fn, check_pulse=True):
        logger.info(_("===== STEP {num}: {name} =====").format(num=num, name=name))
        t0 = time.perf_counter()
        try:
            out = fn()
            dt = time.perf_counter() - t0
            logger.info(_("step {num} OK in {dt:.3f}s").format(num=num, dt=dt) + (f": {out}" if out else ""))
            self.results.append((num, name, "OK", dt))
            ok = True
        except BaseException as e:
            dt = time.perf_counter() - t0
            logger.error(_("step {num} FAIL in {dt:.3f}s: {type}: {e}")
                         .format(num=num, dt=dt, type=type(e).__name__, e=e))
            logger.debug(traceback.format_exc())
            self.results.append((num, name, f"FAIL: {type(e).__name__}", dt))
            ok = False
        if check_pulse:
            self.pulse(num)
        return ok

    def pulse(self, after_step):
        """Pulse: ping + PID comparison. Catches the fact of KiCad death."""
        pids = list_kicad_pids()
        died = [p for p in self.baseline_pids if p not in pids]
        if died:
            logger.error(_("!!! kicad.exe PID {died} DIED after step {step} !!!")
                         .format(died=died, step=after_step))
        if pids and set(pids) != set(self.baseline_pids):
            logger.warning(_("PID set changed: was {old}, now {new}")
                           .format(old=self.baseline_pids, new=pids))
        if self.kicad is not None:
            try:
                t0 = time.perf_counter()
                self.kicad.ping()
                logger.info(_("pulse after step {step}: ping OK ({dt:.0f} ms), PID {pids}")
                            .format(step=after_step, dt=(time.perf_counter()-t0)*1000, pids=pids))
            except BaseException as e:
                logger.error(_("pulse after step {step}: ping FAIL — {type}: {e}; PID {pids}")
                             .format(step=after_step, type=type(e).__name__, e=e, pids=pids))

    # --- step contents ---

    def s_connect(self, timeout_ms):
        import kipy
        self.kicad = kipy.KiCad(timeout_ms=timeout_ms)
        return _("client created")

    def s_ping(self):
        self.kicad.ping()
        return "pong"

    def s_version(self):
        v = self.kicad.get_version()
        try:
            api_v = self.kicad.get_api_version()
        except Exception as e:
            api_v = f"<{type(e).__name__}>"
        return _("kicad={v}, api={api}").format(v=v, api=api_v)

    def s_documents(self):
        from kipy.proto.common.types import DocumentType
        docs = self.kicad.get_open_documents(DocumentType.DOCTYPE_PCB)
        return _("open PCB documents: {count}").format(count=len(docs))

    def s_board(self):
        self.board = self.kicad.get_board()
        return _("board received: {ok}").format(ok=self.board is not None)

    def s_read_footprints(self):
        fps = list(self.board.get_footprints())
        if not fps:
            raise RuntimeError(_("no footprints on the board — nothing to write"))
        # Candidate for no‑op write: small passive (C*/R*), not FPGA —
        # so even theoretical damage touches the least.
        self.fp = next((f for f in fps
                        if f.reference_field.text.value[:1] in ("C", "R")), fps[0])
        return _("{total} footprints; write candidate: {ref}").format(
            total=len(fps), ref=self.fp.reference_field.text.value)

    def s_deep_read(self):
        fp = self.fp
        from kipy.board_types import Pad
        pads = [i for i in fp.definition.items if isinstance(i, Pad)]
        return _("{ref}: pos=({x:.3f}, {y:.3f}) mm, angle={angle:.1f}, layer={layer}, pads={pads}").format(
            ref=fp.reference_field.text.value,
            x=fp.position.x/1e6, y=fp.position.y/1e6,
            angle=fp.orientation.degrees, layer=fp.layer,
            pads=len(pads))

    def s_noop_write(self):
        """
        SUSPECT: begin_commit() -> update_items([fp]) with no changes
        -> push_commit() — ONE transaction, exactly the path that
        adapter.commit_with_retry() uses in combat (see kicadstamp/kicad/adapter.py).
        Previously there was bare update_items() without begin_commit/push_commit
        — passed cleanly 8/8 times, but the live crash under Linux (2026-07-26)
        occurred exactly on begin_commit(), which that version of the script
        did not touch at all. We log each sub‑call separately (INFO, flushed
        immediately) — if the transaction breaks, the log will show how far it
        got, even without a FAIL verdict on the step itself.
        """
        ref = self.fp.reference_field.text.value
        logger.info(_("sending begin_commit()..."))
        commit = self.board.begin_commit()
        logger.info(_("begin_commit() OK, sending no‑op update_items([{ref}]) inside transaction...").format(ref=ref))
        self.board.update_items([self.fp])
        logger.info(_("update_items() OK, sending push_commit()..."))
        self.board.push_commit(commit, "diagnose_first_write_crash: no-op")
        return _("begin_commit -> no‑op update_items({ref}) -> push_commit completed fully").format(ref=ref)


def main():
    ap = argparse.ArgumentParser(description=_("Diagnose KiCad crash on first write"))
    ap.add_argument("--log", default=None, help=_("path to log file"))
    ap.add_argument("--until", type=int, default=9,
                    help=_("run steps up to N inclusive (8 = reads only)"))
    ap.add_argument("--delay", type=float, default=0.0,
                    help=_("pause (sec) BEFORE first write — test hypothesis H1"))
    ap.add_argument("--repeat", type=int, default=1,
                    help=_("how many times to repeat the no‑op write"))
    ap.add_argument("--timeout-ms", type=int, default=15000,
                    help=_("IPC timeout in ms"))
    args = ap.parse_args()

    log_path = Path(args.log) if args.log else Path(
        f"diag_{datetime.datetime.now():%Y%m%d_%H%M%S}.log")
    setup_logging(log_path)
    logger.info(_("log: {path}").format(path=log_path.resolve()))
    logger.info(_("python {version}; arguments: {args}").format(version=sys.version.split()[0], args=vars(args)))
    try:
        import kipy
        logger.info(_("kipy {version}").format(version=getattr(kipy, '__version__', '?')))
    except ImportError:
        logger.error(_("kipy not installed"))
        return 2

    baseline = snapshot_environment(_("before connection"))
    ladder = Ladder(baseline)

    steps = [
        (1, _("connect (kipy.KiCad)"), lambda: ladder.s_connect(args.timeout_ms)),
        (2, _("ping"), ladder.s_ping),
        (3, _("get_version/get_api_version"), ladder.s_version),
        (4, _("get_open_documents(PCB)"), ladder.s_documents),
        (5, _("get_board"), ladder.s_board),
        (6, _("read footprints"), ladder.s_read_footprints),
        (7, _("deep read of one footprint"), ladder.s_deep_read),
        (8, _("repeat read (stability of reads)"), ladder.s_read_footprints),
    ]

    for num, name, fn in steps:
        if num > args.until:
            break
        if not ladder.step(num, name, fn):
            logger.error(_("ladder broke at step {num} ({name}) — see verdict above")
                         .format(num=num, name=name))
            break
    else:
        if args.until >= 9:
            if args.delay > 0:
                logger.info(_("pausing {delay}s before write (test H1)...").format(delay=args.delay))
                time.sleep(args.delay)
            for i in range(args.repeat):
                tag = f"9.{i+1}" if args.repeat > 1 else "9"
                if not ladder.step(tag, _("NO-OP WRITE: begin_commit -> update_items([fp]) -> push_commit"),
                                   ladder.s_noop_write):
                    break
                time.sleep(0.5)

    snapshot_environment(_("after ladder"))
    logger.info(_("===== RESULT ====="))
    for num, name, verdict, dt in ladder.results:
        logger.info(_("  [{num}] {name}: {verdict} ({dt:.3f}s)")
                    .format(num=num, name=name, verdict=verdict, dt=dt))
    logger.info(_("full log: {path}").format(path=log_path.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())