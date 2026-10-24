"""
Local speech recognition service.

Runs AI4Bharat's Malayalam IndicConformer on this server's processor, so lectures
can be transcribed for free instead of spending ElevenLabs credits. It is a small
HTTP service that the backend calls; it is never exposed to the internet.

The model is loaded when a request arrives and released when it finishes, so the
roughly 2 GB it needs is only held while a job is actually being transcribed.
Loading takes about 8 seconds, which is nothing next to a 40-minute lecture.
"""

import gc
import json
import logging
import os
import threading
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import common

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s asr: %(message)s")
log = logging.getLogger("asr")

DATA_DIR = os.environ.get("DATA_DIR", "/data")

# Only one transcription at a time: two models in memory at once would not fit in
# the container's limit, and the processor is shared with the rest of the site.
_busy = threading.Lock()

app = FastAPI(title="Local ASR (IndicConformer)")


class TranscribeRequest(BaseModel):
    paths: list[str]              # WAV files inside the shared data folder
    decoder: str = "rnnt"         # 'rnnt' produces grammatical words; 'ctc' for fast greedy
    # Where to report how far along this is. Both containers share the data folder,
    # so the backend can read it while this one request is still running - which is
    # how the website shows progress without the model being reloaded per batch.
    progress_path: str | None = None
    # Kept small: the model holds ~1.8 GB and each clip in a batch adds its own
    # working memory, so a large batch gets this service killed by the memory limit.
    # A bigger batch barely helps on a processor anyway.
    batch_size: int = 2


# Refuse batches that are known to be unsafe inside this container's memory limit.
MAX_SAFE_BATCH = 4

# Clips are transcribed in groups of this many, with the memory handed back between
# groups. Without this, memory creeps up across a long lecture and the service is
# eventually killed part-way through - measured: a 41-minute lecture finished at
# 2.2 GB, while a 42-minute one died at 78%. Grouping keeps the peak flat, so the
# length of the lecture stops mattering.
GROUP_SIZE = 16


def _safe_progress_path(path: str | None) -> str | None:
    """Progress is written into the shared data folder, and nowhere else."""
    if not path:
        return None
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(DATA_DIR, path))
    if not full.startswith(os.path.realpath(DATA_DIR) + os.sep):
        raise HTTPException(status_code=400, detail="progress_path is outside the data folder")
    return full


def _write_progress(path: str | None, done: int, total: int, started: float) -> None:
    """Say how far along we are, for the website to pick up.

    Progress is a convenience: if writing it fails, the transcription carries on.
    """
    if not path:
        return
    try:
        elapsed = time.time() - started
        remaining = (elapsed / done * (total - done)) if done else None
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"done": done, "total": total,
                       "elapsed_seconds": round(elapsed),
                       "seconds_left": round(remaining) if remaining else None}, f)
    except OSError:
        pass


def _rss_mb() -> int:
    """How much memory this process is holding, in MB."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return -1


@app.get("/health")
def health():
    return {
        "status": "ok",
        "busy": _busy.locked(),
        "model": common.DEFAULT_REPO,
        "language": common.LANGUAGE_ID,
        "token_configured": bool(os.environ.get("HF_TOKEN", "").strip()),
    }


def _safe_path(path: str) -> str:
    """Only allow files inside the shared data folder.

    The backend is the only caller, but a path check costs nothing and keeps a
    bug in the caller from turning into "read any file on the system".
    """
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(DATA_DIR, path))
    if not full.startswith(os.path.realpath(DATA_DIR) + os.sep):
        raise HTTPException(status_code=400, detail=f"path is outside the data folder: {path}")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail=f"audio file not found: {path}")
    return full


@app.post("/transcribe")
def transcribe(request: TranscribeRequest):
    if not request.paths:
        return {"texts": [], "seconds_taken": 0.0}
    if request.decoder not in ("ctc", "rnnt"):
        raise HTTPException(status_code=400, detail="decoder must be 'ctc' or 'rnnt'")
    # Clamp rather than honour a batch size that would get this process killed:
    # a killed service looks to the caller like a network failure, which is far
    # harder to understand than a slightly slower transcription.
    batch_size = max(1, min(request.batch_size, MAX_SAFE_BATCH))

    paths = [_safe_path(p) for p in request.paths]
    progress_path = _safe_progress_path(request.progress_path)

    if not _busy.acquire(blocking=False):
        # Better to say so plainly than to queue up and run out of memory.
        raise HTTPException(status_code=409,
                            detail="The local model is already transcribing another job. Try again shortly.")
    started = time.time()
    model = None
    try:
        log.info("loading model (%s decoder) for %d clips, batch %d",
                 request.decoder, len(paths), batch_size)
        model = common.load_model(request.decoder)
        # print, not the logger: NeMo replaces the root logger's handlers while it
        # loads, and anything logged after that silently disappears.
        print(f"[asr] model loaded, holding {_rss_mb()} MB", flush=True)

        texts: list[str] = []
        chunk_words: list[list[dict]] = []
        transcribing_started = time.time()
        _write_progress(progress_path, 0, len(paths), transcribing_started)
        for start in range(0, len(paths), GROUP_SIZE):
            group = paths[start:start + GROUP_SIZE]
            t_batch, w_batch = common.transcribe_with_words(model, group, batch_size, decoder=request.decoder)
            texts.extend(t_batch)
            chunk_words.extend(w_batch)
            # Give the memory back before starting the next group, so a long
            # lecture does not slowly climb into the container's limit.
            gc.collect()
            _write_progress(progress_path, len(texts), len(paths), transcribing_started)
            print(f"[asr] {len(texts)}/{len(paths)} clips done, holding {_rss_mb()} MB", flush=True)

        took = time.time() - started
        print(f"[asr] transcribed {len(texts)} clips in {took:.1f}s, holding {_rss_mb()} MB", flush=True)
        return {"texts": texts, "chunk_words": chunk_words, "seconds_taken": round(took, 1),
                "decoder": request.decoder, "batch_size": batch_size}
    except SystemExit as e:
        # common.checkpoint_path() raises SystemExit with a readable explanation
        # (gated model, missing token). Do not let it kill the service.
        raise HTTPException(status_code=502, detail=str(e)) from None
    finally:
        # Hand the memory back rather than holding ~2 GB until the next job.
        if model is not None:
            del model
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache() if torch.cuda.is_available() else None
        except Exception:
            pass
        _busy.release()
