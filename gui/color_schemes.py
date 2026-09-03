# gui/color_schemes.py
"""Built-in QPalette color schemes (2026-09-03, plan color_scheme_setting).

Embedded in code, not read from disk (see design §1) — a filesystem
dependency on /usr/share/qt6ct would break on any machine without that
package installed, including frozen builds. Values copied verbatim from
qt6ct's own colors/airy.conf (https://github.com/trialuser02/qt6ct) — the
same list order qt6ct.cpp::loadColorScheme uses: index i ==
QPalette.ColorRole(i) for i in range(21) (WindowText..Accent), no
permutation (confirmed by reading the qt6ct source, design §0).
"""
from typing import Optional

from PyQt6.QtGui import QColor, QPalette


# Each list: 21 ARGB hex strings, index i == QPalette.ColorRole(i).
_SCHEMES: dict[str, dict[str, list[str]]] = {
    "Airy": {
        "active": [
            "#ff000000", "#ffdcdcdc", "#ffdcdcdc", "#ff5e5c5b", "#ff646464",
            "#ffe1e1e1", "#ff000000", "#ff0a0a0a", "#ff0a0a0a", "#ffc8c8c8",
            "#ffffffff", "#ffe7e4e0", "#ff0986d3", "#ff0a0a0a", "#ff0986d3",
            "#ffa70b06", "#ff5c5b5a", "#ffffffff", "#ff646464", "#ff050505",
            "#80000000",
        ],
        "inactive": [
            "#ff323232", "#ffb4b4b4", "#ffdcdcdc", "#ff5e5c5b", "#ff646464",
            "#ffe1e1e1", "#ff323232", "#ff323232", "#ff323232", "#ff969696",
            "#ffc8c8c8", "#ffe7e4e0", "#ff0986d3", "#ff323232", "#ff0986d3",
            "#ffa70b06", "#ff5c5b5a", "#ffffffff", "#ff646464", "#ff323232",
            "#80000000",
        ],
        "disabled": [
            "#ffffffff", "#ff424245", "#ffdcdcdc", "#ff5e5c5b", "#ff646464",
            "#ffe1e1e1", "#ff808080", "#ffffffff", "#ff808080", "#ff969696",
            "#ffc8c8c8", "#ffe7e4e0", "#ff0986d3", "#ff808080", "#ff0986d3",
            "#ffa70b06", "#ff5c5b5a", "#ffffffff", "#ff646464", "#ffffffff",
            "#80000000",
        ],
    },
}

_GROUP_MAP = {
    "active": QPalette.ColorGroup.Active,
    "inactive": QPalette.ColorGroup.Inactive,
    "disabled": QPalette.ColorGroup.Disabled,
}


def available_color_schemes() -> list[str]:
    """Built-in scheme names, sorted (today just ["Airy"], but callers must
    not assume exactly one)."""
    return sorted(_SCHEMES.keys())


def load_color_scheme(name: str) -> Optional[QPalette]:
    """The QPalette for a built-in scheme name, or None when unknown (the
    caller must degrade quietly — see gui_main.apply_saved_color_scheme).

    Mirrors qt6ct.cpp::loadColorScheme: role i == QPalette.ColorRole(i) for
    i in range(21), applied to all three color groups.
    """
    scheme = _SCHEMES.get(name)
    if scheme is None:
        return None
    palette = QPalette()
    for group_key, group in _GROUP_MAP.items():
        for role, hexcolor in enumerate(scheme[group_key]):
            palette.setColor(group, QPalette.ColorRole(role), QColor(hexcolor))
    return palette
