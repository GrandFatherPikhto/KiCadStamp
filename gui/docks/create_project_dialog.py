# gui/docks/create_project_dialog.py
"""
CreateProjectDialog — File > "Create Project..." / Ctrl+N, KiCad-style.

2026-09-24, Denis: "В File будет «Создать проект». Открываем диалог создания
проекта. Там указываем имя, как в кикад, автоматически создаётся директория с
нужной инфраструктурой."

So the user types a NAME and points at a PARENT folder; the dialog creates
``<parent>/<name>/<name>.sexp`` plus the five infrastructure directories
(``kicadstamp.project_setup.create_project``) and reports the created config
through ``created_config_path``. This REPLACES the old save-mode dialog of "New
Root file...": a config's name is no longer typed at all — it is the project's
name, and the directory named after it is made automatically.

The dialog is MODAL on purpose: the caller cannot open the project before the
dialog's result exists. A collision with an existing project is refused with a
warning box and the dialog stays OPEN, with nothing written — that is a
validation of what the user just typed, which is the class of modal the 2026-09-11
"no modal" rule allows (a connection-STATE error is the class it bans).
"""
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QVBoxLayout)

from kicadstamp.i18n import _
from kicadstamp.project_setup import ProjectConfigExists, create_project
from kicadstamp.utils.paths import project_config_path_for_dir

logger = logging.getLogger(__name__)

# Names that are NOT project names (2026-09-24, ДОПОЛНЕНИЕ 1, Д-1): "." makes the
# target directory EQUAL the picked folder — no project directory is created at all
# — and ".." walks OUT of it. Neither carries a path separator, so the separator
# guard below cannot see them, and both were measured producing real files on disk.
_RELATIVE_DIR_NAMES = (".", "..")


class CreateProjectDialog(QDialog):
    """Name + parent folder -> a created project directory."""

    def __init__(self, parent=None, start_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle(_("Create Project"))
        self.setObjectName("create_project_dialog")
        # What the caller reads after accept(): the config that now exists.
        self.created_config_path: Optional[Path] = None

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("My new project"))
        self.folder_edit = QLineEdit(start_dir)
        browse_button = QPushButton(_("Browse..."))
        browse_button.clicked.connect(self._on_browse)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit)
        folder_row.addWidget(browse_button)

        form = QFormLayout()
        form.addRow(_("Project name:"), self.name_edit)
        form.addRow(_("Folder:"), folder_row)

        # The visible promise of "the directory is created automatically": the
        # user sees the exact path before committing to it.
        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.preview_label)
        layout.addWidget(self.buttons)

        self.name_edit.textChanged.connect(self._update_preview)
        self.folder_edit.textChanged.connect(self._update_preview)
        self._update_preview()

    # ── The widget's own state ────────────────────────────────────────────

    def _project_text(self) -> str:
        """The name the user typed, trimmed — the empty cell of the table."""
        return self.name_edit.text().strip()

    def _folder_text(self) -> str:
        return self.folder_edit.text().strip()

    def _name_problem(self, name: str) -> Optional[str]:
        """Why this project name cannot be used, or None.

        ONE place, so the greyed button and the refusal in _on_ok can never
        disagree — Д-1 requires both halves, because the button is UX and the
        refusal is the guard (a shortcut or a programmatic accept bypasses the
        button, and there is a cell that calls _on_ok directly for exactly that).
        """
        if name in _RELATIVE_DIR_NAMES:
            return _("A project name cannot be a relative path name like '.' or '..'.")
        if "/" in name or "\\" in name:
            return _("Type a project name without path separators.")
        return None

    def _target_dir(self) -> Optional[Path]:
        """<folder>/<name> — the directory the dialog would create, or None when
        the pair is not usable yet. Unusable means: an empty name or folder, a name
        carrying a path separator (it would let the project escape the picked
        folder), or a relative name "." / ".." (which escapes WITHOUT a separator —
        see _RELATIVE_DIR_NAMES)."""
        name, folder = self._project_text(), self._folder_text()
        if not name or not folder or self._name_problem(name):
            return None
        return Path(folder) / name

    def _update_preview(self) -> None:
        name = self._project_text()
        target = self._target_dir()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            target is not None and Path(self._folder_text()).is_dir())
        problem = self._name_problem(name) if name else None
        if problem:
            # The preview says WHAT is wrong before the click, not after it.
            self.preview_label.setText(problem)
            return
        if target is None:
            self.preview_label.setText(
                _("Pick a folder and type a project name (no slashes)."))
            return
        self.preview_label.setText(
            _("Will create {path}").format(path=project_config_path_for_dir(target)))

    def _on_browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, _("New project directory"), self._folder_text())
        if chosen:
            self.folder_edit.setText(chosen)

    def _on_ok(self) -> None:
        """Create the project, or stay open and say why not. Nothing is written
        on any refusal — that is the whole point of the ordering here."""
        name, folder = self._project_text(), self._folder_text()
        problem = self._name_problem(name)
        if not name or problem:
            QMessageBox.warning(self, _("Create Project"),
                                problem or _("Type a project name without path separators."))
            return
        if not Path(folder).is_dir():
            QMessageBox.warning(self, _("Create Project"),
                                _("Pick an existing folder first."))
            return
        try:
            self.created_config_path = create_project(Path(folder) / name)
        except ProjectConfigExists as exists:
            message = _("A project config already exists here: {path} — nothing "
                        "was created. Open it instead, or choose another "
                        "directory.").format(path=exists.path)
            # One ERROR line in the Log dock as well as the box: the refusal must
            # stay observable after the box is dismissed (2026-09-24 decision —
            # "с модалкой", the Log line was part of it).
            logger.error(message)
            QMessageBox.warning(self, _("Create Project"), message)
            return
        except OSError as e:
            message = _("Write failed: {error}").format(error=e)
            logger.error(message)
            QMessageBox.warning(self, _("Create Project"), message)
            return
        self.accept()
