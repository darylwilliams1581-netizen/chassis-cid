"""Dual-map reconciliation and frequency-routed weights stay stubbed.

Sleep mode has a minimal school-mode pass in P38; see test_sleep_mode.py.
"""

from __future__ import annotations

import pytest

from drayl.stubs import DualMapReconciler, FrequencyRoutedWeights


def test_dual_map_reconciliation_is_stubbed():
    with pytest.raises(NotImplementedError, match="Dual map reconciliation is stubbed"):
        DualMapReconciler().reconcile()


def test_frequency_routed_weights_are_stubbed():
    with pytest.raises(NotImplementedError, match="Frequency-routed weights are stubbed"):
        FrequencyRoutedWeights().assign_paths()
