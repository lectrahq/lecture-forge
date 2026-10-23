"""
Talking to the local speech recognition service (the 'asr' container).

That service runs AI4Bharat's Malayalam IndicConformer on this server's processor.
It is free to use - no ElevenLabs credits - but less accurate, so it is offered as
a choice rather than a replacement.
"""

import logging
import os
import time

import httpx

log = logging.getLogger("local_asr")

ASR_URL = os.environ.get("ASR_URL", "http://asr:9000")

# A long lecture is cut into hundreds of clips, and the model runs at roughly
# 14x real time on this processor, so an hour of audio takes about four minutes.
# The generous ceiling is for the rare very long upload.
TIMEOUT_SECONDS = 3600


class LocalAsrError(Exception):
    """A problem the user should see (shown as the job's error message)."""


def available() -> bool:
    """Is the service up? Used for the health endpoint, never to gate a job."""
    try:
        response = httpx.get(f"{ASR_URL}/health", timeout=5)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


# Small on purpose. The model alone holds ~1.8 GB, and each extra clip in a batch
# adds its activations on top; a batch of 8 thirty-second clips is enough to pass
# the container's memory limit and get the service killed outright. On a processor
# (rather than a graphics card) a bigger batch barely helps anyway - measured here,
# eight clips took 11.1s at batch 1 against 9.7s at batch 4 - so the safety is
# nearly free.
DEFAULT_BATCH_SIZE = 2


def transcribe_clips(paths: list[str], decoder: str = "ctc",
                     batch_size: int = DEFAULT_BATCH_SIZE,
                     progress_path: str | None = None) -> list[str]:
    """Transcribe a list of WAV files, returning one piece of text per file."""
    texts, _ = transcribe_clips_with_words(paths, decoder=decoder, batch_size=batch_size,
                                           progress_path=progress_path)
    return texts


def transcribe_clips_with_words(paths: list[str], decoder: str = "ctc",
                                batch_size: int = DEFAULT_BATCH_SIZE,
                                progress_path: str | None = None) -> tuple[list[str], list[list[dict]]]:
    """Transcribe a list of WAV files, returning both text and exact acoustic word timestamps per clip."""
    if not paths:
        return [], []

    max_retries = 120  # retry for up to 10 minutes if ASR is busy with another job
    response = None
    for attempt in range(max_retries):
        try:
            response = httpx.post(
                f"{ASR_URL}/transcribe",
                json={"paths": paths, "decoder": decoder, "batch_size": batch_size,
                      "progress_path": progress_path},
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as e:
            raise LocalAsrError(
                "Could not reach the local speech recognition service. "
                f"Is the 'asr' container running? ({type(e).__name__})"
            ) from None

        if response.status_code == 409:
            log.info("local ASR service busy (409), waiting 5s (attempt %d/%d)...", attempt + 1, max_retries)
            time.sleep(5)
            continue
        break

    if response is None or response.status_code != 200:
        detail = ""
        if response is not None:
            try:
                detail = response.json().get("detail", "")
            except ValueError:
                detail = response.text[:300]
            status = response.status_code
        else:
            status = "timeout"
        raise LocalAsrError(f"Local speech recognition failed ({status}): {detail}")

    data = response.json()
    texts = data.get("texts", [])
    chunk_words = data.get("chunk_words", [[] for _ in texts])
    if len(texts) != len(paths):
        raise LocalAsrError(
            f"The local model returned {len(texts)} results for {len(paths)} clips."
        )
    log.info("local model transcribed %d clips in %ss", len(texts), data.get("seconds_taken"))
    return texts, chunk_words
