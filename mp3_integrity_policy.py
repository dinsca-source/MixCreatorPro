# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass


TAIL_NON_BLOCKING_WINDOW_MS = 1000
TAIL_NON_BLOCKING_BOUNDARY_TOLERANCE_MS = 20
TAIL_NON_BLOCKING_EXCLUSION_REASON = (
    "Anomalia localizzata esclusivamente nell'ultimo secondo effettivo (coda finale) del file MP3: classificata come warning non bloccante."
)
TAIL_NON_BLOCKING_HEALTHY_REASON = (
    "File utilizzabile: rilevate anomalie esclusivamente nella coda finale (ultimo secondo effettivo), conservate come warning non bloccanti."
)

TAIL_CLASSIFICATION_BLOCKING = "Bloccante"
TAIL_CLASSIFICATION_WARNING = "Avvertimento di coda"
TAIL_FINAL_OUTCOME_IMPACT_BLOCKING = "Bloccante"
TAIL_FINAL_OUTCOME_IMPACT_NONE = "Nessuno"


@dataclass(slots=True)
class TailPolicyDecision:
    within_non_blocking_tail: bool
    blocking: bool
    classification: str
    final_outcome_impact: str


def is_issue_exclusively_in_non_blocking_tail(
    *,
    start_ms: int | None,
    end_ms: int | None,
    file_duration_ms: int,
    tail_window_ms: int = TAIL_NON_BLOCKING_WINDOW_MS,
    boundary_tolerance_ms: int = TAIL_NON_BLOCKING_BOUNDARY_TOLERANCE_MS,
) -> bool:
    if start_ms is None or file_duration_ms <= 0:
        return False

    actual_end = end_ms if end_ms is not None else start_ms
    if actual_end < start_ms:
        actual_end = start_ms

    tail_start = max(0, int(file_duration_ms) - int(tail_window_ms))
    tol = max(0, int(boundary_tolerance_ms))

    if actual_end < (tail_start - tol):
        return False
    if start_ms < (tail_start - tol):
        return False
    return True


def build_tail_policy_decision(*, within_non_blocking_tail: bool) -> TailPolicyDecision:
    if within_non_blocking_tail:
        return TailPolicyDecision(
            within_non_blocking_tail=True,
            blocking=False,
            classification=TAIL_CLASSIFICATION_WARNING,
            final_outcome_impact=TAIL_FINAL_OUTCOME_IMPACT_NONE,
        )
    return TailPolicyDecision(
        within_non_blocking_tail=False,
        blocking=True,
        classification=TAIL_CLASSIFICATION_BLOCKING,
        final_outcome_impact=TAIL_FINAL_OUTCOME_IMPACT_BLOCKING,
    )
