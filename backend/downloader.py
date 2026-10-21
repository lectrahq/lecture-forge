"""
Getting the audio ready: download from YouTube (yt-dlp) and convert with FFmpeg.

The output of this step is always data/<job_id>/audio.wav:
16 kHz, mono, 16-bit PCM WAV - the format the speech model is trained on.
"""

import logging
import os
import re
import shutil
import subprocess
import wave
from urllib.parse import parse_qs, urlparse

import yt_dlp

log = logging.getLogger("downloader")

# YouTube video ids are 11 characters of letters, digits, '-' and '_'.
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}


class AudioError(Exception):
    """A problem the user should see (shown as the job's error message)."""


def check_ffmpeg() -> None:
    """Stop the server at startup if FFmpeg is missing - nothing works without it."""
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(
            f"{' and '.join(missing)} not found on PATH. FFmpeg is required "
            "(the backend Docker image installs it with apt-get)."
        )


def is_youtube_url(url: str) -> bool:
    """Accept normal YouTube video links: watch?v=, youtu.be/, /shorts/, /live/, /embed/."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()

    if host == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0]
    elif host in _YOUTUBE_HOSTS:
        parts = parsed.path.strip("/").split("/")
        if parts[0] == "watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parts[0] in ("shorts", "live", "embed") and len(parts) > 1:
            video_id = parts[1]
        else:
            return False
    else:
        return False
    return bool(_VIDEO_ID.match(video_id))


def download_youtube_audio(url: str, job_dir: str) -> tuple[str, str, float | None]:
    """Download only the audio track. Returns (file path, video title, duration in seconds)."""
    options = {
        "format": "bestaudio/best",          # audio only; falls back to a full video if needed
        "outtmpl": os.path.join(job_dir, "source.%(ext)s"),
        "noplaylist": True,                   # a watch?v=..&list=.. link downloads just the one video
        "quiet": True,
        "noprogress": True,
    }
    cookies = os.environ.get("YTDLP_COOKIES", "").strip()
    if cookies:
        if not os.path.isfile(cookies):
            raise AudioError(f"YTDLP_COOKIES is set but the file '{cookies}' does not exist.")
        options["cookiefile"] = cookies

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
    except yt_dlp.utils.DownloadError as e:
        raise AudioError(_explain_download_error(str(e))) from None

    if not os.path.isfile(path):
        raise AudioError("yt-dlp finished but the downloaded audio file is missing.")
    return path, info.get("title") or url, info.get("duration")


def _explain_download_error(message: str) -> str:
    """Turn yt-dlp's long error text into something a user can act on."""
    message = message.replace("ERROR: ", "").strip()
    lower = message.lower()
    if "sign in to confirm" in lower or "not a bot" in lower:
        return ("YouTube blocked the download (\"Sign in to confirm you're not a bot\"). "
                "Upload the audio/video file instead, or ask the admin to set YTDLP_COOKIES.")
    if "private video" in lower:
        return "This YouTube video is private."
    if "video unavailable" in lower:
        return "This YouTube video is unavailable (removed, region-locked or age-restricted)."
    return f"YouTube download failed: {message[:500]}"


def convert_to_wav(source_path: str, wav_path: str) -> None:
    """FFmpeg: any audio/video file -> 16 kHz mono 16-bit PCM WAV (video is dropped)."""
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", source_path,
        "-vn",                 # ignore any video stream
        "-ac", "1",            # mono
        "-ar", "16000",        # 16 kHz
        "-c:a", "pcm_s16le",   # 16-bit PCM
        wav_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown error"
        raise AudioError(f"Could not read audio from this file - is it a valid audio/video file? (FFmpeg: {detail})")


def wav_duration(wav_path: str) -> float:
    """Length of a WAV file in seconds, read from its header (no need to load the audio)."""
    with wave.open(wav_path, "rb") as w:
        return w.getnframes() / w.getframerate()
