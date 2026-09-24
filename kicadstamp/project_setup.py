# kicadstamp/project_setup.py
"""Creating a project — the one place that writes a brand-new one.

A project is a DIRECTORY (2026-09-24, Denis: "У KiCad создаётся проект
директорией, а открывается файл проекта. Вот так и делаем"): the config is
``<dir>/<dir>.sexp``, and a new project is born with the five infrastructure
directories it will need (``kicadstamp.utils.paths.PROJECT_INFRA_DIRS``).

This module is deliberately Qt-free, so the GUI's Create Project dialog and the
tests can both drive it directly.

WHY THE INFRASTRUCTURE IS CREATED HERE although the 2026-09-11 decision said the
opposite ("every consumer creates its own directory on demand — that is why the
Files tab was removed"): Denis, 2026-09-24 — "автоматически создаётся директория с
нужной инфраструктурой". The reversal covers NEW projects only. An existing
profile keeps whichever directories it happens to have; nothing in this module is
ever pointed at one, and nothing here renames, moves or deletes anything.

The refusal is part of the contract: an existing config is never overwritten, it
raises :class:`ProjectConfigExists` and the caller turns that into a message.
"""
from pathlib import Path

from kicadstamp.utils.paths import (PROJECT_INFRA_DIRS,
                                    project_config_path_for_dir)


class ProjectConfigExists(Exception):
    """Raised instead of overwriting an existing project's config file."""

    def __init__(self, path):
        super().__init__(str(path))
        self.path = Path(path)


def create_project(project_dir) -> Path:
    """Create a project in ``project_dir`` and return its config's path.

    Makes the directory itself (parents included), writes the canonical empty
    config ``<dir>/<dir>.sexp``, and lays out every name in PROJECT_INFRA_DIRS
    inside it. Refuses — with :class:`ProjectConfigExists`, BEFORE writing
    anything — when that config is already there.

    A directory that exists and holds unrelated files is fine: only a collision
    with the project's own config counts as "this project is already here".
    """
    config = Path(project_config_path_for_dir(project_dir))
    if config.exists():
        raise ProjectConfigExists(config)
    config.parent.mkdir(parents=True, exist_ok=True)
    # Through the ONE config writer, not a hand-written literal: a project born
    # here is CURRENT format, and a literal is a second place that has to be
    # kept in step with CURRENT_FORMAT (the bypass the Т3 acceptance's
    # structural cell exists to catch). A fresh file needs no `.bak`.
    # Local import: this module is on the CLI/project-creation path.
    from .config_writer import write_config_file

    write_config_file(config, {})
    for name in PROJECT_INFRA_DIRS:
        (config.parent / name).mkdir(exist_ok=True)
    return config
