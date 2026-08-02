# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import worker
from mp3_repertory_new_tracks_update import (
    DECISION_SKIP_CURRENT,
    DECISION_UPDATE_AND_BYPASS_SESSION,
    Rep003UpdateResult,
    run_repertory_new_tracks_update,
)


class _ImmediateThread:
    def __init__(self, *, target, kwargs, daemon):
        self._target = target
        self._kwargs = kwargs
        self.daemon = daemon

    def start(self):
        self._target(**self._kwargs)


class Rep003EngineTests(unittest.TestCase):
    def _write_mp3(self, path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def test_duplicate_detection_and_keep_decision_preserves_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            for folder in (new_tracks, split, general, smartphone):
                folder.mkdir(parents=True, exist_ok=True)

            source = new_tracks / "song.mp3"
            self._write_mp3(source, b"NEW")

            split_existing = split / "Italiano" / "song.mp3"
            general_existing = general / "song.mp3"
            smart_existing = smartphone / "Italiano" / "song.mp3"
            smart_general_existing = smartphone / "REPERTORIO_GENERALE_DA_MIXCREATOR" / "song.mp3"
            self._write_mp3(split_existing, b"OLD_SPLIT")
            self._write_mp3(general_existing, b"OLD_GENERAL")
            self._write_mp3(smart_existing, b"OLD_SMART")
            self._write_mp3(smart_general_existing, b"OLD_SMART_GENERAL")

            assignments = {
                str(source): {
                    "destinations": ["Italiano"],
                    "status": "Gestito",
                }
            }

            decisions = []

            def _decision(payload):
                decisions.append(payload)
                return DECISION_SKIP_CURRENT

            result = run_repertory_new_tracks_update(
                new_tracks_dir=new_tracks,
                split_repertory_dir=split,
                general_repertory_dir=general,
                smartphone_tablet_dir=smartphone,
                assignments_snapshot=assignments,
                decision_callback=_decision,
            )

            self.assertEqual(result.processed_tracks, 1)
            self.assertEqual(len(decisions), 1)
            self.assertGreaterEqual(len(decisions[0].get("existing_paths", [])), 3)
            self.assertEqual(split_existing.read_bytes(), b"OLD_SPLIT")
            self.assertEqual(general_existing.read_bytes(), b"OLD_GENERAL")
            self.assertEqual(smart_existing.read_bytes(), b"OLD_SMART")
            self.assertEqual(smart_general_existing.read_bytes(), b"OLD_SMART_GENERAL")
            self.assertEqual(result.kept_tracks, 1)
            self.assertTrue(Path(result.report_paths["csv"]).is_file())
            self.assertTrue(Path(result.report_paths["html"]).is_file())
            self.assertTrue(Path(result.report_paths["xlsx"]).is_file())
            self.assertTrue(Path(result.log_path).is_file())

    def test_multiple_copy_targets_and_smartphone_tree_replication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            for folder in (new_tracks, split, general, smartphone):
                folder.mkdir(parents=True, exist_ok=True)

            source = new_tracks / "brano.mp3"
            self._write_mp3(source, b"AUDIO")

            assignments = {
                str(source): {
                    "destinations": ["Italiano", "Latino/Veloci"],
                    "status": "Gestito",
                }
            }

            result = run_repertory_new_tracks_update(
                new_tracks_dir=new_tracks,
                split_repertory_dir=split,
                general_repertory_dir=general,
                smartphone_tablet_dir=smartphone,
                assignments_snapshot=assignments,
            )

            self.assertTrue((split / "Italiano" / "brano.mp3").is_file())
            self.assertTrue((split / "Latino" / "Veloci" / "brano.mp3").is_file())
            self.assertTrue((smartphone / "Italiano" / "brano.mp3").is_file())
            self.assertTrue((smartphone / "Latino" / "Veloci" / "brano.mp3").is_file())
            self.assertTrue((general / "brano.mp3").is_file())
            self.assertTrue((smartphone / "REPERTORIO_GENERALE_DA_MIXCREATOR" / "brano.mp3").is_file())
            self.assertEqual(result.copied_tracks, 1)

    def test_global_update_all_decision_is_reused_for_following_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            for folder in (new_tracks, split, general, smartphone):
                folder.mkdir(parents=True, exist_ok=True)

            source_one = new_tracks / "uno.mp3"
            source_two = new_tracks / "due.mp3"
            self._write_mp3(source_one, b"NEW_ONE")
            self._write_mp3(source_two, b"NEW_TWO")
            self._write_mp3(general / "uno.mp3", b"OLD_ONE")
            self._write_mp3(general / "due.mp3", b"OLD_TWO")

            assignments = {
                str(source_one): {"destinations": ["A"], "status": "Gestito"},
                str(source_two): {"destinations": ["A"], "status": "Gestito"},
            }

            calls = {"count": 0}

            def _decision(_payload):
                calls["count"] += 1
                return DECISION_UPDATE_AND_BYPASS_SESSION

            result = run_repertory_new_tracks_update(
                new_tracks_dir=new_tracks,
                split_repertory_dir=split,
                general_repertory_dir=general,
                smartphone_tablet_dir=smartphone,
                assignments_snapshot=assignments,
                decision_callback=_decision,
            )

            self.assertEqual(calls["count"], 1)
            self.assertEqual((general / "uno.mp3").read_bytes(), b"NEW_ONE")
            self.assertEqual((general / "due.mp3").read_bytes(), b"NEW_TWO")
            self.assertEqual(result.processed_tracks, 2)


class Rep003WorkerTests(unittest.TestCase):
    def test_worker_decision_handshake_and_completed_callback(self) -> None:
        completed_result = None
        errors = []

        def _fake_engine(**kwargs):
            decision_callback = kwargs["decision_callback"]
            selected = decision_callback(
                {
                    "source_path": "C:/new/song.mp3",
                    "existing_paths": ["C:/split/song.mp3"],
                }
            )
            self.assertEqual(selected, DECISION_SKIP_CURRENT)
            return Rep003UpdateResult(
                success=True,
                interrupted=False,
                error=None,
                total_tracks=1,
                processed_tracks=1,
                copied_tracks=0,
                updated_tracks=0,
                kept_tracks=1,
                skipped_tracks=0,
                error_tracks=0,
                elapsed_seconds=0.1,
                session_folder="C:/tmp/session",
                report_paths={"csv": "a.csv", "html": "a.html", "xlsx": "a.xlsx"},
                log_path="a.log",
                records=[],
            )

        w = worker.MP3RepertoryNewTracksWorker(
            on_completed=lambda payload: nonlocal_set("completed_result", payload),
            on_error=lambda message: errors.append(message),
            on_decision_required=lambda payload: w.submit_decision(payload["request_id"], DECISION_SKIP_CURRENT),
        )

        def nonlocal_set(name, value):
            nonlocal completed_result
            if name == "completed_result":
                completed_result = value

        with mock.patch.object(worker, "run_repertory_new_tracks_update", _fake_engine), mock.patch.object(
            worker.threading, "Thread", _ImmediateThread
        ):
            w.start(
                new_tracks_dir="C:/new",
                split_repertory_dir="C:/split",
                general_repertory_dir="C:/general",
                smartphone_tablet_dir="C:/smart",
                assignments_snapshot={"C:/new/song.mp3": {"status": "Gestito", "destinations": ["A"]}},
            )

        self.assertIsNotNone(completed_result)
        self.assertEqual(errors, [])

    def test_worker_emits_cancelled_when_engine_marks_interrupted(self) -> None:
        cancelled_messages = []

        def _fake_engine(**kwargs):
            _ = kwargs
            return Rep003UpdateResult(
                success=False,
                interrupted=True,
                error=None,
                total_tracks=1,
                processed_tracks=0,
                copied_tracks=0,
                updated_tracks=0,
                kept_tracks=0,
                skipped_tracks=0,
                error_tracks=0,
                elapsed_seconds=0.0,
                session_folder="C:/tmp/session",
                report_paths={"csv": "a.csv", "html": "a.html", "xlsx": "a.xlsx"},
                log_path="a.log",
                records=[],
            )

        w = worker.MP3RepertoryNewTracksWorker(
            on_cancelled=lambda message: cancelled_messages.append(message),
        )

        with mock.patch.object(worker, "run_repertory_new_tracks_update", _fake_engine), mock.patch.object(
            worker.threading, "Thread", _ImmediateThread
        ):
            w.start(
                new_tracks_dir="C:/new",
                split_repertory_dir="C:/split",
                general_repertory_dir="C:/general",
                smartphone_tablet_dir="C:/smart",
                assignments_snapshot={"C:/new/song.mp3": {"status": "Gestito", "destinations": ["A"]}},
            )

        self.assertEqual(cancelled_messages, ["Inserimento nuovi brani interrotto."])


if __name__ == "__main__":
    unittest.main()
