# kicadstamp/cloner/sexp.py
"""
Thin helpers over sexpdata. The choice of sexpdata over schema-aware
libraries is deliberate: the KiCad format is syntactically stable since v6,
the vocabulary only grows, and a generic parser digests new tokens
transparently (verified: kinparse stumbles on a KiCad 10 netlist,
sexpdata — does not).
"""

import sexpdata


def sval(x):
    """Symbol -> str, everything else as-is."""
    return x.value() if isinstance(x, sexpdata.Symbol) else x


def is_node(n, key: str) -> bool:
    return isinstance(n, list) and bool(n) and sval(n[0]) == key


def children(node, key: str):
    """All child nodes (key ...)."""
    return [n for n in node if is_node(n, key)]


def child(node, key: str, default=None):
    """First child node (key ...) or default."""
    for n in node:
        if is_node(n, key):
            return n
    return default


def atom(node, key: str, default=None):
    """Value of the first atom of node (key value): child(node,key)[1]."""
    c = child(node, key)
    if c is None or len(c) < 2:
        return default
    return sval(c[1])


def load_file(path: str):
    with open(path, encoding="utf-8", errors="replace") as f:
        return sexpdata.loads(f.read())


def sym(s: str) -> sexpdata.Symbol:
    """Inverse of sval(): wrap a plain str as a Symbol for building nodes."""
    return sexpdata.Symbol(s)


def dumps(obj) -> str:
    """Thin wrapper over sexpdata.dumps()."""
    return sexpdata.dumps(obj)


def save_file(path: str, obj) -> None:
    """Mirrors load_file(): writes dumps(obj) to a utf-8 file."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(obj))
