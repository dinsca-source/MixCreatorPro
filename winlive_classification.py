# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WinLiveOutcome(str, Enum):
    STRUCTURE_ERROR = "ERRORI STRUTTURA WINLIVE"
    MISSING_TEXT_AND_CHORDS = "SENZA TAG DI TESTO E DI ACCORDI"
    MISSING_TEXT_ONLY = "SENZA TAG DI TESTO"
    MISSING_CHORDS_ONLY = "SENZA TAG ACCORDI"
    MISSING_TEXT_AND_UNRECOGNIZED_CHORDS = "SENZA TAG DI TESTO + ACCORDI NON RICONOSCIUTI"
    UNRECOGNIZED_CHORDS = "ACCORDI NON RICONOSCIUTI"
    MODIFICATION_NOT_INTEGRAL = "NON INTEGRO DOPO MODIFICA"
    FILE_NORMALIZED = "NORMALIZZATO"
    REQUIRES_NORMALIZATION = "RICHIEDE NORMALIZZAZIONE"
    FILE_ALREADY_OK = "CONFORME"


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
    structure_valid: bool = True
    synct_present: bool = True
    chord_present: bool = True
    normalization_required: bool = False
    normalization_attempted: bool = False


@dataclass(slots=True)
class WinLiveClassificationResult:
    outcome: WinLiveOutcome
    reason: str


def classify_winlive(input_data: WinLiveClassificationInput) -> WinLiveClassificationResult:
    if not input_data.structure_valid:
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.STRUCTURE_ERROR,
            reason="Struttura WinLive non parsabile o ambigua.",
        )

    text_missing = not input_data.synct_present
    chord_missing = not input_data.chord_present
    has_unrecognized = input_data.chord_present and input_data.chord_unrecognized_count > 0

    # 1. errore strutturale originale
    # 2. assenza di entrambi i blocchi
    # 3. assenza WL5SYNCT
    # 4. assenza WL5CHORD
    # 5. accordi non riconosciuti
    # 6. modifica tentata ma validazione fallita
    # 7. modifica applicata e validata
    # 8. richiede normalizzazione in sola analisi
    # 9. conforme

    if text_missing and chord_missing:
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.MISSING_TEXT_AND_CHORDS,
            reason="Assenti sia WL5SYNCT che WL5CHORD.",
        )

    if text_missing and has_unrecognized:
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.MISSING_TEXT_AND_UNRECOGNIZED_CHORDS,
            reason="WL5SYNCT assente e accordi non riconosciuti presenti.",
        )

    if text_missing and not chord_missing:
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.MISSING_TEXT_ONLY,
            reason="WL5SYNCT assente.",
        )

    if chord_missing and not text_missing:
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.MISSING_CHORDS_ONLY,
            reason="WL5CHORD assente.",
        )

    if has_unrecognized:
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.UNRECOGNIZED_CHORDS,
            reason="Rilevati accordi non riconosciuti nel blocco WL5CHORD.",
        )

    if input_data.normalization_attempted and input_data.post_validation_status == PostNormalizationValidationStatus.FAILED:
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.MODIFICATION_NOT_INTEGRAL,
            reason="Normalizzazione tentata ma validazione post-modifica fallita.",
        )

    if input_data.normalization_attempted and input_data.post_validation_status == PostNormalizationValidationStatus.OK:
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.FILE_NORMALIZED,
            reason="File normalizzato e validato.",
        )

    if input_data.normalization_required:
        return WinLiveClassificationResult(
            outcome=WinLiveOutcome.REQUIRES_NORMALIZATION,
            reason="File valido ma richiede normalizzazione WinLive.",
        )

    return WinLiveClassificationResult(
        outcome=WinLiveOutcome.FILE_ALREADY_OK,
        reason="Struttura WinLive conforme senza modifiche necessarie.",
    )
