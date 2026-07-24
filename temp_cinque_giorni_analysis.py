from __future__ import annotations

import subprocess
from pathlib import Path

from mp3_diagnostics import MP3DiagnosticsEngine, AUDIO_WINDOW_MS, SILENCE_RMS_THRESHOLD_DB, SILENCE_PEAK_THRESHOLD_DB

FILE = Path(r"C:\BASI Organizzate\Generico\Cinque Giorni (Michele Zarrillo).mp3")
engine = MP3DiagnosticsEngine()

def fmt(ms: int) -> str:
    s, rem = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{rem:03d}"


def main() -> None:
    duration_s = engine._safe_duration_seconds(FILE)
    duration_ms = int(round(duration_s * 1000))
    print(f"duration_seconds={duration_s:.3f}")
    print(f"duration_ms={duration_ms}")

    bounds = engine.detect_significant_audio_bounds(FILE)
    print("bounds=", bounds)

    command = [
        str(engine.ffmpeg.ffmpeg_path),
        "-hide_banner",
        "-nostats",
        "-v",
        "error",
        "-i",
        str(FILE),
        "-ac",
        "1",
        "-ar",
        "8000",
        "-f",
        "s16le",
        "-",
    ]
    proc = subprocess.run(command, capture_output=True)
    print(f"ffmpeg_returncode={proc.returncode}")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
    print("ffmpeg_stderr_lines=")
    for line in stderr.splitlines():
        print(line)

    windows = engine._extract_audio_windows(file_path=FILE, duration_ms=duration_ms)
    print(f"window_count={len(windows)}")
    start_cut = max(0, duration_ms - 8000)
    print("start_cut_ms=", start_cut)
    print("start\tend\trms\tpeak\taudible\tffmpeg_error")
    error_windows = []
    for w in windows:
        if w.end_ms < start_cut:
            continue
        audible = (w.rms_dbfs > SILENCE_RMS_THRESHOLD_DB) or (w.peak_dbfs > SILENCE_PEAK_THRESHOLD_DB)
        err = ""
        if w.rms_dbfs <= SILENCE_RMS_THRESHOLD_DB and w.peak_dbfs <= SILENCE_PEAK_THRESHOLD_DB:
            err = "silence"
        print(f"{fmt(w.start_ms)}\t{fmt(w.end_ms)}\t{w.rms_dbfs:.2f}\t{w.peak_dbfs:.2f}\t{'SI' if audible else 'NO'}\t{err}")
        if not audible:
            error_windows.append((w.start_ms, w.end_ms, w.rms_dbfs, w.peak_dbfs))

    print("silent_windows_in_last8s=", len(error_windows))
    print("last_10_windows=")
    for w in windows[-10:]:
        audible = (w.rms_dbfs > SILENCE_RMS_THRESHOLD_DB) or (w.peak_dbfs > SILENCE_PEAK_THRESHOLD_DB)
        print(f"{fmt(w.start_ms)}-{fmt(w.end_ms)} rms={w.rms_dbfs:.2f} peak={w.peak_dbfs:.2f} audible={'SI' if audible else 'NO'}")


if __name__ == "__main__":
    main()
