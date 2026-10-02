# tests/fakes/format3.py
"""Fixtures for exercising format 3 (UUID, step 2->3) — plan §У1.4/У1.5.

``CURRENT_FORMAT`` stays 2 in the product, so a format-3 file is refused by the
reader unless a test pins the build to 3. ``format3`` does exactly that by
substituting the MODULE attribute (never a from-import — both
``format_version.current_format()`` and ``refuse_newer()`` read
``CURRENT_FORMAT`` at call time; the pattern is test_config_format_version.py:171).

``det_uuid`` gives deterministic UUIDs so byte snapshots do not drift.
"""
import uuid

import pytest

from kicadstamp.config import format_version

# A fixed namespace: det_uuid(n) is stable across runs and machines.
_NS = uuid.UUID("00000000-0000-0000-0000-0000000000ab")


def det_uuid(n) -> str:
    """A deterministic UUID for a small integer/name — one per record/target."""
    return str(uuid.uuid5(_NS, str(n)))


@pytest.fixture
def format3(monkeypatch):
    """Pin this build's CURRENT_FORMAT to 3 (plan §У1.4).

    With CURRENT_FORMAT = 3 the reader accepts ``(version 3)`` and the writer
    stamps it; no 2->3 converter exists yet, so lifting a format-2 file is a
    'hole in the chain' fatal — deliberately, that is what U1 leaves for U3."""
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 3)
    return 3
