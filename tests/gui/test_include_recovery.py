# tests/gui/test_include_recovery.py
"""Tests for gui/include_recovery.py — the missing-include repair offered
when the GUI opens a project (plan_2026_09_12_three_old_tails, Э1.2/Э1.3).

The dialog itself is never exec'd here: recover_missing_include's `ask`
callback is injected (or the module function is monkeypatched) so all three
outcomes run without a live modal, and the retry-count contract (exactly one)
is asserted directly."""
import gui.include_recovery as recovery_mod
import pytest
from PyQt6.QtWidgets import QMessageBox  # noqa: F401  (kept for sibling-test parity)

from gui.docks.root_metadata import RootMetadataDock
from kicadstamp.config.includes import walk_include_tree
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import MissingIncludeError, ValidationError


def _err(tmp_path, entry="gone.sexp", source="root.sexp") -> MissingIncludeError:
    return MissingIncludeError(
        "fatal text",
        missing_path=(tmp_path / entry).resolve(),
        include_entry=entry,
        source_path=(tmp_path / source).resolve(),
    )


def _force_choice(monkeypatch, choice):
    """Make walk_with_recovery's recovery round pick `choice` — without a
    modal. Patches the module global walk_with_recovery looks up; the ORIGINAL
    is captured first so the replacement can still delegate to it."""
    original = recovery_mod.recover_missing_include

    def fake(parent, error, **kwargs):
        return original(parent, error, ask=lambda p, e: choice)

    monkeypatch.setattr(recovery_mod, "recover_missing_include", fake)


# ── default action rule (Scheme List side file) ────────────────────────────

def test_default_choice_is_create_for_the_scheme_list_storage():
    assert recovery_mod._default_choice("scheme_lists.sexp") == recovery_mod.CREATE
    assert recovery_mod._default_choice("scheme_lists.json") == recovery_mod.CREATE
    # path form as recorded: the BASENAME is what matters
    assert recovery_mod._default_choice("sub/scheme_lists.sexp") == recovery_mod.CREATE


def test_default_choice_is_cancel_for_any_other_name():
    assert recovery_mod._default_choice("subsystem.sexp") == recovery_mod.CANCEL
    assert recovery_mod._default_choice("cells.json") == recovery_mod.CANCEL


# ── retry contract: exactly one round, no loop ─────────────────────────────

def test_walk_with_recovery_retries_once_then_returns(monkeypatch, tmp_path):
    err = _err(tmp_path)
    calls = {"n": 0}

    def fake_walk(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise err
        return "tree"

    monkeypatch.setattr(recovery_mod, "walk_include_tree", fake_walk)
    monkeypatch.setattr(recovery_mod, "recover_missing_include",
                        lambda parent, error, **kw: True)

    assert recovery_mod.walk_with_recovery(None, "root.sexp") == "tree"
    assert calls["n"] == 2  # initial walk + exactly one retry


def test_walk_with_recovery_second_failure_is_not_offered_again(monkeypatch, tmp_path):
    err = _err(tmp_path)
    walks = {"n": 0}
    recoveries = {"n": 0}

    def fake_walk(path):
        walks["n"] += 1
        raise err

    def fake_recover(parent, error, **kw):
        recoveries["n"] += 1
        return True

    monkeypatch.setattr(recovery_mod, "walk_include_tree", fake_walk)
    monkeypatch.setattr(recovery_mod, "recover_missing_include", fake_recover)

    with pytest.raises(MissingIncludeError):
        recovery_mod.walk_with_recovery(None, "root.sexp")

    assert walks["n"] == 2        # initial + one retry, never a third
    assert recoveries["n"] == 1   # no second dialog


def test_walk_with_recovery_cancel_reraises_without_retry(monkeypatch, tmp_path):
    err = _err(tmp_path)
    calls = {"n": 0}

    def fake_walk(path):
        calls["n"] += 1
        raise err

    monkeypatch.setattr(recovery_mod, "walk_include_tree", fake_walk)
    monkeypatch.setattr(recovery_mod, "recover_missing_include",
                        lambda parent, error, **kw: False)

    with pytest.raises(MissingIncludeError):
        recovery_mod.walk_with_recovery(None, "root.sexp")
    assert calls["n"] == 1


def test_recover_missing_include_ignores_other_errors():
    """Only the structured error is actionable — anything else can't be
    repaired here and must never pop the dialog."""
    assert recovery_mod.recover_missing_include(None, ValidationError("x")) is False


# ── the two physical repairs ───────────────────────────────────────────────

def test_create_empty_writes_an_empty_sexp_config(tmp_path):
    err = _err(tmp_path, entry="scheme_lists.sexp")

    assert recovery_mod._create_empty(err) is True

    path = tmp_path / "scheme_lists.sexp"
    assert path.exists()
    assert sexp_to_dict(path.read_text(encoding="utf-8")) == {}
    assert path.read_text(encoding="utf-8").startswith("(kicadstamp-config")


def test_create_empty_returns_false_when_parent_dir_is_missing(tmp_path):
    """A missing PARENT directory is a wrong path, not a directory to invent —
    the repair fails (caller then treats it as Cancel)."""
    err = _err(tmp_path, entry="nope/deep/file.sexp")
    assert recovery_mod._create_empty(err) is False


def test_remove_include_line_drops_the_matching_entry(tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"include": ["keep.sexp", "gone.sexp"]}),
                    encoding="utf-8")
    keep = tmp_path / "keep.sexp"
    keep.write_text(dict_to_sexp({}), encoding="utf-8")
    err = _err(tmp_path, entry="gone.sexp", source="root.sexp")

    assert recovery_mod._remove_include_line(err) is True

    data = sexp_to_dict(root.read_text(encoding="utf-8"))
    assert data["include"] == ["keep.sexp"]
    # the graph is loadable again
    node = walk_include_tree(str(root))
    assert [c.path.resolve() for c in node.children] == [keep.resolve()]


def test_remove_include_line_drops_the_key_when_last_entry(tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"include": ["gone.sexp"]}), encoding="utf-8")
    err = _err(tmp_path, entry="gone.sexp", source="root.sexp")

    assert recovery_mod._remove_include_line(err) is True

    assert "include" not in sexp_to_dict(root.read_text(encoding="utf-8"))


def test_remove_include_line_no_match_returns_false(tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"include": ["keep.sexp"]}), encoding="utf-8")
    (tmp_path / "keep.sexp").write_text(dict_to_sexp({}), encoding="utf-8")
    err = _err(tmp_path, entry="gone.sexp", source="root.sexp")

    assert recovery_mod._remove_include_line(err) is False
    assert sexp_to_dict(root.read_text(encoding="utf-8"))["include"] == ["keep.sexp"]


# ── end to end through RootMetadataDock.set_root_file ──────────────────────

def test_set_root_file_repairs_by_creating_the_empty_file(
        main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"include": ["scheme_lists.sexp"]}),
                    encoding="utf-8")
    missing = tmp_path / "scheme_lists.sexp"
    assert not missing.exists()
    _force_choice(monkeypatch, recovery_mod.CREATE)

    dock = RootMetadataDock(main_window)
    dock.set_root_file(root)

    assert missing.exists()
    assert sexp_to_dict(missing.read_text(encoding="utf-8")) == {}
    assert dock._path == root
    # the graph loads now
    assert walk_include_tree(str(root)).path == root.resolve()


def test_set_root_file_repairs_by_removing_the_include_line(
        main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"include": ["keep.sexp", "gone.sexp"]}),
                    encoding="utf-8")
    keep = tmp_path / "keep.sexp"
    keep.write_text(dict_to_sexp({}), encoding="utf-8")
    _force_choice(monkeypatch, recovery_mod.REMOVE)

    dock = RootMetadataDock(main_window)
    dock.set_root_file(root)

    data = sexp_to_dict(root.read_text(encoding="utf-8"))
    assert data["include"] == ["keep.sexp"]
    assert dock._path == root


def test_set_root_file_cancel_keeps_the_broken_line(
        main_window, tmp_path, monkeypatch, caplog):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"include": ["gone.sexp"]}), encoding="utf-8")
    _force_choice(monkeypatch, recovery_mod.CANCEL)

    dock = RootMetadataDock(main_window)
    dock.set_root_file(root)

    assert dock._path == root  # root stays set, window stays open
    assert not (tmp_path / "gone.sexp").exists()
    assert sexp_to_dict(root.read_text(encoding="utf-8"))["include"] == ["gone.sexp"]
    assert any("could not load root config" in r.message for r in caplog.records)


def test_set_root_file_offers_recovery_exactly_once(
        main_window, tmp_path, monkeypatch):
    """Two dangling includes: the first repair round fixes include #1, the
    retry then fails on #2 — and that second failure is NOT offered again."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"include": ["first.sexp", "second.sexp"]}),
                    encoding="utf-8")
    attempted = []
    original = recovery_mod.recover_missing_include

    def fake(parent, error, **kwargs):
        attempted.append(error.include_entry)
        return original(parent, error, ask=lambda p, e: recovery_mod.CREATE)

    monkeypatch.setattr(recovery_mod, "recover_missing_include", fake)

    dock = RootMetadataDock(main_window)
    dock.set_root_file(root)

    assert attempted == ["first.sexp"]          # no second dialog
    assert (tmp_path / "first.sexp").exists()   # the one repair landed
    assert not (tmp_path / "second.sexp").exists()
