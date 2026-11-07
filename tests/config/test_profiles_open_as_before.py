#!/usr/bin/env python3
"""The PROFILE GUARD of К8а (plan_2026_11_07_tree_instances_dialog_and_subtree,
acceptance §10): every real profile under `profiles/` must open exactly as it did
before К8.

Why it exists: К8's converter judged the `cluster:` axis by the SHAPE of the
template ("does it have mount nodes?") and refused `3ch-awg-tia-v103` — the one
live profile that carries the axis — although all five mount anchors there
already held the very cluster the axis would have written. A profile that stops
opening is worse than a key that stays: this cell is the standing proof that the
value-based criterion (§10 п.1–3) does not repeat it.

How it works, and what it deliberately does NOT do:

  * each profile's ROOT config is discovered the way the product does
    (`<dir>/<dir>.sexp`, else `<dir>/config.sexp`);
  * the CONFIG GRAPH (the root plus everything its `include:` entries name,
    transitively) is copied to `tmp_path` and the COPY is loaded — the lift
    writes, so loading the original would rewrite Denis's profiles;
  * the board and the schematics are NOT copied: `load_config` never reads the
    board, and a missing sheet map only changes warnings, never the question
    "does this file open";
  * the ORIGINAL files are hashed before and after, so a stray write is caught
    here rather than discovered later.

`profiles/` is gitignored, so in a checkout without it the cell SKIPS — loudly,
with the reason, never silently green.
"""
import hashlib
import re
import shutil
from pathlib import Path

import pytest

from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import sexp_to_dict
from kicadstamp.exceptions import ValidationError

_REPO = Path(__file__).resolve().parents[2]
PROFILES = _REPO / "profiles"

_INCLUDE_RE = re.compile(r'\(\s*include\s+"([^"]+)"')

# The profiles that did NOT open BEFORE К8 either, with the text their refusal
# carried then. `tests` is the old `chains:` retirement (plan_2026_10_08_remove_
# spokes) — nothing to do with К8. Anything else failing is a REGRESSION.
_REFUSED_BEFORE_K8 = {"tests": "chains"}


def _root_config(profile_dir: Path) -> Path | None:
    for name in (f"{profile_dir.name}.sexp", "config.sexp"):
        candidate = profile_dir / name
        if candidate.is_file():
            return candidate
    return None


def _graph_files(root: Path) -> list[Path]:
    """The root config and every file its `include:` entries name, transitively
    — read as TEXT on purpose: the guard must be able to name the files even for
    a profile the loader refuses (that is the case it watches for)."""
    seen: list[Path] = []
    pending = [root]
    while pending:
        path = pending.pop()
        if path in seen or not path.is_file():
            continue
        seen.append(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        for target in _INCLUDE_RE.findall(text):
            pending.append((path.parent / target).resolve())
    return seen


def _copy_graph(profile_dir: Path, root: Path, dest_dir: Path) -> Path:
    """Copy the config graph into `dest_dir`, keeping every file's path RELATIVE
    to the profile dir — an `include:` with a subdirectory keeps working."""
    for source in _graph_files(root):
        relative = source.relative_to(profile_dir.resolve())
        target = dest_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return dest_dir / root.resolve().relative_to(profile_dir.resolve())


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _discovered() -> list[tuple[Path, Path]]:
    """(profile dir, its root config) for every profile that has one."""
    if not PROFILES.is_dir():
        return []
    found = []
    for profile_dir in sorted(p for p in PROFILES.iterdir() if p.is_dir()):
        root = _root_config(profile_dir)
        if root is not None:
            found.append((profile_dir, root))
    return found


pytestmark = pytest.mark.skipif(
    not PROFILES.is_dir(),
    reason="profiles/ is gitignored — the profile guard has no profiles to open")


def test_every_profile_opens_the_way_it_did_before_k8(tmp_path):
    """The guard itself: each profile opens, or fails for the reason it failed
    BEFORE К8. The message lists every profile that broke, so a regression is
    named instead of counted."""
    discovered = _discovered()
    assert discovered, "no profile with a root config was found — the guard would pass vacuously"

    broken: list[str] = []
    for profile_dir, root in discovered:
        copy = _copy_graph(profile_dir, root, tmp_path / profile_dir.name)
        try:
            load_config(str(copy))
        except ValidationError as exc:
            expected = _REFUSED_BEFORE_K8.get(profile_dir.name)
            if expected is None or expected not in str(exc):
                first = " / ".join(line.strip() for line in str(exc).splitlines()
                                   if line.strip())[:300]
                broken.append(f"{profile_dir.name}: {first}")

    assert broken == [], ("these profiles do not open the way they did before К8: "
                          + " | ".join(broken))


def test_the_live_cluster_axis_profile_opens_and_loses_only_the_key(tmp_path):
    """The exact regression of К8, pinned on the real profile: `3ch-awg-tia-v103`
    carries `(cluster "DAC_BUF")` on `ch1_dac_buf`/`ch2_dac_buf`, its mount
    anchors ALREADY say `DAC_BUF`, so the file must open — and after the lift the
    declarations have no key while every entity keeps its own cluster."""
    profile_dir = PROFILES / "3ch-awg-tia-v103"
    root = _root_config(profile_dir)
    if root is None:
        pytest.skip("the live profile with the cluster axis is not here")

    original_digest = _digest(root)
    copy = _copy_graph(profile_dir, root, tmp_path / profile_dir.name)

    cfg, _ctx = load_config(str(copy))

    lifted = sexp_to_dict(copy.read_text(encoding="utf-8"))["tree_instances"]
    assert len(lifted) >= 2
    assert all("cluster" not in row for row in lifted)
    # the KEY is gone; the copies keep the cluster of the RECORD they copy
    originals = {e.name: e.cluster for e in cfg.entities}
    copies = {e.name: e for e in cfg.entities if e.name.endswith("__ch1_dac_buf")}
    assert copies, "the ch1_dac_buf instance must still materialize"
    for name, copy_entity in copies.items():
        record = name[: -len("__ch1_dac_buf")]
        assert copy_entity.cluster == originals[record], name
    # the lift happened on the COPY, and a backup lives next to the copy only
    assert list(copy.parent.glob(f"{copy.name}.bak.*"))
    assert _digest(root) == original_digest, "the real profile was modified by the guard"


def test_the_guard_does_not_touch_a_single_original_file(tmp_path):
    """Second half of the same promise: after opening every profile's COPY, no
    original file in any profile's graph has changed."""
    discovered = _discovered()
    if not discovered:
        pytest.skip("no profiles")
    before = {(root, _digest(root)) for _profile, root in discovered}
    for profile_dir, root in discovered:
        _copy_graph(profile_dir, root, tmp_path / profile_dir.name)
        try:
            load_config(str(tmp_path / profile_dir.name / root.name))
        except ValidationError:
            pass                       # the refusal is the previous cell's business
    after = {(root, _digest(root)) for _profile, root in discovered}
    assert after == before
