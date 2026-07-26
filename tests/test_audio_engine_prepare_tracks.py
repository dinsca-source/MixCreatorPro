# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from audio_engine import AudioEngine
from clip_info import ClipInfo


class _FakeFFmpeg:
    def __init__(self, durations: dict[str, float]) -> None:
        self._durations = durations

    def get_duration(self, file_path: Path) -> float:
        return float(self._durations[file_path.name])


class AudioEnginePrepareTracksTests(unittest.TestCase):
    def _build_engine(self, durations: dict[str, float]) -> AudioEngine:
        engine = AudioEngine.__new__(AudioEngine)
        engine.ffmpeg = _FakeFFmpeg(durations)
        engine._process = None
        return engine

    def test_reused_valid_clips_are_excluded_from_real_work_units(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            names = ["a.mp3", "b.mp3", "c.mp3", "d.mp3"]
            for name in names:
                (source / name).write_bytes(b"x")

            files = [source / name for name in names]
            durations = {name: 100.0 for name in names}
            engine = self._build_engine(durations)

            previous = {
                "a.mp3": {"start_ms": 10_000, "duration_ms": 30_000},
                "b.mp3": {"start_ms": 99_000, "duration_ms": 5_000},
            }
            custom = {
                "d.mp3": ClipInfo(use_custom_clip=True, clip_start_ms=2_000, clip_end_ms=8_000),
            }

            events: list[tuple[int, int, str]] = []

            prepared, reuse_summary, preparation_summary = engine._prepare_tracks(
                files=files,
                source_folder=source,
                clip_seconds=20,
                cut_mode="inizio",
                custom_clips=custom,
                previous_resolved_clips=previous,
                progress_callback=lambda c, t, m: events.append((c, t, m)),
                cancel_event=None,
            )

            self.assertEqual(len(prepared), 4)
            self.assertEqual(sum(1 for item in prepared if item["source_mode"] == "previous"), 1)

            self.assertEqual(preparation_summary.total_tracks, 4)
            self.assertEqual(preparation_summary.reusable_clips, 1)
            self.assertEqual(preparation_summary.clips_to_generate, 3)
            self.assertEqual(preparation_summary.recalculated_clips, 1)
            self.assertEqual(preparation_summary.new_clips, 1)
            self.assertEqual(preparation_summary.modified_clips, 1)
            self.assertEqual(preparation_summary.previous_invalid_clips, 1)
            self.assertEqual(preparation_summary.reused_track_names, ["a.mp3"])
            self.assertEqual(preparation_summary.tracks_to_process, ["b.mp3", "c.mp3", "d.mp3"])

            self.assertEqual(reuse_summary["reused"], 1)
            self.assertEqual(reuse_summary["recalculated"], 1)
            self.assertEqual(reuse_summary["new"], 1)
            self.assertEqual(reuse_summary["modified"], 1)
            self.assertEqual(reuse_summary["previous_invalid"], 1)

            self.assertEqual(len(events), 4)
            self.assertEqual(events[0][0], 0)
            self.assertEqual(events[0][1], 3)
            self.assertTrue(events[0][2].startswith("Riutilizzo clip precedente:"))

            processing_events = [item for item in events if "Riutilizzo" not in item[2]]
            self.assertEqual([(c, t) for c, t, _ in processing_events], [(1, 3), (2, 3), (3, 3)])
            for _, total, message in processing_events:
                self.assertEqual(total, 3)
                self.assertRegex(message, r"\d/3")


if __name__ == "__main__":
    unittest.main()
