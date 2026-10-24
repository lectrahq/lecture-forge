"""Audio waveform downsampling and visualization cache.

Generates a normalized peak amplitude envelope (300 bars) for 16 kHz mono WAV
audio files, cached as JSON for sub-millisecond client rendering.
"""

import array
import json
import logging
import os
import wave

log = logging.getLogger(__name__)

NUM_WAVEFORM_BARS = 300


def compute_waveform_peaks(wav_path: str, num_bars: int = NUM_WAVEFORM_BARS) -> list[float]:
    """Read a 16 kHz mono WAV and downsample amplitude into normalized 0.05..1.0 values."""
    if not os.path.isfile(wav_path):
        return []

    try:
        with wave.open(wav_path, "rb") as wf:
            n_channels = wf.getnchannels()
            samp_width = wf.getsampwidth()
            n_frames = wf.getnframes()

            if n_frames == 0:
                return [0.08] * num_bars

            # We expect 16-bit PCM (2 bytes per sample)
            if samp_width != 2:
                log.warning("Unsupported sample width for waveform: %d", samp_width)
                return [0.08] * num_bars

            frames_per_bar = max(1, n_frames // num_bars)
            peaks: list[float] = []

            for _ in range(num_bars):
                raw_bytes = wf.readframes(frames_per_bar)
                if not raw_bytes:
                    break

                samples = array.array("h", raw_bytes)
                if not samples:
                    peaks.append(0.08)
                    continue

                # Subsample if slice is large (step by 16 for high-speed calculation)
                step = max(1, len(samples) // 128)
                sub = samples[::step]
                peak = max(abs(s) for s in sub) if sub else 0

                # Normalize to 0.06 .. 1.0 (minimum 0.06 so quiet zones still have a visible bar)
                norm = round(min(1.0, max(0.06, peak / 32767.0)), 3)
                peaks.append(norm)

            # Pad if needed to reach exact num_bars
            while len(peaks) < num_bars:
                peaks.append(0.08)

            return peaks
    except Exception as e:
        log.warning("Failed computing waveform for %s: %s", wav_path, e)
        return [0.08] * num_bars


def get_or_create_waveform(folder: str, wav_path: str, num_bars: int = NUM_WAVEFORM_BARS) -> list[float]:
    """Return cached waveform JSON if present, otherwise compute, cache, and return."""
    cache_file = os.path.join(folder, "waveform.json")
    if os.path.isfile(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and len(data) > 0:
                    return data
        except Exception:
            pass

    peaks = compute_waveform_peaks(wav_path, num_bars=num_bars)
    if peaks:
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(peaks, f)
        except Exception as e:
            log.warning("Could not cache waveform to %s: %s", cache_file, e)

    return peaks
