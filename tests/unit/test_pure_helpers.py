"""Unit tests for pure helpers — no server required."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_pincode_band_import():
    from app.dispatch.eligibility import pincode_band

    assert pincode_band("500072", "500072") == 0
    assert pincode_band("500072", "500081") == 1
    assert pincode_band("500072", "560001") == 2


def test_normalize_pincode():
    from app.dispatch.eligibility import normalize_pincode

    assert normalize_pincode("500 072") == "500072"
    assert normalize_pincode("bad") is None
