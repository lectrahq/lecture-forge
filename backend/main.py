"""
Lecture Audio Transcription Tool - backend API (FastAPI).

A job is created from a YouTube link or an uploaded file. The work runs in the
background and moves through these stages:

    queued -> downloading -> transcribing -> segmenting -> done   (or failed)

Everything a job produces is written to data/<job_id>/:
    audio.wav          16 kHz mono 16-bit WAV of the whole lecture
    transcript.json    full raw response from ElevenLabs Scribe
    transcript.txt     plain transcript text
    segments/          short training clips (<job_id>_0001.wav, ...)
    manifest.jsonl     NeMo training manifest for the clips
    segments.json      clip start/end/text (for the website)
"""

import io
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import unicodedata
import wave
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

load_dotenv()  # in Docker the variables already come from backend/.env via docker-compose

import db  # noqa: E402  (imported after load_dotenv so DATA_DIR is set)
from downloader import (AudioError, check_ffmpeg, convert_to_wav,  # noqa: E402
                        download_youtube_audio, is_playlist_url, is_youtube_url,
                        list_playlist_videos, wav_duration, youtube_video_id)
from local_asr import LocalAsrError, transcribe_clips  # noqa: E402
from normalise import (has_latin, load_english_map, map_coverage,  # noqa: E402
                       normalise_text, prepare_reference)
from segmenter import (build_segments, cut_chunks, find_silences,  # noqa: E402
                       plan_chunks, plan_segments, timed_words)
from transcriber import (TranscriptionError, api_key_configured,  # noqa: E402
                         credits_used_now, estimate_credits, get_credit_usage,
                         measured_cost, transcribe)
from export_dataset import (create_dataset_archive, export_dataset,  # noqa: E402
                            get_dataset_summary, list_datasets)
from audio_health import get_cached_health  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jobs")

# File types accepted by the upload endpoint (anything FFmpeg can read audio from).
ALLOWED_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wma", ".amr",
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".3gp", ".mpeg", ".mpg", ".ts",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_ffmpeg()  # raises a clear error and stops startup if FFmpeg is missing
    db.init_db()
    stale = db.fail_unfinished_jobs()
    if stale:
        log.warning("marked %d unfinished job(s) as failed after restart", stale)
    if not api_key_configured():
        log.warning("ELEVENLABS_API_KEY is not set - transcription will fail until it is added to backend/.env")
    waiting = db.count_queued()
    if waiting:
        log.info("%d job(s) still queued from before the restart - resuming", waiting)
    worker = threading.Thread(target=worker_loop, name="job-worker", daemon=True)
    worker.start()
    log.info("backend ready, data folder: %s", db.DATA_DIR)
    yield
    _stop_worker.set()
    worker.join(timeout=5)


app = FastAPI(title="Lecture Audio Transcription Tool", lifespan=lifespan)


# ---------------------------------------------------------------- helpers

ENGLISH_MAP_PATH = os.environ.get("ENGLISH_MAP", os.path.join(db.DATA_DIR, "english_map.json"))


def score_pair(reference: str, prediction: str) -> dict:
    """Word and character error rate for one pair of already-cleaned strings."""
    import jiwer

    if not reference.strip():
        return {"wer": None, "cer": None}
    return {"wer": round(float(jiwer.wer(reference, prediction)), 4),
            "cer": round(float(jiwer.cer(reference, prediction)), 4)}


def new_job_id() -> str:
    """Sortable, readable id, e.g. 20261019-153012-a3f9c1."""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def job_dir(job_id: str) -> str:
    return os.path.join(db.DATA_DIR, job_id)


def parse_keyterms(keyterms: list[str] | str | None) -> list[str]:
    """Accept a list or a comma-separated string; trim, drop empties and duplicates."""
    if not keyterms:
        return []
    if isinstance(keyterms, str):
        keyterms = keyterms.split(",")
    cleaned = []
    for term in keyterms:
        term = " ".join(str(term).split())
        if term and term not in cleaned:
            cleaned.append(term)
    return cleaned


def public_job(job: dict) -> dict:
    """The job fields the website needs."""
    return {
        "job_id": job["id"],
        "status": job["status"],
        "source": job["source"],
        "source_url": job["source_url"],
        "filename": job["filename"],
        "title": job["title"] or job["filename"] or job["source_url"],
        "duration": job["duration"],
        "keyterms": [t for t in (job["keyterms"] or "").split(",") if t],
        "segment_count": job["segment_count"],
        "engine": job["engine"],
        "purpose": job["purpose"],
        # How far through the local model is, while it is running.
        "progress": read_progress(job["id"]) if job["status"] == "transcribing_local" else None,
        # How long each transcriber took, for comparing them on speed.
        "elevenlabs_seconds": job["elevenlabs_seconds"],
        "local_seconds": job["local_seconds"],
        # What the job cost in ElevenLabs credits. Null for a local-only job,
        # which is the point: it cost nothing.
        "credits_used": job["credits_used"],
        # audio.wav exists once the "downloading" stage has finished (even if a later stage failed)
        "audio_available": os.path.isfile(os.path.join(job_dir(job["id"]), "audio.wav")),
        "has_local_transcript": os.path.isfile(os.path.join(job_dir(job["id"]), "local_transcript.txt")),
        "has_comparison": os.path.isfile(os.path.join(job_dir(job["id"]), "local_segments.json")),
        "human_verified": os.path.isfile(os.path.join(job_dir(job["id"]), "transcript_corrected.json")),
        "audio_health": get_cached_health(job_dir(job["id"]), recompute=False)
        if os.path.isfile(os.path.join(job_dir(job["id"]), "audio_health.json")) else None,
        # A transcribe-only ElevenLabs job can become training data later, for free,
        # because the word timings needed to cut clips are already in transcript.json.
        "can_promote": (job["status"] == "done" and job["purpose"] == "transcribe"
                        and job["engine"] != "local"
                        and os.path.isfile(os.path.join(job_dir(job["id"]), "transcript.json"))),
        "error": job["error"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }


def progress_file(job_id: str) -> str:
    """Where the local model reports how far through a job it is.

    It lives in the shared data folder, so the asr container can write it while
    this one reads it, without either waiting on the other.
    """
    return os.path.join(job_dir(job_id), "_progress.json")


def read_progress(job_id: str) -> dict | None:
    path = progress_file(job_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None      # being written right now; the next poll will get it


def clear_progress(job_id: str) -> None:
    try:
        os.remove(progress_file(job_id))
    except OSError:
        pass


def download_name(title: str, job_id: str) -> str:
    """A safe file name for downloads, e.g. 'Scalars And Vectors.wav'."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title or "")  # characters Windows doesn't allow
    name = " ".join(name.split())[:100].strip(" .")
    return (name or job_id) + ".wav"


def get_job_or_404(job_id: str) -> dict:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


# ---------------------------------------------------------------- pipeline

def run_local_only(job_id: str, folder: str, wav_path: str, duration: float) -> None:
    """Transcribe with the local model alone: no ElevenLabs, no credits, no clips.

    The local model gives no word timings, so the audio is cut at silences purely
    to avoid feeding it an hour at once. Those pieces are deleted afterwards - this
    job deliberately produces no training data.
    """
    db.update_job(job_id, status="transcribing_local")
    log.info("[%s] splitting audio at silences for the local model", job_id)
    chunks = plan_chunks(duration, find_silences(wav_path))
    temp_dir = os.path.join(folder, "_local_chunks")
    try:
        paths = cut_chunks(wav_path, chunks, temp_dir, job_id)
        log.info("[%s] transcribing %d chunks with the local model", job_id, len(paths))
        started = time.time()
        texts = transcribe_clips(paths, progress_path=progress_file(job_id))
        db.update_job(job_id, local_seconds=round(time.time() - started, 1))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        clear_progress(job_id)

    full_text = " ".join(t.strip() for t in texts if t.strip())
    with open(os.path.join(folder, "local_transcript.txt"), "w", encoding="utf-8") as f:
        f.write(full_text + "\n")
    log.info("[%s] local transcript saved (%d characters)", job_id, len(full_text))


def run_local_on_segments(job_id: str, folder: str, wav_path: str, segments: list[dict],
                          keep_clips: bool) -> None:
    """Run the local model over the same pieces ElevenLabs produced, so the two
    transcripts can be compared segment by segment.

    For a training job the clips already exist on disk. For a transcribe-only job
    they are written to a temporary folder, used, and removed.
    """
    db.update_job(job_id, status="transcribing_local")
    temp_dir = os.path.join(folder, "_local_chunks")
    try:
        if keep_clips:
            paths = [os.path.join(folder, s["file"]) for s in segments]
        else:
            paths = cut_chunks(wav_path, segments, temp_dir, job_id)
        log.info("[%s] transcribing %d segments with the local model", job_id, len(paths))
        started = time.time()
        texts = transcribe_clips(paths, progress_path=progress_file(job_id))
        db.update_job(job_id, local_seconds=round(time.time() - started, 1))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        clear_progress(job_id)

    compared = [
        {"start": s["start"], "end": s["end"], "elevenlabs": s["text"], "local": text}
        for s, text in zip(segments, texts)
    ]
    with open(os.path.join(folder, "local_segments.json"), "w", encoding="utf-8") as f:
        json.dump(compared, f, ensure_ascii=False, indent=1)
    with open(os.path.join(folder, "local_transcript.txt"), "w", encoding="utf-8") as f:
        f.write(" ".join(t.strip() for t in texts if t.strip()) + "\n")
    log.info("[%s] local transcript and comparison saved", job_id)


# --- the job queue --------------------------------------------------------
#
# One lecture at a time, on purpose. Every submission used to start its own
# background task immediately, so queueing twenty lectures meant twenty yt-dlp
# downloads, twenty FFmpeg conversions and twenty ElevenLabs calls at once -
# inside a container limited to 2 GB of memory and 2.5 processors. That does not
# go slowly, it falls over: the container is killed and the whole batch is lost.
#
# A single worker thread makes a long collection run survivable, and because
# waiting jobs simply sit in the database as 'queued', they also outlive a
# restart instead of being thrown away.

# How often to look for new work when the queue is empty. Submitting a job does
# not need to feel instant - it needs to not take the server down.
QUEUE_POLL_SECONDS = 3

_stop_worker = threading.Event()


def worker_loop() -> None:
    """Runs queued jobs one after another until the server shuts down."""
    log.info("job worker started")
    while not _stop_worker.is_set():
        job = db.next_queued_job()
        if job is None:
            _stop_worker.wait(QUEUE_POLL_SECONDS)
            continue
        try:
            # run_job marks the job 'downloading' as its first act, so it leaves
            # the queue immediately and cannot be picked up twice.
            run_job(job["id"])
        except Exception:
            # run_job records its own failures; this is the last line of defence
            # so that one bad lecture cannot kill the worker and strand the rest.
            log.exception("[%s] unhandled error in worker", job["id"])
    log.info("job worker stopped")


def run_job(job_id: str) -> None:
    """The whole pipeline for one job. Runs in a background thread."""
    job = db.get_job(job_id)
    folder = job_dir(job_id)
    wav_path = os.path.join(folder, "audio.wav")
    keyterms = parse_keyterms(job["keyterms"])
    engine = job["engine"]
    wants_clips = job["purpose"] == "training"

    try:
        # 1. Get the audio and convert it to 16 kHz mono WAV.
        db.update_job(job_id, status="downloading")
        if job["source"] == "youtube":
            log.info("[%s] downloading from YouTube: %s", job_id, job["source_url"])
            source_path, title, _ = download_youtube_audio(job["source_url"], folder)
            db.update_job(job_id, title=title)
        else:
            source_path = os.path.join(folder, "source" + os.path.splitext(job["filename"])[1].lower())
        log.info("[%s] converting to 16 kHz mono WAV", job_id)
        convert_to_wav(source_path, wav_path)
        os.remove(source_path)  # the original can be large (video); audio.wav is all we need
        duration = wav_duration(wav_path)
        db.update_job(job_id, duration=round(duration, 2))
        log.info("[%s] audio ready: %.1f s", job_id, duration)

        # 2. The local model on its own: no ElevenLabs call, so no credits spent.
        if engine == "local":
            run_local_only(job_id, folder, wav_path, duration)
            db.update_job(job_id, status="done", segment_count=None)
            log.info("[%s] done (local model only, no training data produced)", job_id)
            return

        # 3. Transcribe with ElevenLabs Scribe v2.
        db.update_job(job_id, status="transcribing")
        log.info("[%s] transcribing with ElevenLabs (%d keyterms)", job_id, len(keyterms))
        credits_before = credits_used_now()
        started = time.time()
        transcript = transcribe(wav_path, keyterms)
        elapsed = time.time() - started
        cost, exact = measured_cost(credits_before, credits_used_now(), duration)
        db.update_job(job_id, elevenlabs_seconds=round(elapsed, 1), credits_used=cost)
        log.info("[%s] ElevenLabs took %.1fs and cost %s credits (%s)", job_id, elapsed,
                 cost, "measured" if exact else "estimated")
        with open(os.path.join(folder, "transcript.json"), "w", encoding="utf-8") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=1)
        with open(os.path.join(folder, "transcript.txt"), "w", encoding="utf-8") as f:
            f.write((transcript.get("text") or "").strip() + "\n")
        log.info("[%s] transcript saved (%d characters, language %s)", job_id,
                 len(transcript.get("text") or ""), transcript.get("language_code"))

        # 4. Work out where the pieces are. Only a training job writes the clips
        #    and the manifest to disk; a transcribe-only job just needs the
        #    boundaries so the website can show the text piece by piece.
        db.update_job(job_id, status="segmenting")
        if wants_clips:
            log.info("[%s] segmenting into training clips", job_id)
            segments = build_segments(job_id, folder, transcript, duration)
        else:
            log.info("[%s] segmenting for display only (no clips kept)", job_id)
            segments = plan_segments(timed_words(transcript), duration)
            with open(os.path.join(folder, "segments.json"), "w", encoding="utf-8") as f:
                json.dump(segments, f, ensure_ascii=False, indent=1)

        # 5. Optionally run the local model over the same pieces, to compare.
        if engine == "both":
            run_local_on_segments(job_id, folder, wav_path, segments, keep_clips=wants_clips)

        # 6. Pre-calculate acoustic health profile for the audio
        try:
            get_cached_health(folder, recompute=False)
        except Exception:
            pass

        db.update_job(job_id, status="done", segment_count=len(segments))
        log.info("[%s] done: %d segments%s", job_id, len(segments),
                 "" if wants_clips else " (transcript only, no training data)")

    except (AudioError, TranscriptionError, LocalAsrError) as e:
        log.warning("[%s] failed: %s", job_id, e)
        db.update_job(job_id, status="failed", error=str(e))
    except Exception as e:  # unexpected bug: keep the details in the log, show a short message
        log.exception("[%s] failed with an unexpected error", job_id)
        db.update_job(job_id, status="failed", error=f"Unexpected error: {type(e).__name__}: {e}"[:500])


# ---------------------------------------------------------------- API routes

class YoutubeJobRequest(BaseModel):
    url: str
    keyterms: list[str] = []
    engine: str = "elevenlabs"
    purpose: str = "training"


def check_choices(engine: str, purpose: str) -> tuple[str, str]:
    """Validate the two choices from the form, and settle the one case they interact.

    The local model returns no word timings, so a local-only job can never produce
    training clips; asking for them is a mistake rather than something to honour
    silently.
    """
    if engine not in db.ENGINES:
        raise HTTPException(status_code=400, detail=f"engine must be one of {db.ENGINES}")
    if purpose not in db.PURPOSES:
        raise HTTPException(status_code=400, detail=f"purpose must be one of {db.PURPOSES}")
    if engine == "local":
        purpose = "transcribe"
    return engine, purpose


@app.get("/api/health")
def health():
    return {"status": "ok", "api_key_configured": api_key_configured()}


# ElevenLabs is asked at most once a minute, however often the page is reloaded.
_credit_cache = {"checked_at": 0.0, "data": None}


@app.get("/api/usage")
def usage():
    if time.time() - _credit_cache["checked_at"] > 60:
        _credit_cache["data"] = get_credit_usage()
        _credit_cache["checked_at"] = time.time()
    return {"elevenlabs": _credit_cache["data"], "tool": db.usage_totals()}


@app.post("/api/jobs")
def create_youtube_job(request: YoutubeJobRequest):
    url = request.url.strip()
    if not is_youtube_url(url):
        raise HTTPException(status_code=400, detail="That is not a valid YouTube video link.")
    engine, purpose = check_choices(request.engine, request.purpose)
    job_id = new_job_id()
    os.makedirs(job_dir(job_id))
    db.create_job(job_id, "youtube", parse_keyterms(request.keyterms),
                  engine=engine, purpose=purpose, source_url=url)
    # Not started here: the worker picks it up. See the job queue above.
    log.info("[%s] queued (youtube, engine=%s, purpose=%s)", job_id, engine, purpose)
    return {"job_id": job_id, "queued_ahead": max(0, db.count_queued() - 1)}


class BatchJobRequest(BaseModel):
    """Several YouTube links at once, one per line."""
    urls: str
    keyterms: list[str] | str = ""
    engine: str = "elevenlabs"
    purpose: str = "training"


# Collecting twenty lectures through a one-link-at-a-time form is a chore, and a
# chore is where mistakes happen - most of all picking the wrong engine, because
# a lecture transcribed with the local model has no word timings and so can never
# become training data. One paste, one engine, one purpose, for the whole batch.
MAX_BATCH = 50


@app.post("/api/jobs/batch")
def create_batch_jobs(request: BatchJobRequest):
    engine, purpose = check_choices(request.engine, request.purpose)
    keyterms = parse_keyterms(request.keyterms)

    lines = [line.strip() for line in request.urls.splitlines() if line.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="No links were given.")

    # Reject the whole paste rather than quietly queueing the good half: finding
    # out which three of twenty links were dropped is worse than pasting again.
    bad = [line for line in lines
           if not is_youtube_url(line) and not is_playlist_url(line)]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"{len(bad)} of {len(lines)} lines are neither a YouTube video "
                   f"nor a playlist link, starting with: {bad[0][:80]}")

    # Expand any playlist into its videos. A course playlist is the natural way to
    # hand over twenty lectures, and copying twenty links out of one by hand is
    # exactly the sort of chore that ends with three of them missing.
    wanted: list[str] = []
    playlists = 0
    for line in lines:
        if is_playlist_url(line):
            playlists += 1
            try:
                videos = list_playlist_videos(line, MAX_BATCH + 1)
            except AudioError as e:
                # Without this the user gets a bare "Internal Server Error" for the
                # ordinary case of a mistyped or private playlist.
                raise HTTPException(status_code=400, detail=str(e)) from None
            if not videos:
                raise HTTPException(
                    status_code=400,
                    detail="That playlist has no videos this tool can read. "
                           "It may be private, or empty.")
            wanted.extend(v["url"] for v in videos)
        else:
            wanted.append(line)

    # Deduplicate by video id, so the same lecture written three different ways
    # (youtu.be/ID, watch?v=ID, watch?v=ID&list=...) is only queued once.
    seen_ids, unique = set(), []
    for url in wanted:
        video_id = youtube_video_id(url)
        if video_id and video_id not in seen_ids:
            seen_ids.add(video_id)
            unique.append(url)

    # Skip lectures already in the library. Without this, re-pasting a playlist
    # after adding one video would transcribe - and pay for - all the others again.
    already = {youtube_video_id(j["source_url"]): j["id"]
               for j in db.list_jobs(limit=1000)
               if j.get("source_url") and youtube_video_id(j["source_url"])}
    fresh = [u for u in unique if youtube_video_id(u) not in already]
    skipped_existing = len(unique) - len(fresh)

    if len(fresh) > MAX_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"That comes to {len(fresh)} new lectures; {MAX_BATCH} at a time "
                   "is the limit. Split the list.")
    if not fresh:
        raise HTTPException(
            status_code=400,
            detail=f"Nothing new to add: all {len(unique)} lecture(s) are already here.")

    created = []
    for url in fresh:
        job_id = new_job_id()
        os.makedirs(job_dir(job_id))
        db.create_job(job_id, "youtube", keyterms,
                      engine=engine, purpose=purpose, source_url=url)
        created.append(job_id)

    log.info("queued %d job(s) from a batch (%d playlist(s), %d already here, "
             "engine=%s, purpose=%s)",
             len(created), playlists, skipped_existing, engine, purpose)
    return {"job_ids": created, "queued": len(created),
            "playlists_expanded": playlists,
            "duplicates_ignored": len(wanted) - len(unique),
            "already_in_library": skipped_existing,
            "queued_total": db.count_queued()}


@app.post("/api/jobs/upload")
def create_upload_job(file: UploadFile = File(...),
                      keyterms: str = Form(""),
                      engine: str = Form("elevenlabs"),
                      purpose: str = Form("training")):
    engine, purpose = check_choices(engine, purpose)
    filename = os.path.basename(file.filename or "")
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{extension or filename}'. "
                   f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    job_id = new_job_id()
    folder = job_dir(job_id)
    os.makedirs(folder)
    # Copy the upload to disk in chunks (lecture videos can be over a gigabyte).
    with open(os.path.join(folder, "source" + extension), "wb") as out:
        shutil.copyfileobj(file.file, out, length=1024 * 1024)

    db.create_job(job_id, "upload", parse_keyterms(keyterms),
                  engine=engine, purpose=purpose, filename=filename)
    db.update_job(job_id, title=os.path.splitext(filename)[0])
    # Not started here: the worker picks it up. See the job queue above.
    log.info("[%s] created (upload: %s, engine=%s, purpose=%s)", job_id, filename, engine, purpose)
    return {"job_id": job_id}


@app.get("/api/jobs")
def list_jobs(purpose: str | None = None):
    """All jobs, or just one of the two lists: ?purpose=training or ?purpose=transcribe."""
    if purpose and purpose not in db.PURPOSES:
        raise HTTPException(status_code=400, detail=f"purpose must be one of {db.PURPOSES}")
    return [public_job(job) for job in db.list_jobs(purpose=purpose)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return public_job(get_job_or_404(job_id))


@app.post("/api/jobs/{job_id}/promote")
def promote_to_training(job_id: str):
    """Turn a finished transcribe-only job into training data.

    This spends no ElevenLabs credits: the word timings are already saved in
    transcript.json, so the clips and the manifest can be cut from the audio we
    still have. It takes a few seconds.
    """
    job = get_job_or_404(job_id)
    if job["purpose"] == "training":
        raise HTTPException(status_code=409, detail="This job is already training data.")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail=f"The job is {job['status']}, not finished.")
    if job["engine"] == "local":
        raise HTTPException(
            status_code=409,
            detail="Local-model jobs have no word timings, so they cannot become training data. "
                   "Transcribe it with ElevenLabs to use it for training.")

    folder = job_dir(job_id)
    transcript_path = os.path.join(folder, "transcript.json")
    wav_path = os.path.join(folder, "audio.wav")
    for path, what in ((transcript_path, "transcript"), (wav_path, "audio")):
        if not os.path.isfile(path):
            raise HTTPException(status_code=409, detail=f"The {what} for this job is missing.")

    with open(transcript_path, encoding="utf-8") as f:
        transcript = json.load(f)
    log.info("[%s] promoting to training data (no credits spent)", job_id)
    segments = build_segments(job_id, folder, transcript, wav_duration(wav_path))
    db.update_job(job_id, purpose="training", segment_count=len(segments))
    log.info("[%s] promoted: %d clips and a manifest written", job_id, len(segments))
    return public_job(get_job_or_404(job_id))


@app.get("/api/jobs/{job_id}/comparison")
def get_comparison(job_id: str):
    """ElevenLabs vs the local model, piece by piece, with scores.

    The two write English differently - ElevenLabs keeps Latin letters, the local
    model spells English words in Malayalam script - so the texts are put through
    the shared clean-up first, which includes transliterating ElevenLabs' English
    using the spelling table. Without that table filled in, the score mostly
    measures the alphabet rather than the model, which is why 'comparable' below
    says whether it is meaningful yet.
    """
    get_job_or_404(job_id)
    path = os.path.join(job_dir(job_id), "local_segments.json")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="This job has no local-model comparison.")
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)

    mapping = {}
    if os.path.isfile(ENGLISH_MAP_PATH):
        mapping = load_english_map(ENGLISH_MAP_PATH)

    out, references, predictions = [], [], []
    for row in rows:
        reference, _ = prepare_reference(row["elevenlabs"], mapping)
        prediction = normalise_text(row["local"])
        references.append(reference)
        predictions.append(prediction)
        out.append({**row, "scores": score_pair(reference, prediction)})

    coverage = map_coverage(ENGLISH_MAP_PATH, read_english_map().get("counts", {})) \
        if os.path.isfile(ENGLISH_MAP_PATH) else {"coverage_percent": 0.0}

    return {
        "job_id": job_id,
        "segments": out,
        "overall": score_pair(" ".join(references), " ".join(predictions)),
        "english_map_words": len(mapping),
        "coverage": coverage,
        # Below this, the number says more about alphabets than about recognition,
        # so the website must not present it as a result.
        "comparable": coverage.get("coverage_percent", 0) >= 50,
        "reference": "elevenlabs",
        "note": "Scores show agreement with ElevenLabs, not accuracy. "
                "ElevenLabs has its own mistakes.",
    }


def extract_wav_snippet(job_id: str, start_sec: float, end_sec: float) -> bytes:
    """Slice an uncompressed 16 kHz mono WAV file in sub-millisecond time."""
    get_job_or_404(job_id)
    wav_path = os.path.join(job_dir(job_id), "audio.wav")
    if not os.path.isfile(wav_path):
        raise HTTPException(status_code=404, detail="Audio file not available for this job.")

    start_sec = max(0.0, float(start_sec))
    end_sec = max(start_sec + 0.05, float(end_sec))
    if end_sec - start_sec > 30.0:
        end_sec = start_sec + 30.0

    try:
        with wave.open(wav_path, "rb") as wf:
            framerate = wf.getframerate()
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            total_frames = wf.getnframes()

            start_frame = min(total_frames, max(0, int(start_sec * framerate)))
            end_frame = min(total_frames, max(start_frame, int(end_sec * framerate)))
            count = max(0, end_frame - start_frame)

            wf.setpos(start_frame)
            frames = wf.readframes(count)

            buf = io.BytesIO()
            with wave.open(buf, "wb") as out:
                out.setnchannels(nchannels)
                out.setsampwidth(sampwidth)
                out.setframerate(framerate)
                out.writeframes(frames)
            return buf.getvalue()
    except Exception as e:
        log.exception("[%s] error extracting wav snippet %.2f-%.2f: %s", job_id, start_sec, end_sec, e)
        raise HTTPException(status_code=500, detail="Failed extracting audio snippet.")


_word_samples_cache = {"indexed_at": 0.0, "data": {}}


def get_word_samples_index() -> dict[str, list[dict]]:
    """Scan completed jobs with transcripts to index where English words were spoken."""
    now = time.time()
    if now - _word_samples_cache["indexed_at"] < 60 and _word_samples_cache["data"]:
        return _word_samples_cache["data"]

    index: dict[str, list[dict]] = {}
    latin_run = re.compile(r"[A-Za-z]+")

    job_titles = {}
    jobs = db.list_jobs()
    for j in jobs:
        job_titles[j["id"]] = j.get("title") or j["id"]

    for j in jobs:
        if j.get("status") != "done":
            continue
        folder = job_dir(j["id"])
        p = os.path.join(folder, "transcript.json")
        wav_path = os.path.join(folder, "audio.wav")
        if not (os.path.isfile(p) and os.path.isfile(wav_path)):
            continue

        try:
            with open(p, encoding="utf-8") as f:
                transcript = json.load(f)
        except Exception:
            continue

        words = transcript.get("words", [])
        for idx, item in enumerate(words):
            if item.get("type") != "word":
                continue
            text = unicodedata.normalize("NFC", item.get("text") or "")
            runs = [r.lower() for r in latin_run.findall(text)]
            for r in runs:
                if r not in index:
                    index[r] = []
                if len(index[r]) < 5:
                    ctx_start = max(0, idx - 4)
                    ctx_end = min(len(words), idx + 5)
                    parts = []
                    for k in range(ctx_start, ctx_end):
                        val = words[k].get("text", "")
                        parts.append(f"[{val}]" if k == idx else val)
                    context_str = "".join(parts).strip()

                    s = float(item.get("start", 0))
                    e = float(item.get("end", 0))
                    snippet_s = max(0.0, round(s - 0.7, 2))
                    snippet_e = round(e + 0.8, 2)

                    index[r].append({
                        "job_id": j["id"],
                        "job_title": job_titles.get(j["id"], j["id"]),
                        "word_spoken": item.get("text"),
                        "start": round(s, 2),
                        "end": round(e, 2),
                        "snippet_start": snippet_s,
                        "snippet_end": snippet_e,
                        "context": context_str,
                        "audio_url": f"/api/audio-snippet?job_id={j['id']}&start={snippet_s}&end={snippet_e}",
                    })

    _word_samples_cache["data"] = index
    _word_samples_cache["indexed_at"] = now
    return index


def read_english_map() -> dict:
    if not os.path.isfile(ENGLISH_MAP_PATH):
        return {"map": {}, "status": {}, "counts": {}, "candidates": {}}
    with open(ENGLISH_MAP_PATH, encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/english-map")
def get_english_map():
    """The English-to-Malayalam spelling table, commonest words first.

    Reviewing the commonest words first is what matters: in one lecture the fifty
    most frequent English words accounted for 64% of all English spoken.
    """
    data = read_english_map()
    counts = data.get("counts", {})
    mapping = data.get("map", {})
    status = data.get("status", {})
    candidates = data.get("candidates", {})
    samples_index = get_word_samples_index()

    words = sorted(set(mapping) | set(counts),
                   key=lambda w: (-counts.get(w, 0), w))
    entries = [{
        "word": word,
        "spelling": mapping.get(word, ""),
        "status": status.get(word, "missing"),
        "occurrences": counts.get(word, 0),
        # Other spellings the model produced for this word. More than one means the
        # model was inconsistent, which is a reason to look closely.
        "alternatives": candidates.get(word, {}),
        "source": data.get("source", {}).get(word),
        "sample": (samples_index.get(word) or [None])[0],
        "samples_count": len(samples_index.get(word) or []),
    } for word in words]

    return {"entries": entries, "coverage": map_coverage(ENGLISH_MAP_PATH, counts)}


@app.get("/api/english-map/occurrences/{word}")
def get_word_occurrences(word: str):
    """Return all sample audio occurrences of a spoken English word across finished lectures."""
    word = word.strip().lower()
    samples_index = get_word_samples_index()
    occurrences = samples_index.get(word, [])
    return {"word": word, "occurrences": occurrences}


@app.get("/api/audio-snippet")
def get_audio_snippet(job_id: str, start: float = 0.0, end: float = 5.0):
    """Stream a short slice of the lecture WAV file for hearing exact word pronunciation."""
    content = extract_wav_snippet(job_id, start, end)
    return Response(
        content=content,
        media_type="audio/wav",
        headers={
            "Content-Length": str(len(content)),
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
        },
    )


class SpellingUpdate(BaseModel):
    spelling: str
    approved: bool = True


@app.put("/api/english-map/{word}")
def set_english_spelling(word: str, update: SpellingUpdate):
    """Set a word's Malayalam spelling and mark it approved (or back to proposed)."""
    word = word.strip().lower()
    if not word:
        raise HTTPException(status_code=400, detail="No word given.")
    spelling = normalise_text(update.spelling)
    if update.approved and not spelling:
        raise HTTPException(status_code=400, detail="Cannot approve an empty spelling.")
    if spelling and has_latin(spelling):
        raise HTTPException(
            status_code=400,
            detail="The spelling must be in Malayalam letters - that is the whole point "
                   "of the table, since the local model cannot write English ones.")

    data = read_english_map()
    data.setdefault("map", {})[word] = spelling
    data.setdefault("status", {})[word] = "checked" if update.approved else "proposed"
    data.setdefault("source", {})[word] = "human" if update.approved else data.get("source", {}).get(word)
    with open(ENGLISH_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    log.info("spelling %s set to %r (%s)", word, spelling,
             "approved" if update.approved else "proposed")
    return {"word": word, "spelling": spelling,
            "status": data["status"][word],
            "coverage": map_coverage(ENGLISH_MAP_PATH, data.get("counts", {}))}


class BulkApproveRequest(BaseModel):
    words: list[str] | None = None


@app.post("/api/english-map/bulk-approve")
def bulk_approve_spellings(request: BulkApproveRequest | None = None):
    """Approve proposed Malayalam transliterations in bulk to unlock model comparison scores."""
    data = read_english_map()
    mapping = data.get("map", {})
    status = data.setdefault("status", {})
    target_words = request.words if (request and request.words) else list(mapping.keys())
    approved_count = 0
    for w in target_words:
        w_lower = w.strip().lower()
        spelling = mapping.get(w_lower, "")
        if spelling and not has_latin(spelling):
            status[w_lower] = "checked"
            data.setdefault("source", {})[w_lower] = "approved"
            approved_count += 1
    with open(ENGLISH_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    log.info("bulk approved %d spellings", approved_count)
    return {
        "approved": approved_count,
        "coverage": map_coverage(ENGLISH_MAP_PATH, data.get("counts", {})),
    }


@app.get("/api/jobs/{job_id}/audio")
def get_audio(job_id: str, inline: bool = False):
    """Stream or download the extracted 16 kHz mono WAV."""
    job = get_job_or_404(job_id)
    path = os.path.join(job_dir(job_id), "audio.wav")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Audio is not available for this job.")
    if inline:
        return FileResponse(
            path,
            media_type="audio/wav",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Disposition": "inline",
            },
        )
    return FileResponse(path, media_type="audio/wav",
                        filename=download_name(public_job(job)["title"], job_id))


@app.get("/api/jobs/{job_id}/stream-audio")
def stream_audio(job_id: str):
    """Stream the full 16 kHz mono WAV for in-browser HTML5 playback with Range seeking support."""
    return get_audio(job_id, inline=True)


@app.get("/api/jobs/{job_id}/transcript")
def get_transcript(job_id: str):
    job = get_job_or_404(job_id)
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail=f"Transcript not ready (job is {job['status']}).")
    folder = job_dir(job_id)

    # A local-model-only job has no ElevenLabs transcript and no segment boundaries:
    # the local model gives text without word timings.
    if job["engine"] == "local":
        with open(os.path.join(folder, "local_transcript.txt"), encoding="utf-8") as f:
            text = f.read().strip()
        corrected_path = os.path.join(folder, "transcript_corrected.json")
        is_corrected = os.path.isfile(corrected_path)
        segments = []
        if is_corrected:
            try:
                with open(corrected_path, encoding="utf-8") as f:
                    segments = json.load(f)
            except Exception:
                pass
        return {
            "job_id": job_id,
            "title": public_job(job)["title"],
            "language_code": None,
            "source": "local",
            "text": text,
            "local_text": None,
            "words": [],
            "segments": segments,
            "human_verified": is_corrected,
            "original_segments": None,
        }

    with open(os.path.join(folder, "transcript.txt"), encoding="utf-8") as f:
        text = f.read().strip()
    corrected_path = os.path.join(folder, "transcript_corrected.json")
    is_corrected = os.path.isfile(corrected_path)
    if is_corrected:
        try:
            with open(corrected_path, encoding="utf-8") as f:
                segments = json.load(f)
        except Exception:
            with open(os.path.join(folder, "segments.json"), encoding="utf-8") as f:
                segments = json.load(f)
    elif os.path.isfile(os.path.join(folder, "segments.json")):
        with open(os.path.join(folder, "segments.json"), encoding="utf-8") as f:
            segments = json.load(f)
    else:
        segments = []

    original_segments = None
    if is_corrected and os.path.isfile(os.path.join(folder, "segments.json")):
        try:
            with open(os.path.join(folder, "segments.json"), encoding="utf-8") as f:
                original_segments = json.load(f)
        except Exception:
            pass

    timed_words = []
    with open(os.path.join(folder, "transcript.json"), encoding="utf-8") as f:
        raw_tr = json.load(f)
        language = raw_tr.get("language_code")
        for w in raw_tr.get("words", []):
            if w.get("type") == "word" and w.get("text") and w.get("start") is not None and w.get("end") is not None:
                timed_words.append({
                    "text": w["text"].strip(),
                    "start": round(float(w["start"]), 2),
                    "end": round(float(w["end"]), 2),
                })

    local_text = None
    local_path = os.path.join(folder, "local_transcript.txt")
    if os.path.isfile(local_path):
        with open(local_path, encoding="utf-8") as f:
            local_text = f.read().strip()

    return {
        "job_id": job_id,
        "title": public_job(job)["title"],
        "language_code": language,
        "source": "elevenlabs",
        "text": text,
        "local_text": local_text,
        "words": timed_words,
        "human_verified": is_corrected,
        "segments": [
            # 'file' is only present on training jobs, where the clips were kept.
            {"start": s["start"], "end": s["end"], "text": s["text"], "file": s.get("file")}
            for s in segments
        ],
        "original_segments": [
            {"start": s["start"], "end": s["end"], "text": s["text"], "file": s.get("file")}
            for s in original_segments
        ] if original_segments else None,
    }


@app.get("/api/jobs/{job_id}/segments/{filename}")
def get_segment_clip(job_id: str, filename: str):
    """Stream a single cut audio clip (.wav) for interactive browser playback."""
    get_job_or_404(job_id)
    safe_name = os.path.basename(filename)
    if not safe_name.endswith(".wav"):
        raise HTTPException(status_code=400, detail="Clip must be a .wav file.")
    clip_path = os.path.join(job_dir(job_id), "segments", safe_name)
    if not os.path.isfile(clip_path):
        raise HTTPException(status_code=404, detail="Segment clip file not found.")
    return FileResponse(clip_path, media_type="audio/wav")


class CorrectionSegment(BaseModel):
    start: float
    end: float
    text: str
    file: str | None = None


class CorrectionsRequest(BaseModel):
    segments: list[CorrectionSegment]
    verified: bool = True


@app.put("/api/jobs/{job_id}/transcript/corrections")
def save_transcript_corrections(job_id: str, request: CorrectionsRequest):
    """Save human-verified Malayalam transcript corrections and sync to NeMo training manifest."""
    job = get_job_or_404(job_id)
    folder = job_dir(job_id)

    normalized_segments = []
    manifest_lines = []
    for s in request.segments:
        cleaned_text = normalise_text(s.text)
        duration = round(s.end - s.start, 3)
        item = {
            "start": round(s.start, 3),
            "end": round(s.end, 3),
            "duration": duration,
            "text": cleaned_text,
            "file": s.file,
        }
        normalized_segments.append(item)
        if s.file:
            manifest_lines.append(json.dumps({
                "audio_filepath": s.file,
                "duration": duration,
                "text": cleaned_text,
            }, ensure_ascii=False))

    corrected_path = os.path.join(folder, "transcript_corrected.json")
    with open(corrected_path, "w", encoding="utf-8") as f:
        json.dump(normalized_segments, f, ensure_ascii=False, indent=1)

    if manifest_lines:
        with open(os.path.join(folder, "manifest.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(manifest_lines) + ("\n" if manifest_lines else ""))

    if normalized_segments:
        full_text = " ".join(s["text"] for s in normalized_segments if s["text"])
        with open(os.path.join(folder, "transcript_corrected.txt"), "w", encoding="utf-8") as f:
            f.write(full_text + "\n")

    log.info("[%s] saved %d human-corrected segments", job_id, len(normalized_segments))
    return {"ok": True, "human_verified": True, "segment_count": len(normalized_segments)}


@app.delete("/api/jobs/{job_id}/transcript/corrections")
def revert_transcript_corrections(job_id: str):
    """Revert human corrections back to the automated transcription."""
    get_job_or_404(job_id)
    folder = job_dir(job_id)
    corrected_path = os.path.join(folder, "transcript_corrected.json")
    if os.path.isfile(corrected_path):
        os.remove(corrected_path)
    corrected_txt = os.path.join(folder, "transcript_corrected.txt")
    if os.path.isfile(corrected_txt):
        os.remove(corrected_txt)

    segments_path = os.path.join(folder, "segments.json")
    if os.path.isfile(segments_path):
        with open(segments_path, encoding="utf-8") as f:
            orig = json.load(f)
        manifest_lines = [
            json.dumps({
                "audio_filepath": s["file"],
                "duration": s.get("duration", round(s["end"] - s["start"], 3)),
                "text": s["text"],
            }, ensure_ascii=False)
            for s in orig if s.get("file")
        ]
        with open(os.path.join(folder, "manifest.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(manifest_lines) + ("\n" if manifest_lines else ""))

    log.info("[%s] reverted human corrections to automated segments", job_id)
    return {"ok": True, "human_verified": False}


@app.get("/api/jobs/{job_id}/health")
def api_get_audio_health(job_id: str):
    """Retrieve acoustic quality & health analysis (SNR, RMS loudness, clipping, speech activity)."""
    get_job_or_404(job_id)
    folder = job_dir(job_id)
    health = get_cached_health(folder, recompute=False)
    if not health:
        raise HTTPException(status_code=404, detail="Audio file not available for acoustic analysis.")
    return health


@app.post("/api/jobs/{job_id}/health/analyze")
def api_reanalyze_audio_health(job_id: str):
    """Force re-analysis of audio acoustics."""
    get_job_or_404(job_id)
    folder = job_dir(job_id)
    health = get_cached_health(folder, recompute=True)
    if not health:
        raise HTTPException(status_code=400, detail="Could not analyze audio file.")
    return health


# ---------------------------------------------------------------- Dataset Export (Part 2 Hub)

class DatasetExportRequest(BaseModel):
    train: float = 0.8
    val: float = 0.1
    test: float = 0.1
    name: str | None = None
    pin_test: list[str] = []
    exclude: list[str] = []
    copy: bool = False


@app.get("/api/dataset/summary")
def api_dataset_summary():
    """Summary of training lectures, collected hours, and existing dataset exports."""
    return get_dataset_summary()


@app.post("/api/dataset/export")
def api_export_dataset(request: DatasetExportRequest):
    """Package and export collected training lectures into NeMo train/val/test splits."""
    ratios = {"train": request.train, "val": request.val, "test": request.test}
    try:
        info = export_dataset(
            ratios=ratios,
            name=request.name,
            pin_test=request.pin_test,
            exclude=request.exclude,
            copy=request.copy,
            dry_run=False,
            make_archive=True,
        )
        return info
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("Dataset export failed")
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")


@app.get("/api/dataset/download/{name}")
def api_download_dataset(name: str):
    """Download the full dataset tarball (.tar.gz) containing audio clips and manifests."""
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "", name)
    archive_path = os.path.join(db.DATA_DIR, "datasets", f"{safe_name}.tar.gz")
    if not os.path.isfile(archive_path):
        dataset_folder = os.path.join(db.DATA_DIR, "datasets", safe_name)
        if os.path.isdir(dataset_folder):
            try:
                archive_path = create_dataset_archive(safe_name)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed packaging archive: {e}")
        else:
            raise HTTPException(status_code=404, detail=f"Dataset '{name}' not found.")
    return FileResponse(archive_path, media_type="application/gzip", filename=f"{safe_name}.tar.gz")


@app.get("/api/dataset/info/{name}")
def api_get_dataset_info(name: str):
    """Metadata and split details for an exported dataset."""
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "", name)
    info_path = os.path.join(db.DATA_DIR, "datasets", safe_name, "dataset_info.json")
    if not os.path.isfile(info_path):
        raise HTTPException(status_code=404, detail="Dataset info not found.")
    with open(info_path, encoding="utf-8") as f:
        return json.load(f)

