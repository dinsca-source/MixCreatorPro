# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest

from winlive_classification import (
    PostNormalizationValidationStatus,
    WinLiveClassificationInput,
    WinLiveOutcome,
    classify_winlive,
)


class WinLiveClassificationTests(unittest.TestCase):
    def test_priority_structure_error(self) -> None:
        result = classify_winlive(
            WinLiveClassificationInput(
                text_valid=False,
                chord_valid=False,
                chord_unrecognized_count=0,
                text_was_modified=False,
                post_validation_status=PostNormalizationValidationStatus.NOT_NECESSARY,
                structure_valid=False,
            )
        )
        self.assertEqual(result.outcome, WinLiveOutcome.STRUCTURE_ERROR)

    def test_priority_missing_text_and_unrecognized(self) -> None:
        result = classify_winlive(
            WinLiveClassificationInput(
                text_valid=False,
                chord_valid=True,
                chord_unrecognized_count=1,
                text_was_modified=False,
                post_validation_status=PostNormalizationValidationStatus.NOT_NECESSARY,
                synct_present=False,
                chord_present=True,
            )
        )
        self.assertEqual(result.outcome, WinLiveOutcome.MISSING_TEXT_AND_UNRECOGNIZED_CHORDS)

    def test_priority_unrecognized_chords(self) -> None:
        result = classify_winlive(
            WinLiveClassificationInput(
                text_valid=True,
                chord_valid=True,
                chord_unrecognized_count=3,
                text_was_modified=False,
                post_validation_status=PostNormalizationValidationStatus.NOT_NECESSARY,
                synct_present=True,
                chord_present=True,
            )
        )
        self.assertEqual(result.outcome, WinLiveOutcome.UNRECOGNIZED_CHORDS)

    def test_priority_missing_both(self) -> None:
        result = classify_winlive(
            WinLiveClassificationInput(
                text_valid=False,
                chord_valid=False,
                chord_unrecognized_count=0,
                text_was_modified=False,
                post_validation_status=PostNormalizationValidationStatus.NOT_NECESSARY,
                synct_present=False,
                chord_present=False,
            )
        )
        self.assertEqual(result.outcome, WinLiveOutcome.MISSING_TEXT_AND_CHORDS)

    def test_priority_missing_text_only(self) -> None:
        result = classify_winlive(
            WinLiveClassificationInput(
                text_valid=False,
                chord_valid=True,
                chord_unrecognized_count=0,
                text_was_modified=False,
                post_validation_status=PostNormalizationValidationStatus.NOT_NECESSARY,
                synct_present=False,
                chord_present=True,
            )
        )
        self.assertEqual(result.outcome, WinLiveOutcome.MISSING_TEXT_ONLY)

    def test_priority_missing_chords_only(self) -> None:
        result = classify_winlive(
            WinLiveClassificationInput(
                text_valid=True,
                chord_valid=False,
                chord_unrecognized_count=0,
                text_was_modified=False,
                post_validation_status=PostNormalizationValidationStatus.NOT_NECESSARY,
                synct_present=True,
                chord_present=False,
            )
        )
        self.assertEqual(result.outcome, WinLiveOutcome.MISSING_CHORDS_ONLY)

    def test_priority_file_normalized(self) -> None:
        result = classify_winlive(
            WinLiveClassificationInput(
                text_valid=True,
                chord_valid=True,
                chord_unrecognized_count=0,
                text_was_modified=False,
                post_validation_status=PostNormalizationValidationStatus.OK,
                normalization_attempted=True,
            )
        )
        self.assertEqual(result.outcome, WinLiveOutcome.FILE_NORMALIZED)

    def test_priority_requires_normalization_when_not_attempted(self) -> None:
        result = classify_winlive(
            WinLiveClassificationInput(
                text_valid=True,
                chord_valid=True,
                chord_unrecognized_count=0,
                text_was_modified=False,
                post_validation_status=PostNormalizationValidationStatus.NOT_NECESSARY,
                normalization_required=True,
                normalization_attempted=False,
            )
        )
        self.assertEqual(result.outcome, WinLiveOutcome.REQUIRES_NORMALIZATION)

    def test_priority_modification_not_integral(self) -> None:
        result = classify_winlive(
            WinLiveClassificationInput(
                text_valid=True,
                chord_valid=True,
                chord_unrecognized_count=0,
                text_was_modified=False,
                post_validation_status=PostNormalizationValidationStatus.FAILED,
                normalization_attempted=True,
            )
        )
        self.assertEqual(result.outcome, WinLiveOutcome.MODIFICATION_NOT_INTEGRAL)

    def test_priority_file_already_ok(self) -> None:
        result = classify_winlive(
            WinLiveClassificationInput(
                text_valid=True,
                chord_valid=True,
                chord_unrecognized_count=0,
                text_was_modified=False,
                post_validation_status=PostNormalizationValidationStatus.NOT_NECESSARY,
            )
        )
        self.assertEqual(result.outcome, WinLiveOutcome.FILE_ALREADY_OK)


if __name__ == "__main__":
    unittest.main()
