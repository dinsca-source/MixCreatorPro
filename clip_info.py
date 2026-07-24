# -*- coding: utf-8 -*-
"""
clip_info.py

Contiene la struttura dati per le clip personalizzate dei brani.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ClipInfo:
    use_custom_clip: bool = False
    clip_start_ms: int = 0
    clip_end_ms: int = 0

    def copy(self) -> "ClipInfo":
        return ClipInfo(
            use_custom_clip=self.use_custom_clip,
            clip_start_ms=self.clip_start_ms,
            clip_end_ms=self.clip_end_ms
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "use_custom_clip": self.use_custom_clip,
            "clip_start_ms": self.clip_start_ms,
            "clip_end_ms": self.clip_end_ms
        }

    @classmethod
    def from_dict(
        cls,
        data: Any,
        resolved_path: Any = None
    ) -> "ClipInfo":
        """
        Ricostruisce l'istanza da un dizionario serializzato.
        resolved_path è accettato per compatibilita futura.
        """
        if not isinstance(data, dict):
            return cls()

        use_custom_clip = bool(data.get("use_custom_clip", False))

        try:
            clip_start_ms = int(data.get("clip_start_ms", 0))
        except (TypeError, ValueError):
            clip_start_ms = 0

        try:
            clip_end_ms = int(data.get("clip_end_ms", 0))
        except (TypeError, ValueError):
            clip_end_ms = 0

        if clip_start_ms < 0:
            clip_start_ms = 0

        if clip_end_ms < 0:
            clip_end_ms = 0

        if clip_end_ms <= clip_start_ms:
            use_custom_clip = False
            clip_start_ms = 0
            clip_end_ms = 0

        return cls(
            use_custom_clip=use_custom_clip,
            clip_start_ms=clip_start_ms,
            clip_end_ms=clip_end_ms
        )

    def validate(self, full_duration_ms: int) -> None:
        if self.clip_start_ms < 0:
            raise ValueError("L'inizio del clip deve essere maggiore o uguale a zero.")

        if self.clip_end_ms <= self.clip_start_ms:
            raise ValueError("La fine del clip deve essere maggiore dell'inizio.")

        if full_duration_ms <= 0:
            raise ValueError("Durata del brano non valida.")

        if self.clip_start_ms >= full_duration_ms:
            raise ValueError("L'inizio del clip deve essere inferiore alla durata del brano.")

    def resolve_segment(self, full_duration_ms: int) -> tuple[float, float]:
        self.validate(full_duration_ms)

        clipped_end_ms = min(self.clip_end_ms, full_duration_ms)
        duration_ms = clipped_end_ms - self.clip_start_ms

        if duration_ms <= 0:
            raise ValueError("La durata del clip deve essere maggiore di zero.")

        start_seconds = self.clip_start_ms / 1000.0
        duration_seconds = duration_ms / 1000.0

        return start_seconds, duration_seconds
