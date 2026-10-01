# tests/repo/test_write_later_helper.py
"""Ф3.7 of plan_2026_09_27_repo_and_tests_transformation: the property of
`tests/fakes/write_later.py` itself.

The helper carries ELEVEN cells (and `test_phase3_wiring`'s `_write`): on Linux
they all pass with or without it, so nothing in them notices if `write_later`
quietly stops being a LATER write. These cells are what notices — without them,
the helper could degrade to a bare `write_text` and the whole Windows-tick class
would come back with every rig still green on the machine the rigs are developed
on.

The `os.utime` branch cannot be reached naturally on Linux (two writes an hour
apart do not collide), so it is FORCED the only way a rig can: the file is given
a stamp in the future, which no plain write can beat. That is the same trick the
Ф3.6 cell for the format-number probe uses (`os.utime` on the spot) — a rig
manufacturing the condition it measures, not a weakened assertion.
"""
import os

from tests.fakes.write_later import write_later


def test_write_later_moves_the_stamp_past_the_previous_one_when_the_write_cannot(tmp_path):
    """The forced branch: the previous stamp is in the FUTURE, so the write that
    follows cannot be later — and only the helper's correction can make the file
    look like a later write."""
    target = tmp_path / "root.sexp"
    target.write_text("first", encoding="utf-8")
    future_ns = os.stat(target).st_mtime_ns + 3_600_000_000_000  # one hour, ns
    os.utime(target, ns=(os.stat(target).st_atime_ns, future_ns))

    result = write_later(target, "second")

    assert target.read_text(encoding="utf-8") == "second", "the bytes must land"
    assert result > future_ns, (
        f"write_later must leave a LATER stamp than the one it found: "
        f"got {result}, future stamp was {future_ns}")
    assert result == future_ns + 1_000_000, (
        f"one millisecond past the PREVIOUS stamp is the whole convention: got "
        f"{result} instead of {future_ns + 1_000_000}")
    assert os.stat(target).st_mtime_ns == result, (
        "the returned value must be what the file REALLY carries (the helper "
        "re-stats after os.utime for exactly this reason)")


def test_write_later_on_a_missing_file_is_a_plain_write(tmp_path):
    """No previous stamp means nothing to correct: the file keeps the stamp the
    filesystem gave it, and the helper must not invent one."""
    target = tmp_path / "brand_new.sexp"

    result = write_later(target, "body")

    assert target.read_text(encoding="utf-8") == "body"
    assert result == os.stat(target).st_mtime_ns, (
        "a file that did not exist has no previous stamp to move past")
