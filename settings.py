# -*- coding: utf-8 -*-
"""
MixCreator PRO
settings.py - Versione 1.2

Salva le impostazioni in una cartella permanente dell'utente:
%LOCALAPPDATA%\MixCreatorPRO\config.json

Questo evita la perdita delle preferenze quando l'applicazione
viene eseguita come EXE PyInstaller in modalità one-file.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS: dict[str, Any] = {
    "input_folder": "",
    "output_folder": "",
    "output_name": "MixFinale",
    "clip_seconds": 60,
    "crossfade_seconds": 3,
    "fade_in_seconds": 1,
    "fade_out_seconds": 1,
    "bitrate": "320k",
    "cut_mode": "inizio",
    "random_order": False,
    "normalize_audio": True,
    "continue_short_tracks": False,
    "exclude_unrecoverable_from_mix": False,
    "diagnostics_placement_mode": "copy",
    "diagnostics_last_reverify_csv": "",
    "appearance_mode": "System"
}


class SettingsManager:
    def __init__(self, config_path: str | Path | None = None) -> None:
        if config_path is not None:
            self.config_path = Path(config_path)
        else:
            self.config_path = self._get_user_config_path()

        self._migrate_old_config_if_needed()

    @staticmethod
    def _get_user_config_path() -> Path:
        """
        Restituisce una cartella persistente e scrivibile per l'utente.

        Windows:
            C:\\Users\\NOME\\AppData\\Local\\MixCreatorPRO\\config.json

        Fallback:
            cartella home dell'utente.
        """
        local_appdata = os.getenv("LOCALAPPDATA")

        if local_appdata:
            base_dir = Path(local_appdata)
        else:
            base_dir = Path.home() / "AppData" / "Local"

        return base_dir / "MixCreatorPRO" / "config.json"

    def _migrate_old_config_if_needed(self) -> None:
        """
        Copia automaticamente il vecchio config.json del progetto
        nella nuova cartella permanente, solo se il nuovo file non esiste.
        """
        if self.config_path.exists():
            return

        old_config = Path(__file__).resolve().parent / "config.json"

        if not old_config.is_file():
            return

        try:
            with old_config.open("r", encoding="utf-8") as file:
                old_settings = json.load(file)

            if isinstance(old_settings, dict):
                self.save(old_settings)

        except (OSError, json.JSONDecodeError):
            pass

    def load(self) -> dict[str, Any]:
        """
        Carica le impostazioni.
        Se il file manca o non è valido, lo ricrea con i valori predefiniti.
        """
        if not self.config_path.is_file():
            settings = deepcopy(DEFAULT_SETTINGS)
            self.save(settings)
            return settings

        try:
            with self.config_path.open("r", encoding="utf-8") as file:
                loaded = json.load(file)

        except (OSError, json.JSONDecodeError):
            settings = deepcopy(DEFAULT_SETTINGS)
            self.save(settings)
            return settings

        if not isinstance(loaded, dict):
            settings = deepcopy(DEFAULT_SETTINGS)
            self.save(settings)
            return settings

        settings = deepcopy(DEFAULT_SETTINGS)

        for key, value in loaded.items():
            if key in settings:
                settings[key] = value

        settings = self._validate(settings)
        self.save(settings)

        return settings

    def save(self, settings: dict[str, Any]) -> None:
        """
        Salva le impostazioni in modo atomico.
        """
        validated = self._validate(settings)

        self.config_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        temporary_path = self.config_path.with_suffix(".json.tmp")

        try:
            with temporary_path.open("w", encoding="utf-8") as file:
                json.dump(
                    validated,
                    file,
                    ensure_ascii=False,
                    indent=4
                )

            temporary_path.replace(self.config_path)

        except OSError as error:
            if temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

            raise RuntimeError(
                f"Impossibile salvare le impostazioni:\n{error}"
            ) from error

    def reset(self) -> dict[str, Any]:
        settings = deepcopy(DEFAULT_SETTINGS)
        self.save(settings)
        return settings

    @staticmethod
    def _validate(settings: dict[str, Any]) -> dict[str, Any]:
        validated = deepcopy(DEFAULT_SETTINGS)

        validated.update(
            {
                key: value
                for key, value in settings.items()
                if key in DEFAULT_SETTINGS
            }
        )

        validated["input_folder"] = str(
            validated["input_folder"] or ""
        )

        validated["output_folder"] = str(
            validated["output_folder"] or ""
        )

        validated["output_name"] = str(
            validated["output_name"] or "MixFinale"
        ).strip()

        validated["diagnostics_last_reverify_csv"] = str(
            validated.get("diagnostics_last_reverify_csv", "") or ""
        ).strip()

        if not validated["output_name"]:
            validated["output_name"] = "MixFinale"

        validated["clip_seconds"] = SettingsManager._bounded_int(
            validated["clip_seconds"],
            minimum=5,
            maximum=600,
            fallback=60
        )

        validated["crossfade_seconds"] = SettingsManager._bounded_int(
            validated["crossfade_seconds"],
            minimum=0,
            maximum=30,
            fallback=3
        )

        validated["fade_in_seconds"] = SettingsManager._bounded_int(
            validated["fade_in_seconds"],
            minimum=0,
            maximum=30,
            fallback=1
        )

        validated["fade_out_seconds"] = SettingsManager._bounded_int(
            validated["fade_out_seconds"],
            minimum=0,
            maximum=30,
            fallback=1
        )

        valid_bitrates = {
            "128k",
            "192k",
            "256k",
            "320k"
        }

        if validated["bitrate"] not in valid_bitrates:
            validated["bitrate"] = "320k"

        valid_cut_modes = {
            "inizio",
            "centro",
            "fine",
            "casuale",
            "intro_fine",
            "intero"
        }

        if validated["cut_mode"] not in valid_cut_modes:
            validated["cut_mode"] = "inizio"

        validated["random_order"] = bool(
            validated["random_order"]
        )

        validated["normalize_audio"] = bool(
            validated["normalize_audio"]
        )

        validated["continue_short_tracks"] = bool(
            validated["continue_short_tracks"]
        )

        validated["exclude_unrecoverable_from_mix"] = bool(
            validated["exclude_unrecoverable_from_mix"]
        )

        valid_placement_modes = {
            "copy",
            "move",
        }
        if validated["diagnostics_placement_mode"] not in valid_placement_modes:
            validated["diagnostics_placement_mode"] = "copy"

        valid_appearance_modes = {
            "System",
            "Light",
            "Dark"
        }

        if validated["appearance_mode"] not in valid_appearance_modes:
            validated["appearance_mode"] = "System"

        return validated

    @staticmethod
    def _bounded_int(
        value: Any,
        minimum: int,
        maximum: int,
        fallback: int
    ) -> int:
        try:
            number = int(float(value))

        except (TypeError, ValueError):
            return fallback

        return max(
            minimum,
            min(maximum, number)
        )


def test_settings() -> None:
    manager = SettingsManager()
    settings = manager.load()

    print("Impostazioni caricate correttamente.")
    print(f"File configurazione: {manager.config_path}")
    print(
        json.dumps(
            settings,
            ensure_ascii=False,
            indent=4
        )
    )


if __name__ == "__main__":
    test_settings()
