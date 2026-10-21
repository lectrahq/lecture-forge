"""
Cutting a lecture into short training clips.

Speech models like IndicConformer are trained on short utterances, not
hour-long files. Using the word timestamps from Scribe, we split the lecture
into pieces of roughly 5-20 seconds, always cutting at the longest pause
between two words (never in the middle of a word). FFmpeg then cuts each piece
out of audio.wav, and a NeMo manifest (manifest.jsonl) lists every clip with
its duration and text.
"""

import json
import os
import re
import subprocess
import wave

MIN_SECONDS = 5.0    # try not to make clips shorter than this...
MAX_SECONDS = 20.0   # ...and never longer than this (unless a single word is longer)
PAD_SECONDS = 0.15   # extra audio kept before/after each clip so first/last sounds aren't clipped


def timed_words(transcript: dict) -> list[dict]:
    """The spoken words from the Scribe response, each with text, start and end (seconds)."""
    words = []
    for item in transcript.get("words") or []:
        # Scribe also returns "spacing" items (the gaps between words) and
        # "audio_event" items; only real words matter for cutting.
        if item.get("type") != "word":
            continue
        text = (item.get("text") or "").strip()
        if not text:
            continue
        if item.get("start") is None or item.get("end") is None:
            # Rare: a word without timing. Keep its text with the previous word.
            if words:
                words[-1]["text"] += " " + text
            continue
        words.append({"text": text, "start": float(item["start"]), "end": float(item["end"])})
    return words


def split_at_pauses(words: list[dict]) -> list[list[dict]]:
    """Split a run of words at its largest pause, again and again, until every piece is short enough."""
    if len(words) < 2 or words[-1]["end"] - words[0]["start"] <= MAX_SECONDS:
        return [words]

    best_cut, best_gap = None, -1.0   # largest pause that leaves both sides >= MIN_SECONDS
    any_cut, any_gap = 1, -1.0        # largest pause overall (fallback)
    for i in range(1, len(words)):
        gap = words[i]["start"] - words[i - 1]["end"]
        left = words[i - 1]["end"] - words[0]["start"]
        right = words[-1]["end"] - words[i]["start"]
        if left >= MIN_SECONDS and right >= MIN_SECONDS and gap > best_gap:
            best_cut, best_gap = i, gap
        if gap > any_gap:
            any_cut, any_gap = i, gap

    cut = best_cut if best_cut is not None else any_cut
    return split_at_pauses(words[:cut]) + split_at_pauses(words[cut:])


def plan_segments(words: list[dict], audio_duration: float) -> list[dict]:
    """Start/end time and text of each clip, with a little padding into the silence around it."""
    pieces = split_at_pauses(words) if words else []
    segments = []
    for k, piece in enumerate(pieces):
        start, end = piece[0]["start"], piece[-1]["end"]
        # Pad by at most half the pause, so neighbouring clips never overlap.
        silence_before = start - (pieces[k - 1][-1]["end"] if k > 0 else 0.0)
        silence_after = (pieces[k + 1][0]["start"] if k + 1 < len(pieces) else audio_duration) - end
        start = max(0.0, start - min(PAD_SECONDS, silence_before / 2))
        end = min(audio_duration, end + min(PAD_SECONDS, max(silence_after, 0.0) / 2))
        text = " ".join(word["text"] for word in piece)
        segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    return segments


def cut_clip(wav_path: str, start: float, end: float, out_path: str) -> None:
    """FFmpeg: copy [start, end] out of the 16 kHz mono WAV into its own WAV file."""
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-i", wav_path,   # -ss before -i = fast seek (exact for WAV)
        "-t", f"{end - start:.3f}",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        out_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg could not cut a segment: {result.stderr.strip()[-300:]}")


# --- splitting without word timings (local-model-only jobs) ----------------
#
# The functions above cut at the pauses *between words*, which needs the word
# timings ElevenLabs provides. The local model returns plain text with no timings,
# so when it runs alone the audio is cut at silences found by listening to it
# instead. These pieces are only there to avoid handing the model an hour of audio
# at once - they are temporary and are never kept as training clips.

CHUNK_TARGET_SECONDS = 25.0
CHUNK_MAX_SECONDS = 30.0
_SILENCE_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)")


def find_silences(wav_path: str, noise_db: int = -30, min_length: float = 0.35) -> list[tuple[float, float]]:
    """Quiet stretches in the audio, as (start, end) seconds."""
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-i", wav_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_length}",
        "-f", "null", "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    starts = [float(m) for m in _SILENCE_START.findall(result.stderr)]
    ends = [float(m) for m in _SILENCE_END.findall(result.stderr)]
    return list(zip(starts, ends))


def plan_chunks(audio_duration: float, silences: list[tuple[float, float]]) -> list[dict]:
    """Cut points of roughly CHUNK_TARGET_SECONDS, preferring the middle of a silence."""
    # The middle of each silence is the safest place to cut.
    candidates = [(start + end) / 2 for start, end in silences if 0 < start < audio_duration]
    chunks = []
    position = 0.0
    while position < audio_duration - 0.1:
        target = position + CHUNK_TARGET_SECONDS
        limit = position + CHUNK_MAX_SECONDS
        # The last silence at or before the limit, but past the target if possible.
        usable = [c for c in candidates if target <= c <= limit]
        if not usable:
            usable = [c for c in candidates if position + 5 < c <= limit]
        end = min(usable[-1], audio_duration) if usable else min(limit, audio_duration)
        if end - position < 0.5:          # nothing sensible left
            end = audio_duration
        chunks.append({"start": round(position, 3), "end": round(end, 3)})
        position = end
    return chunks


def cut_chunks(wav_path: str, chunks: list[dict], out_dir: str, prefix: str) -> list[str]:
    """Write each chunk to its own WAV and return the paths."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for number, chunk in enumerate(chunks, start=1):
        path = os.path.join(out_dir, f"{prefix}_chunk_{number:04d}.wav")
        cut_clip(wav_path, chunk["start"], chunk["end"], path)
        paths.append(path)
    return paths


def build_segments(job_id: str, job_dir: str, transcript: dict, audio_duration: float) -> list[dict]:
    """Cut all clips into <job_dir>/segments/ and write manifest.jsonl + segments.json."""
    segments = plan_segments(timed_words(transcript), audio_duration)
    wav_path = os.path.join(job_dir, "audio.wav")
    os.makedirs(os.path.join(job_dir, "segments"), exist_ok=True)

    manifest_lines = []
    for number, segment in enumerate(segments, start=1):
        relative_path = f"segments/{job_id}_{number:04d}.wav"
        clip_path = os.path.join(job_dir, relative_path)
        cut_clip(wav_path, segment["start"], segment["end"], clip_path)

        # Read the real length of the clip from its header for the manifest.
        with wave.open(clip_path, "rb") as clip:
            duration = round(clip.getnframes() / clip.getframerate(), 3)

        segment["file"] = relative_path
        segment["duration"] = duration
        # NeMo manifest line. Paths are relative to the job folder so the
        # whole folder can be copied to the GPU server as-is.
        manifest_lines.append(json.dumps(
            {"audio_filepath": relative_path, "duration": duration, "text": segment["text"]},
            ensure_ascii=False,   # keep Malayalam script readable in the file
        ))

    with open(os.path.join(job_dir, "manifest.jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(manifest_lines) + ("\n" if manifest_lines else ""))
    # Same clips with start/end times, used by the website's segment table.
    with open(os.path.join(job_dir, "segments.json"), "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=1)
    return segments
