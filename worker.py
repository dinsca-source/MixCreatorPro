# -*- coding: utf-8 -*-
"""
MixCreator PRO
worker.py - Versione 1.2.04

Novità:
- avanzamento reale;
- annullamento sicuro;
- gestione dedicata dell'evento di cancellazione.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Optional

from audio_engine import (
    AudioEngine,
    AudioEngineCancelled,
    AudioEngineError
)
from clip_info import ClipInfo
from mp3_diagnostics import (
    MP3DiagnosticsCancelled,
    MP3DiagnosticsEngine,
    MP3DiagnosticsError,
)


ProgressCallback = Callable[[int, int, str], None]
CompletedCallback = Callable[[Path, dict[str, Any]], None]
ErrorCallback = Callable[[str], None]
CancelledCallback = Callable[[str], None]
ExtractCompletedCallback = Callable[[dict[str, Any]], None]
DiagnosticsCompletedCallback = Callable[[dict[str, Any]], None]


class MixWorker:
    def __init__(
        self,
        on_progress: Optional[ProgressCallback] = None,
        on_completed: Optional[CompletedCallback] = None,
        on_error: Optional[ErrorCallback] = None,
        on_cancelled: Optional[CancelledCallback] = None
    ) -> None:
        self.on_progress = on_progress
        self.on_completed = on_completed
        self.on_error = on_error
        self.on_cancelled = on_cancelled

        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._cancel_event = threading.Event()
        self._engine: Optional[AudioEngine] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(
        self,
        *,
        input_folder: str,
        output_folder: str,
        output_name: str,
        clip_seconds: int,
        crossfade_seconds: int,
        fade_in_seconds: int,
        fade_out_seconds: int,
        bitrate: str,
        cut_mode: str,
        random_order: bool,
        normalize_audio: bool,
        ordered_file_names: Optional[list[str]] = None,
        custom_clips: Optional[dict[str, "ClipInfo"]] = None,
        previous_resolved_clips: Optional[dict[str, dict[str, Any]]] = None,
    ) -> None:
        if self._running:
            raise RuntimeError("È già in corso una creazione del mix.")

        self._cancel_event.clear()
        self._running = True

        self._thread = threading.Thread(
            target=self._run,
            kwargs={
                "input_folder": input_folder,
                "output_folder": output_folder,
                "output_name": output_name,
                "clip_seconds": clip_seconds,
                "crossfade_seconds": crossfade_seconds,
                "fade_in_seconds": fade_in_seconds,
                "fade_out_seconds": fade_out_seconds,
                "bitrate": bitrate,
                "cut_mode": cut_mode,
                "random_order": random_order,
                "normalize_audio": normalize_audio,
                "ordered_file_names": ordered_file_names,
                "custom_clips": custom_clips,
                "previous_resolved_clips": previous_resolved_clips,
            },
            daemon=True
        )

        self._thread.start()

    def cancel(self) -> None:
        """
        Richiede l'annullamento dell'operazione in corso.
        """
        if not self._running:
            return

        self._cancel_event.set()

        if self._engine is not None:
            self._engine.cancel()

    def _run(self, **kwargs) -> None:
        try:
            self._emit_progress(
                0,
                100,
                "Inizializzazione motore audio..."
            )

            self._engine = AudioEngine()

            output_path, mix_report = self._engine.create_mix(
                progress_callback=self._emit_progress,
                cancel_event=self._cancel_event,
                **kwargs
            )

        except AudioEngineCancelled as error:
            self._emit_cancelled(str(error))

        except (AudioEngineError, RuntimeError, ValueError, OSError) as error:
            self._emit_error(str(error))

        except Exception as error:
            self._emit_error(
                f"Errore imprevisto durante la creazione del mix:\n{error}"
            )

        else:
            self._emit_completed(output_path, mix_report)

        finally:
            self._engine = None
            self._running = False
            self._cancel_event.clear()

    def _emit_progress(
        self,
        current: int,
        total: int,
        message: str
    ) -> None:
        if self.on_progress is not None:
            self.on_progress(current, total, message)

    def _emit_completed(self, output_path: Path, mix_report: dict[str, Any]) -> None:
        if self.on_completed is not None:
            self.on_completed(output_path, mix_report)

    def _emit_error(self, message: str) -> None:
        if self.on_error is not None:
            self.on_error(message)

    def _emit_cancelled(self, message: str) -> None:
        if self.on_cancelled is not None:
            self.on_cancelled(message)


class SongExtractionWorker:
    def __init__(
        self,
        on_progress: Optional[ProgressCallback] = None,
        on_completed: Optional[ExtractCompletedCallback] = None,
        on_error: Optional[ErrorCallback] = None,
        on_cancelled: Optional[CancelledCallback] = None,
    ) -> None:
        self.on_progress = on_progress
        self.on_completed = on_completed
        self.on_error = on_error
        self.on_cancelled = on_cancelled

        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._cancel_event = threading.Event()
        self._engine: Optional[AudioEngine] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(
        self,
        *,
        tracks_data: list[dict[str, Any]],
        output_folder: str,
        bitrate: str,
    ) -> None:
        if self._running:
            raise RuntimeError("È già in corso un'estrazione song.")

        self._cancel_event.clear()
        self._running = True

        self._thread = threading.Thread(
            target=self._run,
            kwargs={
                "tracks_data": tracks_data,
                "output_folder": output_folder,
                "bitrate": bitrate,
            },
            daemon=True,
        )
        self._thread.start()

    def cancel(self) -> None:
        if not self._running:
            return
        self._cancel_event.set()
        if self._engine is not None:
            self._engine.cancel()

    def _run(self, **kwargs) -> None:
        try:
            self._emit_progress(0, 100, "Inizializzazione estrazione song...")

            self._engine = AudioEngine()
            summary = self._engine.extract_song_clips(
                progress_callback=self._emit_progress,
                cancel_event=self._cancel_event,
                **kwargs,
            )
        except AudioEngineCancelled as error:
            self._emit_cancelled(str(error))
        except (AudioEngineError, RuntimeError, ValueError, OSError) as error:
            self._emit_error(str(error))
        except Exception as error:
            self._emit_error(
                "Errore imprevisto durante l'estrazione song:\n"
                f"{error}"
            )
        else:
            self._emit_completed(summary)
        finally:
            self._engine = None
            self._running = False
            self._cancel_event.clear()

    def _emit_progress(self, current: int, total: int, message: str) -> None:
        if self.on_progress is not None:
            self.on_progress(current, total, message)

    def _emit_completed(self, summary: dict[str, Any]) -> None:
        if self.on_completed is not None:
            self.on_completed(summary)

    def _emit_error(self, message: str) -> None:
        if self.on_error is not None:
            self.on_error(message)

    def _emit_cancelled(self, message: str) -> None:
        if self.on_cancelled is not None:
            self.on_cancelled(message)


class MP3DiagnosticsWorker:
    def __init__(
        self,
        on_progress: Optional[ProgressCallback] = None,
        on_completed: Optional[DiagnosticsCompletedCallback] = None,
        on_error: Optional[ErrorCallback] = None,
        on_cancelled: Optional[CancelledCallback] = None,
    ) -> None:
        self.on_progress = on_progress
        self.on_completed = on_completed
        self.on_error = on_error
        self.on_cancelled = on_cancelled

        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._cancel_event = threading.Event()
        self._engine: Optional[MP3DiagnosticsEngine] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(
        self,
        *,
        input_folder: str,
        include_subfolders: bool,
        output_folder: str,
        repair_mode: bool,
        placement_mode: str,
        selected_input_files: Optional[list[Path]] = None,
    ) -> None:
        if self._running:
            raise RuntimeError("È già in corso una diagnostica MP3.")

        self._cancel_event.clear()
        self._running = True

        self._thread = threading.Thread(
            target=self._run,
            kwargs={
                "input_folder": input_folder,
                "include_subfolders": include_subfolders,
                "output_folder": output_folder,
                "repair_mode": repair_mode,
                "placement_mode": placement_mode,
                "selected_input_files": selected_input_files,
            },
            daemon=True,
        )
        self._thread.start()

    def cancel(self) -> None:
        if not self._running:
            return
        self._cancel_event.set()

    def _run(self, **kwargs) -> None:
        try:
            self._emit_progress(0, 100, "Inizializzazione diagnostica MP3...")

            self._engine = MP3DiagnosticsEngine()
            summary = self._engine.run_diagnostics(
                progress_callback=self._emit_progress,
                cancel_event=self._cancel_event,
                **kwargs,
            )
        except MP3DiagnosticsCancelled as error:
            self._emit_cancelled(str(error))
        except (MP3DiagnosticsError, RuntimeError, ValueError, OSError) as error:
            self._emit_error(str(error))
        except Exception as error:
            self._emit_error(
                "Errore imprevisto durante la diagnostica MP3:\n"
                f"{error}"
            )
        else:
            self._emit_completed(summary)
        finally:
            self._engine = None
            self._running = False
            self._cancel_event.clear()

    def _emit_progress(self, current: int, total: int, message: str) -> None:
        if self.on_progress is not None:
            self.on_progress(current, total, message)

    def _emit_completed(self, summary: dict[str, Any]) -> None:
        if self.on_completed is not None:
            self.on_completed(summary)

    def _emit_error(self, message: str) -> None:
        if self.on_error is not None:
            self.on_error(message)

    def _emit_cancelled(self, message: str) -> None:
        if self.on_cancelled is not None:
            self.on_cancelled(message)


def test_worker() -> None:
    worker = MixWorker()
    print("MixWorker 1.1 caricato correttamente.")
    print("Avanzamento reale e annullamento disponibili.")
    print(f"Worker in esecuzione: {worker.is_running}")


if __name__ == "__main__":
    test_worker()
