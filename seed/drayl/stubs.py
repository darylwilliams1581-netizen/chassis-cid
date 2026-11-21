"""Interfaces for work that is specified but not built in P38.

Sleep mode has a minimal school-mode pass in drayl.sleep. These two
pieces are still placeholders only.
"""

from __future__ import annotations


class DualMapReconciler:
    """TODO: dual map reconciliation.

    There are two maps: the static designed quadrant map, and a lived
    heat map that grows from access patterns (frequency, recency, and
    whether retrieved grains improved answers). When they disagree, the
    lived map probably knows something. P38 does not yet decide how to
    resolve that disagreement.
    """

    def reconcile(self) -> None:
        raise NotImplementedError("Dual map reconciliation is stubbed in P38")


class FrequencyRoutedWeights:
    """TODO: frequency-routed weights.

    Low-frequency (stable background) weights should run on cheap
    integer math paths. High-frequency (sharp detail) weights should
    run on full float paths. At the binary level, bit patterns that
    flip often vs stay stable are the signal for path assignment.
    """

    def assign_paths(self) -> None:
        raise NotImplementedError("Frequency-routed weights are stubbed in P38")
