"""Manual runtime check for ClipEditorDialog.

This script is intentionally manual and is not part of automated unittest discovery.
It creates a temporary WAV fixture, opens the dialog, and closes automatically
after a short delay.
"""

from __future__ import annotations

import struct
import tempfile
import wave
from pathlib import Path
import tkinter as tk

from clip_editor import ClipEditorDialog


def _create_temp_wav(path: Path) -> None:
    with wave.open(str(path), "w") as handle:
        handle.setparams((1, 2, 44100, 44100, "NONE", "not compressed"))
        handle.writeframes(struct.pack("<h", 0) * 44100)


def run_manual_check() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        wav_path = Path(temp_dir) / "test_clip.wav"
        _create_temp_wav(wav_path)

        print("START DIALOG")
        root = tk.Tk()
        root.withdraw()
        try:
            dialog = ClipEditorDialog(root, wav_path, None, lambda _: None)
            print("DIALOG CREATED", dialog)
            root.after(2000, root.destroy)
            root.mainloop()
            print("EVENT LOOP EXITED")
        except Exception:
            import traceback

            traceback.print_exc()
            raise


if __name__ == "__main__":
    run_manual_check()
