"""
Speech-to-text with ElevenLabs Scribe v2 (official `elevenlabs` Python SDK).

The API key is read from the ELEVENLABS_API_KEY environment variable
(set in backend/.env). It is never logged or returned to the browser.
"""

import os

import httpx
from elevenlabs.client import ElevenLabs
from elevenlabs.core.api_error import ApiError

MODEL_ID = "scribe_v2"

# Long lectures (1-2 hours) can take several minutes to upload and transcribe,
# so allow up to an hour per request instead of the SDK's 240 s default.
REQUEST_TIMEOUT_SECONDS = 3600


class TranscriptionError(Exception):
    """A problem the user should see (shown as the job's error message)."""


def api_key_configured() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())


def transcribe(wav_path: str, keyterms: list[str]) -> dict:
    """Send the WAV file to Scribe and return the raw JSON response as a dict."""
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise TranscriptionError("ELEVENLABS_API_KEY is not set in backend/.env.")

    client = ElevenLabs(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)
    options = {
        "model_id": MODEL_ID,
        "timestamps_granularity": "word",  # start/end time for every word (needed for segmenting)
        "tag_audio_events": False,         # no "(laughter)" etc. in the training text
        # language_code is left out on purpose: the lectures mix Malayalam and English,
        # so Scribe auto-detects the language.
    }
    if keyterms:
        options["keyterms"] = keyterms     # technical words to help recognition

    try:
        # The open file is streamed to the API, not read into memory all at once.
        # with_raw_response lets us keep the exact JSON that ElevenLabs sent back.
        with open(wav_path, "rb") as audio:
            response = client.speech_to_text.with_raw_response.convert(file=audio, **options)
        return response._response.json()
    except ApiError as e:
        raise TranscriptionError(_explain_api_error(e)) from None
    except httpx.HTTPError as e:  # network problems, timeouts
        raise TranscriptionError(f"Could not reach ElevenLabs: {type(e).__name__}: {e}") from None


# Measured on this project: a 2542.64 s lecture cost exactly 2,825 credits.
CREDITS_PER_SECOND = 2825 / 2542.64


def estimate_credits(audio_seconds: float) -> float:
    """What a job of this length should cost, from the rate measured here."""
    return round(audio_seconds * CREDITS_PER_SECOND, 1)


def credits_used_now() -> int | None:
    """Current credit count, or None if the key may not read it.

    Deliberately not the cached /api/usage value: that is 60 seconds stale, which
    once made a before/after measurement report zero when credits had been spent.
    """
    usage = get_credit_usage()
    return usage["credits_used"] if usage.get("available") else None


def measured_cost(before: int | None, after: int | None, audio_seconds: float) -> tuple[float | None, bool]:
    """Work out what a job cost, and say whether the figure was measured or estimated.

    The before/after difference is exact when one job runs at a time. Two jobs
    transcribing at once would have their spending mixed together, so an
    implausible difference is discarded in favour of the estimate rather than
    reported as fact.
    """
    estimate = estimate_credits(audio_seconds)
    if before is None or after is None:
        return (estimate if audio_seconds else None), False
    delta = after - before
    if delta <= 0 or (estimate and delta > estimate * 2):
        return (estimate if audio_seconds else None), False
    return float(delta), True


def get_credit_usage() -> dict:
    """Credits used and left on the ElevenLabs plan this billing period.

    Needs the API key's "User: Read" permission. ElevenLabs does not let a key read
    its own per-key credit limit, so these are the numbers for the whole account.
    """
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        return {"available": False, "reason": "ELEVENLABS_API_KEY is not set in backend/.env."}

    client = ElevenLabs(api_key=api_key, timeout=30)
    try:
        subscription = client.user.subscription.get()
    except ApiError as e:
        if "missing_permissions" in str(e.body):
            return {"available": False,
                    "reason": "To show credits, give the API key the 'User: Read' permission "
                              "(ElevenLabs → Developers → API keys → edit key)."}
        return {"available": False, "reason": _explain_api_error(e)}
    except httpx.HTTPError as e:
        return {"available": False, "reason": f"Could not reach ElevenLabs: {type(e).__name__}"}

    used, limit = subscription.character_count, subscription.character_limit
    return {
        "available": True,
        "tier": subscription.tier,
        "credits_used": used,
        "credits_limit": limit,
        "credits_left": max(limit - used, 0),
        "resets_at": subscription.next_character_count_reset_unix,  # unix time, or None
    }


def _explain_api_error(error: ApiError) -> str:
    """Turn an ElevenLabs error into a short, readable message (never includes the key)."""
    status = error.status_code
    detail = error.body.get("detail", error.body) if isinstance(error.body, dict) else error.body
    if isinstance(detail, dict):
        code = str(detail.get("status") or detail.get("code") or "")
        message = str(detail.get("message") or detail)
    else:
        code, message = "", str(detail)
    message = message[:400]

    if status == 401:
        return "ElevenLabs rejected the API key (401). Check ELEVENLABS_API_KEY in backend/.env."
    if "quota" in code or "credit" in message.lower() or status == 402:
        return f"ElevenLabs account is out of credits ({status}): {message}"
    if status == 429:
        return f"ElevenLabs rate limit or concurrency limit reached (429): {message}"
    return f"ElevenLabs API error ({status}): {message}"
