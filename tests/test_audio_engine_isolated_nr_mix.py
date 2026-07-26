# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from audio_engine import AudioEngine, AudioEngineError


class _FakeFFmpegManager:
    def __init__(self, durations: dict[str, float]) -> None:
        self.ffmpeg_path = Path("ffmpeg.exe")
        self._durations = durations

    def get_duration(self, file_path: Path) -> float:
        return float(self._durations.get(file_path.name, 74.0))

    @staticmethod
    def _creation_flags() -> int:
        return 0


class _FakePopen:
    def __init__(self, command: list[str], created_files: list[Path]) -> None:
        self.command = command
        self.stdout = iter(["out_time_ms=1000000\n", "progress=end\n"])
        self.stderr = iter([])
        self._created_files = created_files
        self._returncode = 0

        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"mp3")
        self._created_files.append(output_path)

    def wait(self) -> int:
        return self._returncode

    def poll(self) -> int:
        return self._returncode

    def terminate(self) -> None:
        self._returncode = -15


class AudioEngineIsolatedNrMixTests(unittest.TestCase):
    def _build_engine(self, durations: dict[str, float]) -> AudioEngine:
        engine = AudioEngine.__new__(AudioEngine)
        engine.ffmpeg = _FakeFFmpegManager(durations)
        engine._process = None
        return engine

    def _write_tracks(self, folder: Path, names: list[str]) -> None:
        for name in names:
            (folder / name).write_bytes(b"fake-mp3")

    def test_include_anyway_decodes_only_nr_track_to_temp_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input"
            output = Path(temp_dir) / "output"
            source.mkdir()
            output.mkdir()

            ordered_names = [
                "Collection.mp3",
                "Dove si balla.mp3",
                "La Voglia La Pazzia.mp3",
                "My Destiny.mp3",
                "Richard Clayderman - Ballade Pour Adeline.mp3",
                "Sex Bomb.mp3",
                "Una Notte a Napoli.mp3",
            ]
            self._write_tracks(source, ordered_names)

            durations = {name: 74.0 for name in ordered_names}
            engine = self._build_engine(durations)

            run_calls: list[list[str]] = []
            created_mix_outputs: list[Path] = []

            def fake_run(command, **_kwargs):
                run_calls.append(list(command))
                if "-acodec" in command:
                    wav_path = Path(command[-1])
                    wav_path.parent.mkdir(parents=True, exist_ok=True)
                    wav_path.write_bytes(b"wav")

                    class Result:
                        returncode = 0
                        stdout = ""
                        stderr = ""

                    return Result()

                class Result:
                    returncode = 0
                    stdout = ""
                    stderr = ""

                return Result()

            def fake_popen(command, **_kwargs):
                run_calls.append(list(command))
                return _FakePopen(list(command), created_mix_outputs)

            with mock.patch("audio_engine.subprocess.run", side_effect=fake_run), mock.patch(
                "audio_engine.subprocess.Popen", side_effect=fake_popen
            ):
                output_path, mix_report = engine.create_mix(
                    input_folder=source,
                    output_folder=output,
                    output_name="MixFinale",
                    clip_seconds=74,
                    crossfade_seconds=3,
                    fade_in_seconds=0,
                    fade_out_seconds=0,
                    bitrate="320k",
                    cut_mode="inizio",
                    random_order=False,
                    normalize_audio=False,
                    ordered_file_names=ordered_names,
                    isolated_input_names=["La Voglia La Pazzia.mp3"],
                )

            self.assertTrue(output_path.is_file())
            self.assertEqual(output_path, output / "MixFinale.mp3")
            self.assertEqual([item["file_name"] for item in mix_report["tracks"]], ordered_names)

            decode_calls = [call for call in run_calls if "-acodec" in call]
            self.assertEqual(len(decode_calls), 1)
            decode_command = decode_calls[0]
            self.assertIn(str(source / "La Voglia La Pazzia.mp3"), decode_command)

            temp_wav_path = Path(decode_command[-1])
            self.assertFalse(temp_wav_path.exists())

            mix_calls = [call for call in run_calls if "-filter_complex" in call]
            self.assertEqual(len(mix_calls), 1)
            mix_command = mix_calls[0]
            self.assertIn(str(temp_wav_path), mix_command)
            self.assertNotIn(str(source / "La Voglia La Pazzia.mp3"), mix_command)

            self.assertTrue(created_mix_outputs)
            self.assertTrue(created_mix_outputs[0].exists())

    def test_include_anyway_decode_failure_aborts_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input"
            output = Path(temp_dir) / "output"
            source.mkdir()
            output.mkdir()

            ordered_names = [
                "Collection.mp3",
                "La Voglia La Pazzia.mp3",
                "My Destiny.mp3",
            ]
            self._write_tracks(source, ordered_names)

            durations = {name: 74.0 for name in ordered_names}
            engine = self._build_engine(durations)

            def fake_run(command, **_kwargs):
                class Result:
                    pass

                result = Result()
                if "-acodec" in command:
                    result.returncode = 1
                    result.stdout = ""
                    result.stderr = "decoder failed"
                    return result

                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
                return result

            with mock.patch("audio_engine.subprocess.run", side_effect=fake_run), mock.patch(
                "audio_engine.subprocess.Popen"
            ) as popen_mock:
                with self.assertRaises(AudioEngineError) as ctx:
                    engine.create_mix(
                        input_folder=source,
                        output_folder=output,
                        output_name="MixFinale",
                        clip_seconds=74,
                        crossfade_seconds=3,
                        fade_in_seconds=0,
                        fade_out_seconds=0,
                        bitrate="320k",
                        cut_mode="inizio",
                        random_order=False,
                        normalize_audio=False,
                        ordered_file_names=ordered_names,
                        isolated_input_names=["La Voglia La Pazzia.mp3"],
                    )

            self.assertIn("La Voglia La Pazzia.mp3", str(ctx.exception))
            self.assertFalse((output / "MixFinale.mp3").exists())
            popen_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()