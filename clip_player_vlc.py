# -*- coding: utf-8 -*-
"""
clip_player_vlc.py

Wrapper per python-vlc utilizzato dal Clip Editor.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import vlc


class ClipPlayerVLC:
    def __init__(self, mp3_path: str | Path) -> None:
        self.path = Path(mp3_path)
        if not self.path.is_file():
            raise FileNotFoundError(f"File non trovato: {self.path}")

        self.instance = self._create_instance()
        self.player = self.instance.media_player_new()
        self.media = self.instance.media_new(str(self.path))
        self.player.set_media(self.media)
        self.duration_ms = self._determine_duration_ms()

    def _create_instance(self) -> vlc.Instance:
        return vlc.Instance()

    def _determine_duration_ms(self) -> int:
        try:
            self.media.parse()
        except Exception:
            pass

        duration = self.media.get_duration()
        if duration and duration > 0:
            return int(duration)

        try:
            self.player.play()
            time.sleep(0.1)
            duration = self.player.get_length()
            self.player.pause()
            self.player.stop()
        except Exception:
            duration = 0

        if not duration or duration <= 0:
            raise RuntimeError("Impossibile determinare la durata del file MP3 con VLC.")

        return int(duration)

    def play(self) -> None:
        result = self.player.play()
        if result == -1:
            raise RuntimeError("Impossibile avviare la riproduzione VLC.")

    def pause(self) -> None:
        self.player.pause()

    def stop(self) -> None:
        self.player.stop()

    def set_time_ms(self, milliseconds: int) -> None:
        milliseconds = max(0, int(milliseconds))
        # Apply seek and verify it took effect. Some VLC builds require
        # the player to be in a running state to apply seeks reliably.
        try:
            self.player.set_time(milliseconds)
        except Exception:
            # Best-effort: ignore failures from libvlc and continue.
            pass

        # Poll a short time to ensure the player's reported time matches
        # the requested position within a small tolerance.
        target = milliseconds
        for _ in range(8):
            pos = self.player.get_time()
            if pos is not None and abs(pos - target) <= 50:
                return
            # If player is not running, start and immediately pause to force position update
            try:
                # Start only briefly to allow libvlc to apply the seek
                state = self.player.get_state()
                if state not in (vlc.State.Playing, vlc.State.Paused):
                    self.player.play()
                    time.sleep(0.03)
                    self.player.pause()
                else:
                    time.sleep(0.03)
            except Exception:
                time.sleep(0.03)

    def get_time_ms(self) -> int:
        pos = self.player.get_time()
        if pos is None or pos < 0:
            return 0
        return int(pos)

    def get_state(self) -> vlc.State:
        try:
            return self.player.get_state()
        except Exception:
            return vlc.State.NothingSpecial

    def play_from_ms(self, position_ms: int, wait_timeout_ms: int = 2000) -> None:
        """
        Play from a specific millisecond position, handling VLC states reliably.

        This method deals with the Ended/Stopped states where a plain
        set_time + play may be ignored. It will (if needed) restart the
        media, wait for a ready state, apply the seek, verify it, and
        start playback from the requested position.
        """
        target = max(0, int(position_ms))

        # Read initial state
        state = self.get_state()

        # If the player is in an Ended/Error/Stopped state, reinitialize playback
        if state in (vlc.State.Ended, vlc.State.Error, vlc.State.Stopped):
            try:
                # Stop and re-set media to force a clean start
                self.player.stop()
            except Exception:
                pass
            try:
                # Recreate media binding in case VLC needs reattachment
                self.media = self.instance.media_new(str(self.path))
                self.player.set_media(self.media)
            except Exception:
                pass

            # Start playback briefly to initialize decoder/state machine
            result = self.player.play()
            if result == -1:
                raise RuntimeError("Impossibile avviare la riproduzione VLC (play_from_ms init).")

            # Wait until player moves out of Opening/Buffering into a reachable state
            start_time = time.time()
            while True:
                st = self.get_state()
                if st in (vlc.State.Playing, vlc.State.Paused, vlc.State.Buffering, vlc.State.Opening):
                    break
                if (time.time() - start_time) * 1000.0 > wait_timeout_ms:
                    break
                time.sleep(0.02)

            # Pause immediately to allow precise seeking
            try:
                self.player.pause()
            except Exception:
                pass

        else:
            # If not ended, ensure player is at least ready; if not, attempt to start briefly
            if state in (vlc.State.NothingSpecial, vlc.State.Opening, vlc.State.Buffering):
                try:
                    self.player.play()
                    time.sleep(0.02)
                    self.player.pause()
                except Exception:
                    pass

        # Apply seek and verify
        self.set_time_ms(target)

        # Ensure the reported time is close to target; if not, attempt a short play/pause cycle
        start = time.time()
        while True:
            pos = self.get_time_ms()
            if abs(pos - target) <= 60:
                break
            if (time.time() - start) * 1000.0 > wait_timeout_ms:
                break
            try:
                # Try to nudge libvlc to apply seek
                self.player.play()
                time.sleep(0.03)
                self.player.pause()
            except Exception:
                time.sleep(0.03)

        # Start playback from target
        try:
            self.player.play()
        except Exception:
            raise RuntimeError("Impossibile avviare la riproduzione VLC (play_from_ms final play).")

    def get_duration_ms(self) -> int:
        return int(self.duration_ms)

    def is_playing(self) -> bool:
        return self.player.is_playing() == 1

    def is_paused(self) -> bool:
        state = self.player.get_state()
        return state == vlc.State.Paused

    def release(self) -> None:
        try:
            self.player.stop()
        except Exception:
            pass
        try:
            self.player.release()
        except Exception:
            pass
        try:
            self.media.release()
        except Exception:
            pass
        try:
            self.instance.release()
        except Exception:
            pass
