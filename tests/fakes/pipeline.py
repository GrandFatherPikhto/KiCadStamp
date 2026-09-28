# tests/fakes/pipeline.py
"""Lifetime half of the real ApplyPipeline, shared by every dock stand-in.

Why this exists (Ф1.4b of the tests refactor plan): the same class was declared
in SIX gui test files, and had already drifted into two shapes — four copies
counted their close(), two were a bare `pass`. The pipeline IS a context manager
whose __exit__ releases the kipy/pynng socket a run created
(kicadstamp/apply_pipeline.py::ApplyPipeline.close,
plan_2026_09_14_apply_pipeline_socket_leak), and the docks under test enter it
with `with ...`, so every stand-in must support the protocol.

The COUNTING shape is the one kept: `closed` is a class attribute, so a test
that wants to assert "the redraw handed its socket back" reads its OWN
subclass's counter (`type(self).closed += 1` writes on the subclass), while a
test that does not care is unaffected by the increment.

Correspondence cell: tests/test_fakes_conformance.py pins that the real
ApplyPipeline is a context manager with close/__enter__/__exit__, so this stub
cannot drift away from the thing it stands in for.
"""
from __future__ import annotations


class PipelineStubLifetime:
    """Counted close() plus the context-manager protocol, for ApplyPipeline stand-ins."""

    closed = 0

    def close(self) -> None:
        # Counted, so a test can assert that a redraw really hands its socket
        # back (plan_2026_09_14_apply_pipeline_socket_leak P.3.2).
        type(self).closed += 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


__all__ = ["PipelineStubLifetime"]
