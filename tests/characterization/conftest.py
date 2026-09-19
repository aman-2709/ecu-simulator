"""Shared fixtures for characterization tests.

These tests pin the behavior of the legacy implementation byte for byte. They exist so
that every later wire-level change is deliberate. See docs/known-deviations.md for the
DEV-xx identifiers referenced in xfail reasons.
"""

import pytest


def xfail_deviation(dev_id: str, summary: str):
    """Strict xfail marker: the test asserts the corrected behavior for a known deviation.

    strict=True means that once the deviation is fixed the test starts passing and the
    marker must be removed in the same commit, so corrections are never accidental.
    """
    return pytest.mark.xfail(strict=True, reason=f"{dev_id}: {summary} (docs/known-deviations.md)")
