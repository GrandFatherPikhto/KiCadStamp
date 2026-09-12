# tests/gui/test_combo_line_edit_helpers.py
"""The combo-internal-line-edit rule, pinned as CODE and as a TEST.

Background (2026-09-12): ``AnchorOriginWidget._wire_field_changed`` subscribed
to a searchable combo's INTERNAL QLineEdit via ``findChildren(QLineEdit)``.
That line edit is a separate QObject the combo's own ``blockSignals`` never
covered, so a repopulation leaked a ``textChanged`` straight out of
``QComboBox::insertItems`` and deadlocked the GUI on a non-recursive mutex
(plan_2026_09_12_combo_refresh_deadlock.md). The obvious "filter the internal
line edits out everywhere" fix is WRONG: the internal line edit's
``editingFinished`` is the only signal that carries a hand-typed role/cluster
(not in the item list) to a dock — ``currentIndexChanged`` does not fire for
free-typed text at all (measured: 0 emissions,
diagnostics/probe_combo_line_edit_signals.py).

So the rule is expressed as two helpers in gui/docks/_common.py:

  * ``own_line_edits()``   — every QLineEdit except a combo's internal one;
                             safe for ANY signal, including ``textChanged``;
  * ``combo_line_edits()`` — ONLY the internal line edits; connect ONLY
                             user-driven signals (``editingFinished`` /
                             ``returnPressed``), NEVER ``textChanged``.

These tests pin (1) the strict, complete split, (2) the non-editable-combo
case, (3) nesting, (4) a guard that ``findChildren(QLineEdit)`` lives nowhere
else in gui/, and (5) that a free-typed value still reaches a dock's
autostage through the internal line edit — the behaviour we must NOT break.

GUI tests hang without a timeout — run them with one, e.g.
``timeout 300 .venv/bin/python -m pytest tests/gui/test_combo_line_edit_helpers.py -q``.
"""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QComboBox, QLineEdit, QVBoxLayout, QWidget

from gui.docks._common import combo_line_edits, configure_searchable, own_line_edits
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict


# ── 1. the split is strict and complete ─────────────────────────────────


def test_split_is_strict_and_complete(qapp):
    """own_line_edits + combo_line_edits == findChildren(QLineEdit), with an
    empty intersection — the union must equal the original scan exactly, so no
    subscription is added or lost when a call site switches to the helpers."""
    host = QWidget()
    layout = QVBoxLayout(host)
    combo = QComboBox()
    configure_searchable(combo)
    layout.addWidget(combo)
    own = QLineEdit()
    layout.addWidget(own)
    edit = combo.lineEdit()
    assert edit is not None, "searchable combos are editable by design"

    own_list = own_line_edits(host)
    combo_list = combo_line_edits(host)

    assert own_list == [own]
    assert combo_list == [edit]
    assert set(own_list) | set(combo_list) == set(host.findChildren(QLineEdit))
    assert not (set(own_list) & set(combo_list))


def test_split_preserves_findchildren_order(qapp):
    """Both helpers keep findChildren's order, so the ORDER signals are
    connected in does not change when a call site switches to them."""
    host = QWidget()
    layout = QVBoxLayout(host)
    own_a = QLineEdit()
    layout.addWidget(own_a)
    combo = QComboBox()
    configure_searchable(combo)
    layout.addWidget(combo)
    own_b = QLineEdit()
    layout.addWidget(own_b)

    assert own_line_edits(host) == [own_a, own_b]
    assert combo_line_edits(host) == [combo.lineEdit()]


# ── 2. a non-editable combo owns no internal line edit ──────────────────


def test_non_editable_combo_has_no_internal_edit(qapp):
    host = QWidget()
    layout = QVBoxLayout(host)
    combo = QComboBox()
    combo.addItems(["A", "B"])  # NOT editable -> no internal line edit
    layout.addWidget(combo)
    own = QLineEdit()
    layout.addWidget(own)

    assert combo.lineEdit() is None
    assert combo_line_edits(host) == []
    assert own_line_edits(host) == [own], "a sibling field must not be lost"


# ── 3. nesting: findChildren is recursive, the filter must be too ────────


def test_nested_combo_internal_edit_is_recognised(qapp):
    host = QWidget()
    host_layout = QVBoxLayout(host)
    container = QWidget()  # combo is a GRANDCHILD, not a direct child
    container_layout = QVBoxLayout(container)
    combo = QComboBox()
    configure_searchable(combo)
    container_layout.addWidget(combo)
    host_layout.addWidget(container)
    own = QLineEdit()
    host_layout.addWidget(own)

    assert combo_line_edits(host) == [combo.lineEdit()]
    assert own_line_edits(host) == [own]


def test_deeply_nested_combo_internal_edit_is_recognised(qapp):
    host = QWidget()
    host_layout = QVBoxLayout(host)
    outer = QWidget()
    outer_layout = QVBoxLayout(outer)
    inner = QWidget()
    inner_layout = QVBoxLayout(inner)
    combo = QComboBox()
    configure_searchable(combo)
    inner_layout.addWidget(combo)
    outer_layout.addWidget(inner)
    host_layout.addWidget(outer)

    assert combo_line_edits(host) == [combo.lineEdit()]
    assert own_line_edits(host) == []


# ── 4. the guard: findChildren(QLineEdit) lives ONLY in _common.py ───────

_FORBIDDEN_LITERAL = "findChildren(QLineEdit)"
_ALLOWED_RELATIVE = "gui/docks/_common.py"


def test_findchildren_qlineedit_lives_only_in_common():
    """THE point of the whole task: the "never scan QLineEdit by hand" rule
    stops being a comment someone must remember and becomes a test that fails
    the moment a sixth site appears. Every QLineEdit scan in gui/ must go
    through own_line_edits/combo_line_edits (gui/docks/_common.py), where the
    user-driven-vs-model-rebuild signal rule is written down."""
    repo_root = Path(__file__).resolve().parents[2]
    gui_dir = repo_root / "gui"
    offenders = []
    for path in sorted(gui_dir.rglob("*.py")):
        relative = path.relative_to(repo_root).as_posix()
        if relative == _ALLOWED_RELATIVE:
            continue
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            if _FORBIDDEN_LITERAL in line:
                offenders.append(f"{relative}:{lineno}: {line.strip()}")

    assert not offenders, (
        f"{_FORBIDDEN_LITERAL} must appear ONLY in {_ALLOWED_RELATIVE} "
        "(own_line_edits/combo_line_edits). Route every QLineEdit scan "
        "through those helpers instead — a raw scan can subscribe to a "
        "combo's internal line edit, which deadlocks the GUI on a "
        "repopulation (2026-09-12). Offending lines:\n"
        + "\n".join(offenders)
    )


# ── 5. free-typed text still reaches a dock's autostage ─────────────────

def test_free_typed_role_reaches_points_autostage(qapp, main_window, tmp_path):
    """The behaviour this task must NOT break, for the simplest of the five
    docks: a role TYPED by hand (not in the item list) reaches ``_autostage``.

    ``currentIndexChanged`` is silent for free-typed text, so the ONLY carrier
    is the combo's internal line edit's ``editingFinished``. This test is green
    on the pre-refactor code and must stay green afterwards — it pins what we
    are preserving, not what we are changing."""
    from gui.docks.points import PointsDock

    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"points": {}}), encoding="utf-8")
    dock = PointsDock(main_window)
    dock.set_root_path(root)

    # Anchor mode; the role combo is populated with a list that does NOT
    # contain the value we are about to type.
    dock.origin_mode_combo.setCurrentIndex(1)
    combo = dock.anchor_role_edit
    combo.addItems(["FPGA", "MCU"])
    combo.setCurrentText("")  # start empty
    edit = combo.lineEdit()
    assert edit is not None

    edit.selectAll()
    QTest.keyClicks(edit, "FREELY_TYPED_ROLE")
    assert combo.currentText() == "FREELY_TYPED_ROLE"
    assert combo.findText("FREELY_TYPED_ROLE") < 0, "value must not be an item"

    # Name it now (setText does not emit editingFinished, so no autostage yet).
    dock.name_edit.setText("p_free")

    def _points() -> dict:
        return sexp_to_dict(root.read_text(encoding="utf-8")).get("points") or {}

    assert _points() == {}, (
        "typing alone must not stage — currentIndexChanged does not fire for "
        "free-typed text, that is the whole reason the internal line edit is "
        "load-bearing")

    # The commit: the internal line edit's editingFinished.
    edit.editingFinished.emit()

    assert _points()["p_free"]["anchor_role"] == "FREELY_TYPED_ROLE", (
        "a hand-typed role did not reach _autostage — the internal line "
        "edit's subscription is load-bearing and must not be dropped")
