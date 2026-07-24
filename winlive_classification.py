# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WinLiveOutcome(str, Enum):
    MISSING_TEXT_AND_UNRECOGNIZED_CHORDS = "MANCA TAG TESTO E ACCORDI NON RICONOSCIUTI"
    UNRECOGNIZED_CHORDS = "FILE CON ACCORDI NON RICONOSCIUTI"
    MISSING_TEXT_AND_CHORDS = "MANCA TAG TESTO E TAG ACCORDI"
    MISSING_TEXT_ONLY = "MANCA SOLO IL TAG DI TESTO"
    MISSING_CHORDS_ONLY = "MANCA SOLO IL TAG ACCORDI"
    FILE_NORMALIZED = "FILE NORMALIZZATO"
    FILE_ALREADY_OK = "FILE GIA' OK"
    NORMALIZATION_NOT_VALIDATED = "NORMALIZZAZIONE NON VALIDATA"


class PostNormalizationValidationStatus(str, Enum):
    NOT_NECESSARY = "NON NECESSARIA"
    OK = "OK"
    FAILED = "FAILED"


@dataclass(slots=True)
class WinLiveClassificationInput:
    text_valid: bool
    chord_valid: bool
    chord_unrecognized_count: int
    text_was_modified: bool
    post_validation_status: PostNormalizationValidationStatus


@dataclass(slots=True)
class WinLiveClassificationResult:
    outcome: WinLiveOutcome
    reason: str


def classify_winlive(input_data: WinLiveClassificationInput) -> WinLiveClassificationResult:
    text_missing = not input_data.text_valid
    chord_missing = not input_data.chord_valid
    has_unrecognized = input_data.chord_valid and input_data.chord_unrecognized_count > 0

    if text_missing and has_unrecognized:
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.MISSING_TEXT_AND_UNRECOGNIZED_CHORDS,
            reason="Testo non valido e accordi con '?' presenti.",
        )

    if has_unrecognized:
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.UNRECOGNIZED_CHORDS,
            reason="Accordi validi con '?' presenti.",
        )

    if text_missing and chord_missing:
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.MISSING_TEXT_AND_CHORDS,
            reason="Tag testo e accordi non validi.",
        )

    if text_missing and not chord_missing:
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.MISSING_TEXT_ONLY,
            reason="Tag testo non valido, accordi validi.",
        )

    if chord_missing and not text_missing:
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.MISSING_CHORDS_ONLY,
            reason="Tag accordi non valido, testo valido.",
        )

    if input_data.text_was_modified:
        if input_data.post_validation_status == PostNormalizationValidationStatus.OK:
            return WinLiveClassificationResult(
                outcome=WinLiveOutcome.FILE_NORMALIZED,
                reason="File modificato e validato.",
            )
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.NORMALIZATION_NOT_VALIDATED,
            reason="File modificato ma validazione post-normalizzazione non OK.",
        )

    return WinLiveClassificationResult(
        outcome=WinLiveOutcome.FILE_ALREADY_OK,
        reason="Tutto valido e nessuna modifica necessaria.",
    )
