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


def youtube_video_id(url: str) -> str | None:
    """The 11-character video id in a YouTube link, or None if there is not one.

    Returning the id rather than just true/false matters for collecting lectures in
    bulk: the same video can be written as youtu.be/ID, watch?v=ID, or
    watch?v=ID&list=...&index=4, and all three must be recognised as the same
    lecture so it is not transcribed - and paid for - twice.
    """
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
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
            return None
    else:
        return None
    return video_id if _VIDEO_ID.match(video_id) else None


def is_youtube_url(url: str) -> bool:
    """Accept normal YouTube video links: watch?v=, youtu.be/, /shorts/, /live/, /embed/."""
    return youtube_video_id(url) is not None


def is_playlist_url(url: str) -> bool:
    """A link to a whole playlist, rather than to one video.

    A watch?v=...&list=... link is NOT treated as a playlist: it names one video and
    yt-dlp is told to ignore the list. Only /playlist?list=... counts.
    """
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if (parsed.hostname or "").lower() not in _YOUTUBE_HOSTS:
        return False
    if parsed.path.strip("/").split("/")[0] != "playlist":
        return False
    return bool(parse_qs(parsed.query).get("list", [""])[0])


def list_playlist_videos(url: str, limit: int) -> list[dict]:
    """Every video in a playlist, as {id, title, url} - without downloading anything.

    extract_flat stops yt-dlp opening each video in turn, so a sixty-video playlist
    is one quick request rather than sixty slow ones. Private or deleted entries
    appear with no id and are dropped: they cannot be transcribed anyway.
    """
    options = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": True,
        "noprogress": True,
        "playlistend": limit,
    }
    cookies = os.environ.get("YTDLP_COOKIES", "").strip()
    if cookies and os.path.isfile(cookies):
        options["cookiefile"] = cookies

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        # Nothing is being downloaded here, so the download wording would mislead.
        # The common causes are a mistyped list id and a private playlist, and
        # yt-dlp reports both as an unhelpful HTTP error.
        detail = str(e)
        if "HTTP Error 400" in detail or "Unable to download API page" in detail:
            raise AudioError(
                "That playlist could not be read. Check the link is a real playlist "
                "and that it is public or unlisted - private playlists cannot be "
                "opened even with cookies.") from None
        raise AudioError(f"That playlist could not be read: "
                         f"{detail.splitlines()[0][:200]}") from None

    videos = []
    for entry in (info or {}).get("entries") or []:
        if not entry:
            continue
        video_id = entry.get("id")
        if not video_id or not _VIDEO_ID.match(video_id):
            continue
        videos.append({
            "id": video_id,
            "title": entry.get("title") or video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })
    return videos


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
