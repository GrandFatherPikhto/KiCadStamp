# kicadstamp/config/name_hint.py
"""The ONE close-match name suggestion for a reference that names no record
(plan_2026_10_05_uuid_tails, part 1).

Format 2 refused a missing reference BY NAME and suggested the closest known
name (loader's `_check_anchor_point`). Format 3 refuses a dangling reference BY
UUID — and had lost the suggestion, so the user saw a UUID they never typed and
no hint. Both refusals now ask this one place, so the two diagnostics cannot
drift apart (map.md §2.1: one rule, one place).
"""
from __future__ import annotations

import difflib
from typing import Iterable

from ..i18n import _

__all__ = ["close_name_hint"]


def close_name_hint(name, known_names: Iterable[str]) -> str:
    """The suggestion suffix for `name` among `known_names`: " (did you mean
    'x'?)" for the closest name, or "" when none is close enough.

    `difflib.get_close_matches(n=1)` — the very rule the format-2 refusal used;
    a HINT only: the reference is still resolved by UUID alone (§0)."""
    suggestion = difflib.get_close_matches(str(name), sorted(known_names or ()), n=1)
    return (_(" (did you mean {suggestion!r}?)").format(suggestion=suggestion[0])
            if suggestion else "")
