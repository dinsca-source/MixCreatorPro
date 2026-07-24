# -*- coding: utf-8 -*-
"""
MixCreator PRO
ffmpeg_manager.py - Versione 1.0

Gestione centralizzata di FFmpeg e FFprobe.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional


class FFmpegError(RuntimeError):
    """Errore generato dalle operazioni FFmpeg/FFprobe."""


class FFmpegManager:
    def __init__(self, base_dir: Optional[str | Path] = None) -> None:
        self.base_dir = Path(base_dir or Path(__file__).resolve().parent)
        self.ffmpeg_dir = self.base_dir / "ffmpeg"
        self.ffmpeg_path = self.ffmpeg_dir / "ffmpeg.exe"
        self.ffprobe_path = self.ffmpeg_dir / "ffprobe.exe"

    def validate(self) -> None:
        """Verifica che gli eseguibili necessari siano presenti e funzionanti."""
        missing = []

        if not self.ffmpeg_path.is_file():
            missing.append(str(self.ffmpeg_path))

        if not self.ffprobe_path.is_file():
            missing.append(str(self.ffprobe_path))

        if missing:
            raise FFmpegError(
                "File FFmpeg mancanti:\n" + "\n".join(missing)
            )

        self._test_executable(self.ffmpeg_path, "FFmpeg")
        self._test_executable(self.ffprobe_path, "FFprobe")

    def _test_executable(self, executable: Path, name: str) -> None:
        try:
            result = subprocess.run(
                [str(executable), "-version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                creationflags=self._creation_flags()
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise FFmpegError(
                f"{name} non può essere avviato:\n{error}"
            ) from error

        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            raise FFmpegError(
                f"{name} ha restituito un errore:\n{message}"
            )

        if not result.stdout.strip():
            raise FFmpegError(
                f"{name} non ha restituito informazioni di versione."
            )

    def get_duration(self, mp3_path: str | Path) -> float:
        """
        Restituisce la durata del file MP3 in secondi.
        """
        source = Path(mp3_path)

        if not source.is_file():
            raise FFmpegError(f"File non trovato:\n{source}")

        command = [
            str(self.ffprobe_path),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_entries",
            "format=duration",
            str(source)
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                creationflags=self._creation_flags()
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise FFmpegError(
                f"Impossibile analizzare il file:\n{source.name}\n{error}"
            ) from error

        if result.returncode != 0:
            message = result.stderr.strip() or "Errore sconosciuto di FFprobe."
            raise FFmpegError(
                f"FFprobe non riesce a leggere:\n{source.name}\n{message}"
            )

        try:
            data = json.loads(result.stdout)
            duration = float(data["format"]["duration"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise FFmpegError(
                f"Durata non disponibile per:\n{source.name}"
            ) from error

        if duration <= 0:
            raise FFmpegError(
                f"Durata non valida per:\n{source.name}"
            )

        return duration

    def get_version(self) -> str:
        """Restituisce la prima riga della versione di FFmpeg."""
        self.validate()

        result = subprocess.run(
            [str(self.ffmpeg_path), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=self._creation_flags()
        )

        return result.stdout.splitlines()[0].strip()

    @staticmethod
    def _creation_flags() -> int:
        """
        Evita l'apertura di finestre nere dei processi FFmpeg su Windows.
        """
        if os.name == "nt":
            return subprocess.CREATE_NO_WINDOW
        return 0


def test_ffmpeg() -> str:
    """
    Funzione di test rapido utilizzabile da terminale.
    """
    manager = FFmpegManager()
    manager.validate()
    return manager.get_version()


if __name__ == "__main__":
    try:
        print(test_ffmpeg())
        print("FFmpeg e FFprobe funzionano correttamente.")
    except FFmpegError as error:
        print(f"ERRORE:\n{error}")
