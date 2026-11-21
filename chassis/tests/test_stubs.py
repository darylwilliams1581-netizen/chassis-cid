"""Sleep mode, dual-map reconciliation, and frequency-routed weights stay stubbed."""

from __future__ import annotations

import pytest

from drayl.stubs import DualMapReconciler, FrequencyRoutedWeights, SleepMode


def test_sleep_mode_is_stubbed():
    with pytest.raises(NotImplementedError, match="Sleep mode is stubbed in P36"):
        SleepMode().run()


def test_dual_map_reconciliation_is_stubbed():
    with pytest.raises(NotImplementedError, match="Dual map reconciliation is stubbed"):
        DualMapReconciler().reconcile()


def test_frequency_routed_weights_are_stubbed():
    with pytest.raises(NotImplementedError, match="Frequency-routed weights are stubbed"):
        FrequencyRoutedWeights().assign_paths()
