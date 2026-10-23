"""
Acoustic Quality & Audio Health Inspector for Lecture Audio.

Analyzes 16 kHz mono 16-bit PCM audio.wav files without bulky external dependencies.
Measures:
  * Speech RMS Level (dBFS)
  * Noise Floor Level (dBFS)
  * Signal-to-Noise Ratio (SNR in dB)
  * Peak Level & Digital Ceiling Clipping Count
  * Speech vs Silence Duration Ratio
  * Composite Audio Health Score (0 - 100)
  * Actionable Diagnostic Feedback
"""

import json
import logging
import math
import os
import struct
import wave

log = logging.getLogger("audio_health")


def analyze_audio_health(wav_path: str) -> dict:
    """Analyze acoustic characteristics of a 16 kHz mono 16-bit WAV file."""
    if not os.path.isfile(wav_path):
        raise FileNotFoundError(f"Audio file not found: {wav_path}")

    with wave.open(wav_path, "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        nframes = wf.getnframes()

        if sampwidth != 2:
            raise ValueError(f"Expected 16-bit audio, got {sampwidth * 8}-bit")

        # Read in blocks of 0.25 seconds (e.g. 4000 samples at 16 kHz)
        block_size = max(1000, int(framerate * 0.25))

        chunk_rms_list = []
        clipped_count = 0
        total_samples = 0
        max_abs_sample = 0

        while True:
            raw = wf.readframes(block_size)
            if not raw:
                break

            sample_count = len(raw) // 2
            if sample_count == 0:
                break

            samples = struct.unpack(f"<{sample_count}h", raw)
            total_samples += sample_count

            sum_sq = 0
            for s in samples:
                abs_s = abs(s)
                if abs_s > max_abs_sample:
                    max_abs_sample = abs_s
                if abs_s >= 32760:  # Digital ceiling clipping (16-bit limit 32767)
                    clipped_count += 1
                sum_sq += s * s

            mean_sq = sum_sq / sample_count
            rms = math.sqrt(mean_sq)
            rms_db = 20 * math.log10(max(rms, 1.0) / 32768.0)
            chunk_rms_list.append(rms_db)

    if not chunk_rms_list or total_samples == 0:
        return {
            "score": 0,
            "rating": "No Audio",
            "duration_seconds": 0.0,
            "speech_rms_db": -96.0,
            "noise_floor_db": -96.0,
            "snr_db": 0.0,
            "peak_db": -96.0,
            "clipped_samples": 0,
            "clipped_percent": 0.0,
            "speech_percent": 0.0,
            "recommendations": ["Audio file contains no audio frames."],
        }

    duration = total_samples / (framerate * channels)
    sorted_rms = sorted(chunk_rms_list)
    n_chunks = len(sorted_rms)

    # Noise floor: quietest 10% of frames (speech pauses/silence)
    noise_count = max(1, int(n_chunks * 0.10))
    noise_floor_db = round(sum(sorted_rms[:noise_count]) / noise_count, 1)

    # Speech threshold: frames significantly above noise floor
    silence_threshold_db = min(-34.0, noise_floor_db + 6.0)
    speech_chunks = [db for db in chunk_rms_list if db > silence_threshold_db]

    if speech_chunks:
        speech_rms_db = round(sum(speech_chunks) / len(speech_chunks), 1)
        speech_percent = round((len(speech_chunks) / n_chunks) * 100, 1)
    else:
        speech_rms_db = noise_floor_db
        speech_percent = 0.0

    snr_db = round(max(0.0, speech_rms_db - noise_floor_db), 1)
    peak_db = round(20 * math.log10(max(max_abs_sample, 1) / 32768.0), 1)
    clipped_percent = round((clipped_count / total_samples) * 100, 3) if total_samples > 0 else 0.0

    # Scoring algorithm (0 - 100 scale):
    # Perfect acoustic profile: 100.
    # Penalties for low SNR, digital clipping, low speech level, or excessive silence.
    score = 100.0
    recommendations = []

    # SNR evaluation
    if snr_db < 10.0:
        score -= 30.0
        recommendations.append(f"High background noise detected (SNR is {snr_db} dB). Consider noise reduction.")
    elif snr_db < 16.0:
        score -= 15.0
        recommendations.append(f"Moderate background noise (SNR {snr_db} dB). Acceptable, but room acoustics could be cleaner.")

    # Clipping evaluation
    if clipped_percent > 0.05:
        score -= 25.0
        recommendations.append(f"Digital clipping detected ({clipped_percent}% of samples hit 0 dBFS ceiling). Microphone gain is too high.")
    elif clipped_count > 0:
        score -= 10.0
        recommendations.append(f"Occasional peak clipping detected ({clipped_count} samples hit digital ceiling).")

    # Speech level evaluation
    if speech_rms_db < -34.0:
        score -= 20.0
        recommendations.append(f"Speech volume is very low ({speech_rms_db} dBFS). Microphone was too far or recording gain is low.")
    elif speech_rms_db < -28.0:
        score -= 10.0
        recommendations.append(f"Speech volume is quiet ({speech_rms_db} dBFS). Volume boost recommended.")
    elif speech_rms_db > -10.0:
        score -= 15.0
        recommendations.append(f"Recording is extremely loud ({speech_rms_db} dBFS). Risk of acoustic distortion.")

    # Speech activity evaluation
    if speech_percent < 30.0:
        score -= 15.0
        recommendations.append(f"Low speech activity ({speech_percent}% of duration). Substantial silence or non-speech pauses present.")

    final_score = max(5, min(100, int(round(score))))

    if final_score >= 85:
        rating = "Optimal"
        if not recommendations:
            recommendations.append("Acoustic profile is clean and optimal for speech recognition fine-tuning.")
    elif final_score >= 70:
        rating = "Good"
    elif final_score >= 50:
        rating = "Fair"
    else:
        rating = "Poor"

    return {
        "score": final_score,
        "rating": rating,
        "duration_seconds": round(duration, 2),
        "speech_rms_db": speech_rms_db,
        "noise_floor_db": noise_floor_db,
        "snr_db": snr_db,
        "peak_db": peak_db,
        "clipped_samples": clipped_count,
        "clipped_percent": clipped_percent,
        "speech_percent": speech_percent,
        "recommendations": recommendations,
    }


def get_cached_health(folder: str, recompute: bool = False) -> dict | None:
    """Read cached audio_health.json or recompute from audio.wav if available."""
    cache_path = os.path.join(folder, "audio_health.json")
    if not recompute and os.path.isfile(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning("Could not read cached audio_health.json: %s", e)

    wav_path = os.path.join(folder, "audio.wav")
    if not os.path.isfile(wav_path):
        return None

    try:
        health = analyze_audio_health(wav_path)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(health, f, ensure_ascii=False, indent=2)
        return health
    except Exception as e:
        log.exception("Audio health analysis failed for %s", wav_path)
        return None
