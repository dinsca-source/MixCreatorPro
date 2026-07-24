# -*- coding: utf-8 -*-
"""
MixCreator PRO
audio_engine.py - Versione 1.3.04

Novità:
- avanzamento reale durante analisi ed esportazione;
- annullamento sicuro dell'elaborazione;
- durata finale stimata;
- gestione più robusta del processo FFmpeg.
"""

from __future__ import annotations

import os
import random
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from ffmpeg_manager import FFmpegError, FFmpegManager
from clip_info import ClipInfo

ProgressCallback = Callable[[int, int, str], None]


class AudioEngineError(RuntimeError):
    """Errore generato durante la creazione del mix."""


class AudioEngineCancelled(AudioEngineError):
    """Elaborazione annullata dall'utente."""


class AudioEngine:
    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.ffmpeg = FFmpegManager(base_dir)
        self.ffmpeg.validate()
        self._process: Optional[subprocess.Popen] = None

    def find_mp3_files(
        self,
        folder: str | Path,
        random_order: bool = False,
        ordered_file_names: Optional[list[str]] = None
    ) -> list[Path]:
        source_folder = Path(folder)

        if not source_folder.is_dir():
            raise AudioEngineError(
                f"Cartella MP3 non valida:\n{source_folder}"
            )

        discovered: list[Path] = [
            item
            for item in source_folder.rglob("*")
            if item.is_file() and item.suffix.lower() == ".mp3"
        ]

        if not discovered:
            raise AudioEngineError(
                "Nessun file MP3 trovato nella cartella selezionata."
            )

        available_by_relative = {
            item.relative_to(source_folder).as_posix(): item
            for item in discovered
        }

        available_by_name: dict[str, list[Path]] = {}
        for item in discovered:
            available_by_name.setdefault(item.name, []).append(item)

        if ordered_file_names is not None:
            files: list[Path] = []
            for file_name in ordered_file_names:
                normalized = Path(file_name).as_posix()

                if normalized in available_by_relative:
                    files.append(available_by_relative[normalized])
                    continue

                legacy_name = Path(normalized).name
                candidates = available_by_name.get(legacy_name, [])
                if len(candidates) == 1:
                    files.append(candidates[0])

            if not files:
                raise AudioEngineError(
                    "La lista del mix è vuota. "
                    "Inserisci almeno un brano."
                )
        else:
            files = sorted(
                discovered,
                key=lambda item: item.relative_to(source_folder).as_posix().lower()
            )

        if random_order:
            random.shuffle(files)

        return files

    def create_mix(
        self,
        input_folder: str | Path,
        output_folder: str | Path,
        output_name: str,
        clip_seconds: int,
        crossfade_seconds: int,
        fade_in_seconds: int,
        fade_out_seconds: int,
        bitrate: str,
        cut_mode: str = "inizio",
        random_order: bool = False,
        normalize_audio: bool = True,
        ordered_file_names: Optional[list[str]] = None,
        custom_clips: Optional[dict[str, ClipInfo]] = None,
        previous_resolved_clips: Optional[dict[str, dict[str, Any]]] = None,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> tuple[Path, dict[str, Any]]:
        source_folder = Path(input_folder)
        files = self.find_mp3_files(
            source_folder,
            random_order=random_order,
            ordered_file_names=ordered_file_names
        )

        output_dir = Path(output_folder)
        output_dir.mkdir(parents=True, exist_ok=True)

        clean_name = self._sanitize_output_name(output_name)
        output_path = output_dir / f"{clean_name}.mp3"

        if output_path.exists():
            try:
                output_path.unlink()
            except OSError as error:
                raise AudioEngineError(
                    f"Impossibile sovrascrivere il file:\n{output_path}\n{error}"
                ) from error

        clip_seconds = max(1, int(clip_seconds))
        crossfade_seconds = max(0, int(crossfade_seconds))
        fade_in_seconds = max(0, int(fade_in_seconds))
        fade_out_seconds = max(0, int(fade_out_seconds))

        track_data, reuse_summary = self._prepare_tracks(
            files=files,
            source_folder=source_folder,
            clip_seconds=clip_seconds,
            cut_mode=cut_mode,
            custom_clips=custom_clips,
            previous_resolved_clips=previous_resolved_clips,
            progress_callback=progress_callback,
            cancel_event=cancel_event
        )

        minimum_clip = min(item["duration"] for item in track_data)

        if crossfade_seconds >= minimum_clip:
            crossfade_seconds = max(0, int(minimum_clip) - 1)

        if fade_in_seconds >= minimum_clip:
            fade_in_seconds = max(0, int(minimum_clip) - 1)

        if fade_out_seconds >= minimum_clip:
            fade_out_seconds = max(0, int(minimum_clip) - 1)

        total_output_duration = max(
            0.1,
            sum(float(item["duration"]) for item in track_data)
            - (crossfade_seconds * max(0, len(track_data) - 1))
        )

        command = self._build_ffmpeg_command(
            track_data=track_data,
            output_path=output_path,
            crossfade_seconds=crossfade_seconds,
            fade_in_seconds=fade_in_seconds,
            fade_out_seconds=fade_out_seconds,
            bitrate=bitrate,
            normalize_audio=normalize_audio
        )

        self._notify(
            progress_callback,
            0,
            100,
            "Avvio creazione del mix..."
        )

        self._run_ffmpeg_with_progress(
            command=command,
            output_path=output_path,
            total_duration=total_output_duration,
            progress_callback=progress_callback,
            cancel_event=cancel_event
        )

        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise AudioEngineError(
                "FFmpeg ha terminato senza creare un file MP3 valido."
            )

        self._notify(
            progress_callback,
            100,
            100,
            f"Mix completato: {output_path.name}"
        )

        track_report: list[dict[str, Any]] = []
        mix_cursor_seconds = 0.0
        crossfade_ms = max(0, int(round(float(crossfade_seconds) * 1000)))
        fade_in_ms = max(0, int(round(float(fade_in_seconds) * 1000)))
        fade_out_ms = max(0, int(round(float(fade_out_seconds) * 1000)))

        for index, item in enumerate(track_data, start=1):
            source_start_ms = max(0, int(round(float(item["start"]) * 1000)))
            clip_duration_ms = max(1, int(round(float(item["duration"]) * 1000)))
            source_end_ms = source_start_ms + clip_duration_ms

            mix_start_ms = max(0, int(round(mix_cursor_seconds * 1000)))
            mix_end_seconds = mix_cursor_seconds + float(item["duration"])
            mix_end_ms = max(mix_start_ms + 1, int(round(mix_end_seconds * 1000)))

            crossfade_in_ms = crossfade_ms if index > 1 else 0
            crossfade_out_ms = crossfade_ms if index < len(track_data) else 0

            track_report.append(
                {
                    "file_name": str(item["file_name"]),
                    "source_path": str(item["path"]),
                    "start_ms": source_start_ms,
                    "duration_ms": clip_duration_ms,
                    "source_start_ms": source_start_ms,
                    "source_end_ms": source_end_ms,
                    "clip_duration_ms": clip_duration_ms,
                    "mix_start_ms": mix_start_ms,
                    "mix_end_ms": mix_end_ms,
                    "crossfade_in_ms": crossfade_in_ms,
                    "crossfade_out_ms": crossfade_out_ms,
                    "fade_in_ms": fade_in_ms,
                    "fade_out_ms": fade_out_ms,
                    "mix_order": index,
                    "source_mode": str(item.get("source_mode", "calculated")),
                    "manual_clip": bool(item.get("manual_clip", False)),
                }
            )

            mix_cursor_seconds = max(0.0, mix_end_seconds - float(crossfade_seconds))

        mix_report = {
            "tracks": track_report,
            "reuse_summary": reuse_summary,
            "reuse_enabled": previous_resolved_clips is not None,
        }

        return output_path, mix_report

    def extract_song_clips(
        self,
        *,
        tracks_data: list[dict[str, Any]],
        output_folder: str | Path,
        bitrate: str,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> dict[str, Any]:
        output_dir = Path(output_folder)
        output_dir.mkdir(parents=True, exist_ok=True)

        total = len(tracks_data)
        if total <= 0:
            raise AudioEngineError("Nessuna clip disponibile per l'estrazione.")

        errors: list[str] = []
        extracted_files: list[str] = []

        for index, item in enumerate(tracks_data, start=1):
            self._check_cancel(cancel_event)

            file_name = str(item.get("file_name", f"track_{index:03d}.mp3"))
            self._notify(
                progress_callback,
                index - 1,
                total,
                f"Preparazione {index}/{total}: {file_name}",
            )

            source_path = Path(str(item.get("source_path", ""))).expanduser()
            if not source_path.is_file():
                errors.append(f"Sorgente non trovata: {source_path}")
                continue

            try:
                source_start_ms = int(item.get("source_start_ms"))
                source_end_ms = int(item.get("source_end_ms"))
                clip_duration_ms = int(item.get("clip_duration_ms"))
                fade_in_ms = max(0, int(item.get("fade_in_ms", 0)))
                fade_out_ms = max(0, int(item.get("fade_out_ms", 0)))
            except (TypeError, ValueError):
                errors.append(f"Dati temporali non validi per: {file_name}")
                continue

            if source_start_ms < 0 or source_end_ms <= source_start_ms or clip_duration_ms <= 0:
                errors.append(f"Intervallo clip non valido per: {file_name}")
                continue

            output_name = f"{index:03d} - {Path(file_name).name}"
            output_path = self._unique_output_path(output_dir / output_name)

            command: list[str] = [
                str(self.ffmpeg.ffmpeg_path),
                "-hide_banner",
                "-y",
                "-ss",
                f"{source_start_ms / 1000.0:.3f}",
                "-to",
                f"{source_end_ms / 1000.0:.3f}",
                "-i",
                str(source_path),
                "-vn",
            ]

            local_fade_in_ms = min(fade_in_ms, max(0, clip_duration_ms - 100))
            local_fade_out_ms = min(fade_out_ms, max(0, clip_duration_ms - 100))
            fade_filters: list[str] = []

            if local_fade_in_ms > 0:
                fade_filters.append(f"afade=t=in:st=0:d={local_fade_in_ms / 1000.0:.3f}")

            if local_fade_out_ms > 0:
                fade_out_start_seconds = max(0.0, (clip_duration_ms - local_fade_out_ms) / 1000.0)
                fade_filters.append(
                    "afade=t=out:"
                    f"st={fade_out_start_seconds:.3f}:"
                    f"d={local_fade_out_ms / 1000.0:.3f}"
                )

            if fade_filters:
                command.extend(["-af", ",".join(fade_filters)])

            command.extend(
                [
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    self._validate_bitrate(bitrate),
                    "-id3v2_version",
                    "3",
                    str(output_path),
                ]
            )

            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=self._creation_flags(),
                )
            except OSError as error:
                errors.append(f"Errore avvio FFmpeg per {file_name}: {error}")
                continue

            if result.returncode != 0:
                error_message = (result.stderr or result.stdout or "Errore sconosciuto FFmpeg").strip()
                errors.append(f"Estrazione fallita per {file_name}: {error_message[-500:]}")
                try:
                    if output_path.exists():
                        output_path.unlink()
                except OSError:
                    pass
                continue

            extracted_files.append(str(output_path))
            self._notify(
                progress_callback,
                index,
                total,
                f"Estratta {index}/{total}: {output_path.name}",
            )

        return {
            "output_folder": str(output_dir),
            "total": total,
            "extracted": len(extracted_files),
            "errors": errors,
            "files": extracted_files,
        }

    def cancel(self) -> None:
        """
        Termina il processo FFmpeg attualmente in esecuzione.
        """
        process = self._process

        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def _prepare_tracks(
        self,
        files: Iterable[Path],
        source_folder: Path,
        clip_seconds: int,
        cut_mode: str,
        custom_clips: Optional[dict[str, ClipInfo]],
        previous_resolved_clips: Optional[dict[str, dict[str, Any]]],
        progress_callback: Optional[ProgressCallback],
        cancel_event: Optional[threading.Event]
    ) -> tuple[list[dict], dict[str, int]]:
        prepared: list[dict] = []
        file_list = list(files)
        total = len(file_list)
        reuse_summary = {
            "reused": 0,
            "recalculated": 0,
            "new": 0,
        }

        previous_map = previous_resolved_clips or {}

        for index, file_path in enumerate(file_list, start=1):
            self._check_cancel(cancel_event)

            self._notify(
                progress_callback,
                index,
                total,
                f"Analisi {index}/{total}: {file_path.name}"
            )

            try:
                full_duration = self.ffmpeg.get_duration(file_path)
            except FFmpegError as error:
                raise AudioEngineError(str(error)) from error

            clip_info = None
            if custom_clips is not None:
                clip_info = custom_clips.get(file_path.name)

            source_mode = "calculated"
            manual_clip = False
            if clip_info is not None and clip_info.use_custom_clip:
                try:
                    start, duration = clip_info.resolve_segment(
                        int(round(full_duration * 1000))
                    )
                except ValueError as error:
                    raise AudioEngineError(
                        f"Clip personalizzata non valida per {file_path.name}: {error}"
                    ) from error
                source_mode = "manual"
                manual_clip = True
            else:
                relative_name = file_path.relative_to(source_folder).as_posix()
                previous_item = previous_map.get(relative_name)
                reused = False
                previous_was_present = previous_item is not None

                if previous_item is not None:
                    try:
                        previous_start_ms = int(previous_item.get("start_ms", -1))
                        previous_duration_ms = int(previous_item.get("duration_ms", -1))
                    except (TypeError, ValueError):
                        previous_start_ms = -1
                        previous_duration_ms = -1

                    previous_start = previous_start_ms / 1000.0
                    previous_duration = previous_duration_ms / 1000.0
                    remaining = max(0.0, full_duration - previous_start)

                    if (
                        previous_start >= 0.0
                        and previous_duration > 0.0
                        and previous_start < full_duration
                        and previous_duration <= remaining
                    ):
                        start = previous_start
                        duration = previous_duration
                        reused = True

                if reused:
                    source_mode = "previous"
                    reuse_summary["reused"] += 1
                else:
                    start, duration = self._calculate_segment(
                        full_duration=full_duration,
                        clip_seconds=clip_seconds,
                        cut_mode=cut_mode
                    )
                    source_mode = "calculated"
                    if previous_was_present:
                        reuse_summary["recalculated"] += 1
                    else:
                        reuse_summary["new"] += 1

            prepared.append(
                {
                    "path": file_path,
                    "start": start,
                    "duration": duration,
                    "file_name": file_path.relative_to(source_folder).as_posix(),
                    "source_mode": source_mode,
                    "manual_clip": manual_clip,
                }
            )

        return prepared, reuse_summary

    def _run_ffmpeg_with_progress(
        self,
        command: list[str],
        output_path: Path,
        total_duration: float,
        progress_callback: Optional[ProgressCallback],
        cancel_event: Optional[threading.Event]
    ) -> None:
        # Inserisce il canale di avanzamento prima del file di output.
        progress_command = command[:-1] + [
            "-progress",
            "pipe:1",
            "-nostats",
            command[-1]
        ]

        try:
            self._process = subprocess.Popen(
                progress_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=self._creation_flags()
            )
        except OSError as error:
            raise AudioEngineError(
                f"Impossibile avviare FFmpeg:\n{error}"
            ) from error

        stderr_lines: list[str] = []

        def read_stderr() -> None:
            if self._process and self._process.stderr:
                for line in self._process.stderr:
                    stderr_lines.append(line)

        stderr_thread = threading.Thread(
            target=read_stderr,
            daemon=True
        )
        stderr_thread.start()

        assert self._process.stdout is not None

        try:
            for raw_line in self._process.stdout:
                self._check_cancel(cancel_event)

                line = raw_line.strip()

                if line.startswith("out_time_ms="):
                    try:
                        out_time_microseconds = int(line.split("=", 1)[1])
                        elapsed_seconds = out_time_microseconds / 1_000_000
                        percent = int(
                            max(
                                0,
                                min(
                                    100,
                                    (elapsed_seconds / total_duration) * 100
                                )
                            )
                        )
                        self._notify(
                            progress_callback,
                            percent,
                            100,
                            f"Creazione mix: {percent}%"
                        )
                    except ValueError:
                        pass

            return_code = self._process.wait()
            stderr_thread.join(timeout=1)

        finally:
            self._process = None

        if cancel_event is not None and cancel_event.is_set():
            self._delete_partial_output(output_path)
            raise AudioEngineCancelled(
                "Creazione del mix annullata dall'utente."
            )

        if return_code != 0:
            self._delete_partial_output(output_path)
            message = "".join(stderr_lines).strip()
            raise AudioEngineError(
                "FFmpeg non è riuscito a creare il mix.\n\n"
                + message[-4000:]
            )

    def _calculate_segment(
        self,
        full_duration: float,
        clip_seconds: int,
        cut_mode: str
    ) -> tuple[float, float]:
        if cut_mode == "intero":
            if full_duration <= 0:
                raise AudioEngineError(
                    "È stato trovato un MP3 con durata nulla."
                )

            return 0.0, float(full_duration)

        duration = min(float(clip_seconds), full_duration)

        if duration <= 0:
            raise AudioEngineError(
                "È stato trovato un MP3 con durata nulla."
            )

        available_start = max(0.0, full_duration - duration)

        if cut_mode == "centro":
            start = available_start / 2.0
        elif cut_mode == "fine":
            start = available_start
        elif cut_mode == "casuale":
            start = (
                random.uniform(0.0, available_start)
                if available_start > 0
                else 0.0
            )
        elif cut_mode == "intro_fine":
            start = available_start / 2.0
        else:
            start = 0.0

        return start, duration

    def _build_ffmpeg_command(
        self,
        track_data: list[dict],
        output_path: Path,
        crossfade_seconds: int,
        fade_in_seconds: int,
        fade_out_seconds: int,
        bitrate: str,
        normalize_audio: bool
    ) -> list[str]:
        command: list[str] = [
            str(self.ffmpeg.ffmpeg_path),
            "-hide_banner",
            "-y"
        ]

        for item in track_data:
            command.extend(
                [
                    "-ss",
                    f"{item['start']:.3f}",
                    "-t",
                    f"{item['duration']:.3f}",
                    "-i",
                    str(item["path"])
                ]
            )

        filters: list[str] = []

        for index, item in enumerate(track_data):
            duration = float(item["duration"])

            track_filters = [
                "aresample=44100",
                "aformat=sample_fmts=fltp:channel_layouts=stereo",
                "asetpts=PTS-STARTPTS"
            ]

            if fade_in_seconds > 0:
                local_fade_in = min(
                    float(fade_in_seconds),
                    max(0.0, duration - 0.1)
                )
                if local_fade_in > 0:
                    track_filters.append(
                        f"afade=t=in:st=0:d={local_fade_in:.3f}"
                    )

            if fade_out_seconds > 0:
                local_fade_out = min(
                    float(fade_out_seconds),
                    max(0.0, duration - 0.1)
                )
                fade_start = max(0.0, duration - local_fade_out)

                if local_fade_out > 0:
                    track_filters.append(
                        f"afade=t=out:st={fade_start:.3f}:d={local_fade_out:.3f}"
                    )

            filters.append(
                f"[{index}:a]{','.join(track_filters)}[a{index}]"
            )

        if len(track_data) == 1:
            final_label = "a0"

        elif crossfade_seconds > 0:
            previous_label = "a0"

            for index in range(1, len(track_data)):
                next_label = f"mix{index}"

                filters.append(
                    f"[{previous_label}][a{index}]"
                    f"acrossfade=d={crossfade_seconds}:c1=tri:c2=tri"
                    f"[{next_label}]"
                )
                previous_label = next_label

            final_label = previous_label

        else:
            concat_inputs = "".join(
                f"[a{index}]"
                for index in range(len(track_data))
            )

            filters.append(
                f"{concat_inputs}"
                f"concat=n={len(track_data)}:v=0:a=1"
                f"[joined]"
            )

            final_label = "joined"

        if normalize_audio:
            filters.append(
                f"[{final_label}]"
                "loudnorm=I=-14:TP=-1.5:LRA=11"
                "[final]"
            )
            final_label = "final"

        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                f"[{final_label}]",
                "-vn",
                "-c:a",
                "libmp3lame",
                "-b:a",
                self._validate_bitrate(bitrate),
                "-id3v2_version",
                "3",
                str(output_path)
            ]
        )

        return command

    @staticmethod
    def _sanitize_output_name(name: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        cleaned = "".join(
            "_" if character in invalid_chars else character
            for character in str(name).strip()
        )
        cleaned = cleaned.rstrip(". ")
        return cleaned or "MixFinale"

    @staticmethod
    def _validate_bitrate(bitrate: str) -> str:
        valid = {"128k", "192k", "256k", "320k"}
        return bitrate if bitrate in valid else "320k"

    @staticmethod
    def _notify(
        callback: Optional[ProgressCallback],
        current: int,
        total: int,
        message: str
    ) -> None:
        if callback is not None:
            callback(current, total, message)

    @staticmethod
    def _check_cancel(
        cancel_event: Optional[threading.Event]
    ) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise AudioEngineCancelled(
                "Creazione del mix annullata dall'utente."
            )

    @staticmethod
    def _delete_partial_output(output_path: Path) -> None:
        try:
            if output_path.exists():
                output_path.unlink()
        except OSError:
            pass

    @staticmethod
    def _creation_flags() -> int:
        if os.name == "nt":
            return subprocess.CREATE_NO_WINDOW
        return 0

    @staticmethod
    def _unique_output_path(path: Path) -> Path:
        if not path.exists():
            return path

        stem = path.stem
        suffix = path.suffix or ".mp3"
        for index in range(1, 1000):
            candidate = path.with_name(f"{stem} ({index}){suffix}")
            if not candidate.exists():
                return candidate

        raise AudioEngineError(
            f"Impossibile creare un nome file univoco per:\n{path.name}"
        )


def test_engine() -> None:
    print("AudioEngine 1.1 caricato correttamente.")
    print("Avanzamento reale e annullamento disponibili.")


if __name__ == "__main__":
    test_engine()
