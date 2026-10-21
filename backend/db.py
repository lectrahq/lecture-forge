"""
Job tracking in a small SQLite database (data/jobs.db).

Each lecture we process is one "job" row. The background pipeline updates the
row as it moves through the stages, and the API reads it back for the website.
"""

import os
import sqlite3
from datetime import datetime, timezone

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "jobs.db")

# Every status a job can have, in order. "failed" can happen at any stage.
STATUSES = ["queued", "downloading", "transcribing", "segmenting",
            "transcribing_local", "done", "failed"]

# Which model(s) transcribe a job.
ENGINES = ["elevenlabs", "local", "both"]

# What the job is for. Only 'training' jobs produce audio clips and a manifest,
# and only they are ever included in a training dataset.
PURPOSES = ["training", "transcribe"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    # A new short-lived connection per call keeps things simple and thread-safe
    # (background jobs run in worker threads).
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with _connect() as conn:
        # WAL mode lets the API read while a background job is writing.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id            TEXT PRIMARY KEY,
                source        TEXT NOT NULL,      -- 'youtube' or 'upload'
                engine        TEXT NOT NULL DEFAULT 'elevenlabs',  -- see ENGINES
                purpose       TEXT NOT NULL DEFAULT 'training',    -- see PURPOSES
                source_url    TEXT,               -- YouTube link (youtube jobs)
                filename      TEXT,               -- original file name (upload jobs)
                title         TEXT,
                duration      REAL,               -- seconds of audio
                elevenlabs_seconds REAL,          -- how long ElevenLabs took
                local_seconds      REAL,          -- how long the local model took
                credits_used       REAL,          -- ElevenLabs credits this job cost
                keyterms      TEXT,               -- comma-separated
                status        TEXT NOT NULL,
                error         TEXT,
                segment_count INTEGER,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            )
            """
        )
        # Older databases predate these two columns. Jobs made then always used
        # ElevenLabs and always produced clips, so the defaults are correct for them.
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        for column, default in (("engine", "elevenlabs"), ("purpose", "training")):
            if column not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
        # How long each transcriber took, so the two can be compared on speed as
        # well as on accuracy. Null means that transcriber was not used.
        for column in ("elevenlabs_seconds", "local_seconds", "credits_used"):
            if column not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} REAL")


def create_job(job_id: str, source: str, keyterms: list[str],
               engine: str = "elevenlabs", purpose: str = "training",
               source_url: str | None = None, filename: str | None = None) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, source, engine, purpose, source_url, filename, keyterms,"
            " status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
            (job_id, source, engine, purpose, source_url, filename, ",".join(keyterms), now, now),
        )


# Only these columns may be changed by update_job (protects the SQL below).
_UPDATABLE = {"title", "duration", "status", "error", "segment_count", "purpose",
              "elevenlabs_seconds", "local_seconds", "credits_used"}


def update_job(job_id: str, **fields) -> None:
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"cannot update columns: {unknown}")
    fields["updated_at"] = _now()
    columns = ", ".join(f"{name} = ?" for name in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*fields.values(), job_id))


def get_job(job_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(limit: int = 200, purpose: str | None = None) -> list[dict]:
    """Newest first. Pass purpose='training' or 'transcribe' for one of the two lists."""
    query = "SELECT * FROM jobs"
    params: list = []
    if purpose:
        query += " WHERE purpose = ?"
        params.append(purpose)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def usage_totals() -> dict:
    """How much this tool has transcribed successfully, over all time."""
    with _connect() as conn:
        count, seconds = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(duration), 0) FROM jobs WHERE status = 'done'"
        ).fetchone()
    return {"jobs_done": count, "audio_seconds": round(seconds, 1)}


def fail_unfinished_jobs() -> int:
    """Called at startup: jobs that were mid-way when the server stopped can't resume."""
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE jobs SET status = 'failed', error = ?, updated_at = ?"
            " WHERE status NOT IN ('done', 'failed')",
            ("The server restarted while this job was running. Please submit it again.", _now()),
        )
        return cursor.rowcount
