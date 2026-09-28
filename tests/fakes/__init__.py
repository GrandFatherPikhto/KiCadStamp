# tests/fakes/ — shared test doubles (Ф1.4 of the tests refactor plan).
#
# A fake belongs here only when it is repeated in ≥2 test files; a fake used by
# one file stays local to it. tests/test_fakes_conformance.py is the
# correspondence cell: it pins which methods a shared fake may carry beyond the
# real seam, so a shared fake cannot quietly drift away from the interface it
# stands in for.
