from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median


@dataclass
class AdaptiveTimeEstimator:
    initial_seconds_per_unit: float = 8.0
    sample_window: int = 5
    smoothing: float = 0.35
    total_units: int = 0
    completed_units: int = 0
    _started_at: float | None = None
    _samples: deque[float] = field(default_factory=deque)
    _smoothed_seconds_per_unit: float | None = None

    def reset(self, *, total_units: int = 0, initial_seconds_per_unit: float | None = None) -> None:
        self.total_units = max(0, int(total_units))
        self.completed_units = 0
        self._started_at = time.monotonic()
        self._samples = deque(maxlen=max(1, int(self.sample_window)))
        self._smoothed_seconds_per_unit = max(
            0.0,
            float(
                initial_seconds_per_unit
                if initial_seconds_per_unit is not None
                else self.initial_seconds_per_unit
            ),
        )

    def observe(self, completed_units: int, *, total_units: int | None = None, sample_allowed: bool = True) -> None:
        now = time.monotonic()
        if total_units is not None:
            self.total_units = max(0, int(total_units))
        if self._started_at is None:
            self._started_at = now
        self.completed_units = max(0, int(completed_units))
        if self.total_units and self.completed_units > self.total_units:
            self.completed_units = self.total_units
        if not sample_allowed or self.completed_units <= 0:
            return
        elapsed_seconds = max(0.0, now - self._started_at)
        observed_seconds_per_unit = elapsed_seconds / float(self.completed_units)
        self._samples.append(observed_seconds_per_unit)
        recent_median = median(self._samples)
        progress_factor = min(1.0, len(self._samples) / float(max(1, self.sample_window)))
        target_seconds_per_unit = (
            (self.initial_seconds_per_unit * (1.0 - progress_factor))
            + (recent_median * progress_factor)
        )
        current_rate = (
            self._smoothed_seconds_per_unit
            if self._smoothed_seconds_per_unit is not None
            else target_seconds_per_unit
        )
        smoothed = (current_rate * (1.0 - self.smoothing)) + (target_seconds_per_unit * self.smoothing)
        self._smoothed_seconds_per_unit = max(0.0, smoothed)

    def estimated_remaining_seconds(self) -> float | None:
        if self.total_units <= 0:
            return None
        remaining_units = max(0, self.total_units - self.completed_units)
        if remaining_units <= 0:
            return 0.0
        rate = (
            self._smoothed_seconds_per_unit
            if self._smoothed_seconds_per_unit is not None
            else self.initial_seconds_per_unit
        )
        return max(0.0, rate * float(remaining_units))

    def format_remaining(self) -> str:
        remaining_seconds = self.estimated_remaining_seconds()
        if remaining_seconds is None:
            return "calcolo in corso..."
        if remaining_seconds < 60:
            return f"{int(round(remaining_seconds))} secondi"
        minutes = int(remaining_seconds // 60)
        seconds = int(round(remaining_seconds % 60))
        if remaining_seconds >= 3600:
            hours = int(remaining_seconds // 3600)
            minutes = int((remaining_seconds % 3600) // 60)
            return f"{hours:d}:{minutes:02d}:{seconds:02d}"
        if seconds == 60:
            minutes += 1
            seconds = 0
        return f"{minutes:02d}:{seconds:02d}"

def _is_diagnostics_generated_path(base_folder: Path, file_path: Path) -> bool:
    try:
        relative_parts = file_path.relative_to(base_folder).parts
    except ValueError:
        relative_parts = file_path.parts

    for part in relative_parts:
        lowered = part.casefold()
        if lowered.startswith("diagnostica_mp3_"):
            return True
        if lowered == "_tmp_diagnostics":
            return True
    return False


def scan_mp3_files(
    folder_path: str | Path,
    *,
    include_subfolders: bool = False,
    exclude_diagnostics_sessions: bool = False,
) -> list[Path]:
    """
    Restituisce i file MP3 della cartella.

    - include_subfolders=False: solo primo livello.
    - include_subfolders=True: include tutte le sottocartelle.
    - exclude_diagnostics_sessions=True: esclude cartelle Diagnostica_MP3_* e _TMP_DIAGNOSTICS.
    """
    folder = Path(folder_path).expanduser()

    if not folder.is_dir():
        raise FileNotFoundError(f"Cartella non valida: {folder}")

    files: list[Path] = []
    iterator = folder.rglob("*") if include_subfolders else folder.iterdir()

    for item in iterator:
        if not item.is_file():
            continue
        if item.suffix.lower() != ".mp3":
            continue
        if exclude_diagnostics_sessions and _is_diagnostics_generated_path(folder, item):
            continue
        files.append(item)

    files.sort(
        key=lambda path: (
            path.relative_to(folder).as_posix().lower()
            if path.is_relative_to(folder)
            else path.name.lower()
        )
    )
    return files
