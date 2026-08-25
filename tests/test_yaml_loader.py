# tests/test_yaml_loader.py
"""Tests for kicadstamp.utils.yaml_loader.safe_load — the single chokepoint
for reading YAML (CSafeLoader when libyaml is available, SafeLoader otherwise)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import yaml

from kicadstamp.utils.yaml_loader import safe_load


def test_safe_load_matches_yaml_safe_load_for_plain_yaml():
    doc = "a: 1\nb:\n  - x\n  - y\n"
    assert safe_load(doc) == yaml.safe_load(doc)


def test_safe_load_returns_none_for_empty_stream():
    assert safe_load("") is None


def test_safe_load_raises_yaml_error_on_broken_document():
    with pytest.raises(yaml.YAMLError):
        safe_load("a: [unclosed\n")
