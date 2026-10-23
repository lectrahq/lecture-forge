"""
End-to-end checks for the whole backend, run against the live API.

Run inside the backend container:
    docker compose exec -T backend python /app/tests/api_suite.py
    docker compose exec -T backend python /app/tests/api_suite.py --include-paid

By default only the free checks run: the local model costs nothing, so the suite can be
run as often as wanted. The paid checks use ElevenLabs on a 19-second clip (about 21
credits) and need --include-paid.

Rules this suite follows, learned from real failures on this project:
  * Every check is an assertion that prints expected and actual. A check that prints
    nothing is not a check.
  * No output is ever silenced. A script that fails silently looks like a script that passed.
  * Jobs created here are deleted at the end, pass or fail.

Exits non-zero if anything fails, so it can gate a deployment.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

import httpx

API = os.environ.get("API_URL", "http://127.0.0.1:8000/api")
DATA = os.environ.get("DATA_DIR", "/data")

# 19 seconds, public, tiny. Short on purpose: the paid checks spend real credits.
SHORT_VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

_results: list[tuple[bool, str, str]] = []
_created_jobs: list[str] = []


# ---------------------------------------------------------------- helpers

def check(name: str, actual, expected) -> bool:
    """Assert equality, recording both sides so a failure explains itself."""
    ok = actual == expected
    _results.append((ok, name, "" if ok else f"expected {expected!r}, got {actual!r}"))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"\n          expected {expected!r}\n          got      {actual!r}"),
          flush=True)
    return ok


def check_true(name: str, actual, note: str = "") -> bool:
    return check(name + (f" ({note})" if note else ""), bool(actual), True)


def get(path: str, **kwargs):
    return httpx.get(API + path, timeout=60, **kwargs)


def post(path: str, **kwargs):
    return httpx.post(API + path, timeout=120, **kwargs)


def put(path: str, **kwargs):
    return httpx.put(API + path, timeout=60, **kwargs)


def delete(path: str, **kwargs):
    return httpx.delete(API + path, timeout=60, **kwargs)


def credits_now() -> int | None:
    """Uncached credit reading, straight from ElevenLabs.

    The /api/usage endpoint caches for 60 seconds, which once made a test report
    'no credits spent' when credits had in fact been spent.
    """
    sys.path.insert(0, "/app")
    from transcriber import get_credit_usage
    usage = get_credit_usage()
    return usage.get("credits_used") if usage.get("available") else None


def start_job(**body) -> str:
    response = post("/jobs", json=body)
    response.raise_for_status()
    job_id = response.json()["job_id"]
    _created_jobs.append(job_id)
    return job_id


def wait_for(job_id: str, timeout: int = 900) -> dict:
    """Poll until the job finishes. Prints each state change so a hang is visible."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        job = get(f"/jobs/{job_id}").json()
        if job["status"] != last:
            last = job["status"]
            print(f"         … {last}", flush=True)
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(5)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


def job_files(job_id: str) -> set[str]:
    folder = os.path.join(DATA, job_id)
    return set(os.listdir(folder)) if os.path.isdir(folder) else set()


def cleanup():
    """Remove every job this suite created, whatever happened."""
    if not _created_jobs:
        return
    print("\n--- cleaning up", flush=True)
    conn = sqlite3.connect(os.path.join(DATA, "jobs.db"))
    for job_id in _created_jobs:
        folder = os.path.join(DATA, job_id)
        if os.path.isdir(folder):
            shutil.rmtree(folder, ignore_errors=True)
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        print(f"  removed {job_id}", flush=True)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- the checks

def test_basics():
    print("\n=== service is up and answering", flush=True)
    health = get("/health").json()
    check("health status", health["status"], "ok")
    check_true("ElevenLabs key configured", health["api_key_configured"])

    usage = get("/usage").json()
    check_true("usage reports ElevenLabs", usage["elevenlabs"].get("available"))
    check_true("usage reports tool totals", "jobs_done" in usage["tool"])

    check("job list is a list", isinstance(get("/jobs").json(), list), True)
    check("training filter works", get("/jobs?purpose=training").status_code, 200)
    check("transcribe filter works", get("/jobs?purpose=transcribe").status_code, 200)
    check("bad purpose filter rejected", get("/jobs?purpose=nonsense").status_code, 400)


def test_rejections():
    print("\n=== bad input is refused, not accepted and failed later", flush=True)
    check("non-YouTube link rejected", post("/jobs", json={"url": "https://vimeo.com/1"}).status_code, 400)
    check("unknown engine rejected",
          post("/jobs", json={"url": SHORT_VIDEO, "engine": "magic"}).status_code, 400)
    check("unknown purpose rejected",
          post("/jobs", json={"url": SHORT_VIDEO, "purpose": "magic"}).status_code, 400)
    check("unknown job is 404", get("/jobs/does-not-exist").status_code, 404)
    check("transcript of unknown job is 404", get("/jobs/does-not-exist/transcript").status_code, 404)
    check("audio of unknown job is 404", get("/jobs/does-not-exist/audio").status_code, 404)


def test_local_job():
    """The free path, and the one that must never touch ElevenLabs."""
    print("\n=== local model only (free)", flush=True)
    before = credits_now()
    job_id = start_job(url=SHORT_VIDEO, engine="local", purpose="training")
    job = wait_for(job_id)

    check("local job finished", job["status"], "done")
    check("local job has no error", job["error"], None)
    check("engine recorded", job["engine"], "local")
    check("purpose forced to transcribe", job["purpose"], "transcribe")
    check_true("local transcript written", job["has_local_transcript"])
    check("ElevenLabs timer empty", job["elevenlabs_seconds"], None)
    check_true("local timer recorded", job["local_seconds"])

    files = job_files(job_id)
    check_true("audio.wav kept", "audio.wav" in files)
    check_true("local_transcript.txt written", "local_transcript.txt" in files)
    check("no training clips written", "segments" in files, False)
    check("no manifest written", "manifest.jsonl" in files, False)
    check("progress file cleaned up", "_progress.json" in files, False)
    check("chunk folder cleaned up", "_local_chunks" in files, False)

    transcript = get(f"/jobs/{job_id}/transcript").json()
    check("transcript marked as local", transcript["source"], "local")
    check("no segments for a local job", len(transcript["segments"]), 0)
    check_true("transcript has text", transcript["text"].strip())

    in_transcribe = [j["job_id"] for j in get("/jobs?purpose=transcribe").json()]
    in_training = [j["job_id"] for j in get("/jobs?purpose=training").json()]
    check_true("appears in the Transcripts list", job_id in in_transcribe)
    check("absent from the Training list", job_id in in_training, False)

    check("cannot be promoted (no word timings)", job["can_promote"], False)
    check("promote is refused", post(f"/jobs/{job_id}/promote").status_code, 409)

    # Acoustic quality & audio health inspection
    health_resp = get(f"/jobs/{job_id}/health")
    check("audio health endpoint responds", health_resp.status_code, 200)
    health_data = health_resp.json()
    check_true("audio health has score", 0 <= health_data.get("score", -1) <= 100)
    check_true("audio health has snr_db", "snr_db" in health_data)
    check_true("audio health has rating", health_data.get("rating") in ("Optimal", "Good", "Fair", "Poor"))

    reanalyze_resp = post(f"/jobs/{job_id}/health/analyze")
    check("reanalyze audio health succeeds", reanalyze_resp.status_code, 200)

    # Human verification and corrections
    save_resp = put(f"/jobs/{job_id}/transcript/corrections", json={
        "segments": [{"start": 0.0, "end": 2.0, "text": "പരീക്ഷണ തിരുത്തൽ", "file": None}],
        "verified": True,
    })
    check("save human corrections succeeds", save_resp.status_code, 200)
    check_true("human_verified marked true", get(f"/jobs/{job_id}/transcript").json().get("human_verified"))

    revert_resp = delete(f"/jobs/{job_id}/transcript/corrections")
    check("revert human corrections succeeds", revert_resp.status_code, 200)
    check("human_verified marked false", get(f"/jobs/{job_id}/transcript").json().get("human_verified"), False)

    after = credits_now()
    check("NO ElevenLabs credits spent", after, before)


def test_elevenlabs_job_and_promote():
    """Costs about 21 credits on a 19-second clip."""
    print("\n=== ElevenLabs, then promote (paid: ~21 credits)", flush=True)
    before = credits_now()
    job_id = start_job(url=SHORT_VIDEO, engine="elevenlabs", purpose="transcribe")
    job = wait_for(job_id)

    check("job finished", job["status"], "done")
    check("job has no error", job["error"], None)
    check_true("ElevenLabs timer recorded", job["elevenlabs_seconds"])
    check("local timer empty", job["local_seconds"], None)

    files = job_files(job_id)
    check_true("transcript.json written", "transcript.json" in files)
    check_true("segments.json written", "segments.json" in files)
    check("transcribe-only writes no clips", "segments" in files, False)
    check("transcribe-only writes no manifest", "manifest.jsonl" in files, False)

    after_transcribe = credits_now()
    check_true("credits were spent", after_transcribe > before,
               f"{before} -> {after_transcribe}")

    # --- promote: must produce clips without spending anything
    print("  -- promoting to training data", flush=True)
    check_true("job is promotable", job["can_promote"])
    promoted = post(f"/jobs/{job_id}/promote")
    check("promote succeeded", promoted.status_code, 200)
    check("purpose is now training", promoted.json()["purpose"], "training")
    check_true("clips counted", promoted.json()["segment_count"])

    files = job_files(job_id)
    check_true("clips written by promote", "segments" in files)
    check_true("manifest written by promote", "manifest.jsonl" in files)

    in_training = [j["job_id"] for j in get("/jobs?purpose=training").json()]
    check_true("moved to the Training list", job_id in in_training)
    check("promoting twice is refused", post(f"/jobs/{job_id}/promote").status_code, 409)

    after_promote = credits_now()
    check("promote spent NO credits", after_promote, after_transcribe)


def test_export_filters():
    print("\n=== dataset export takes training jobs only", flush=True)
    sys.path.insert(0, "/app")
    import db
    finished = [j for j in db.list_jobs() if j["status"] == "done"]
    training = [j for j in finished if j["purpose"] == "training"]
    transcribe = [j for j in finished if j["purpose"] != "training"]
    print(f"         {len(training)} training, {len(transcribe)} transcribe-only", flush=True)

    result = os.popen("cd /app && python export_dataset.py --dry-run 2>&1").read()
    if transcribe:
        check_true("skipped transcribe-only jobs are reported",
                   "skipping" in result and "transcribe-only" in result)
        for job in transcribe:
            check(f"transcribe-only {job['id'][:18]} excluded",
                  f"{job['id']}  " in result.replace("skipping", ""), False)
    if training:
        check_true("a training job is included", any(j["id"] in result for j in training))


def test_english_map():
    """Approving a spelling must persist, and only approved spellings may count.

    Works on a copy: the real table feeds scoring and the exported training data,
    so a test word must never be left behind in it.
    """
    print("\n=== the English spelling table", flush=True)
    path = os.path.join(DATA, "english_map.json")
    backup = path + ".suite-backup"
    had_table = os.path.isfile(path)
    if had_table:
        shutil.copy2(path, backup)

    # A word no lecture will ever contain, so restoring the table is the only
    # cleanup needed and nothing real is touched meanwhile.
    word = "zqxtestword"
    spelling = "പരീക്ഷണം"
    try:
        before = get("/english-map")
        check("the table loads", before.status_code, 200)
        approved_before = before.json()["coverage"]["words_approved"]

        # A spelling in Latin letters defeats the purpose and must be refused.
        bad = httpx.put(f"{API}/english-map/{word}", timeout=30,
                        json={"spelling": "test", "approved": True})
        check("a Latin spelling is refused", bad.status_code, 400)

        # Saved but not approved: must NOT count towards coverage.
        proposed = httpx.put(f"{API}/english-map/{word}", timeout=30,
                             json={"spelling": spelling, "approved": False})
        check("an unapproved spelling saves", proposed.status_code, 200)
        check("an unapproved spelling is 'proposed'",
              proposed.json()["status"], "proposed")
        check("an unapproved spelling does not count as approved",
              proposed.json()["coverage"]["words_approved"], approved_before)

        sys.path.insert(0, "/app")
        from normalise import load_english_map
        check("an unapproved spelling is not offered for scoring",
              word in load_english_map(path), False)

        # Approved: must count, and must survive a fresh read of the file.
        done = httpx.put(f"{API}/english-map/{word}", timeout=30,
                         json={"spelling": spelling, "approved": True})
        check("approving a spelling succeeds", done.status_code, 200)
        check("the approved spelling is marked checked", done.json()["status"], "checked")
        check("approving raises the approved count",
              done.json()["coverage"]["words_approved"], approved_before + 1)
        check("the approved spelling is offered for scoring",
              load_english_map(path).get(word), spelling)

        reread = get("/english-map").json()
        entry = next((e for e in reread["entries"] if e["word"] == word), None)
        check_true("the approval is on disk, not only in the reply", entry is not None)
        if entry:
            check("the stored spelling is the one sent", entry["spelling"], spelling)
            check("the stored status is checked", entry["status"], "checked")
    finally:
        # Restore the real table exactly as it was.
        if had_table:
            shutil.move(backup, path)
        elif os.path.isfile(path):
            os.remove(path)
        left = get("/english-map").json()["entries"]
        check("the test word is gone again",
              any(e["word"] == word for e in left), False)


def test_batch_and_queue():
    """Submitting many lectures at once, and running them one at a time.

    The queue is what makes a twenty-lecture collection run possible. Before it,
    every submission started its own download, conversion and API call straight
    away, so one paste of twenty links would have put twenty of each inside a
    container limited to 2 GB.
    """
    print("\n=== batch submission and the job queue", flush=True)

    bad = post("/jobs/batch", json={"urls": ""})
    check("an empty paste is refused", bad.status_code, 400)

    bad = post("/jobs/batch", json={"urls": "https://example.com/not-youtube"})
    check("a non-YouTube link is refused", bad.status_code, 400)

    # Partial success would be worse than none: working out which three of twenty
    # links were silently dropped is harder than pasting the list again.
    mixed = post("/jobs/batch", json={
        "urls": "https://youtu.be/ewICskAJzHY\nhttps://example.com/nope"})
    check("one bad link rejects the whole paste", mixed.status_code, 400)
    check_true("the reply names a rejected link",
               "neither a YouTube video nor a playlist" in mixed.text)

    too_many = post("/jobs/batch", json={
        "urls": "\n".join(f"https://youtu.be/vid{i:08d}" for i in range(60))})
    check("more than 50 links is refused", too_many.status_code, 400)

    # Well-formed ids that do not exist: they fail at the download step within
    # seconds, so the queue can be watched draining without spending credits.
    ids = ["zzzSUITEz01", "zzzSUITEz02", "zzzSUITEz03"]
    urls = "\n".join(f"https://youtu.be/{i}" for i in ids)
    reply = post("/jobs/batch", json={
        "urls": urls + "\n" + f"https://youtu.be/{ids[0]}",
        "engine": "local", "purpose": "transcribe"})
    check("a valid batch is accepted", reply.status_code, 200)
    body = reply.json()
    _created_jobs.extend(body["job_ids"])
    check("three distinct lectures queued", body["queued"], 3)
    check("the repeated link was dropped", body["duplicates_ignored"], 1)

    # Only one job may be in flight at any moment. uvicorn runs a single process
    # with a single worker thread, so this is structural rather than a race that
    # merely has not been lost yet.
    most_at_once = 0
    mine = {}
    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(2)
        mine = {j["job_id"]: j["status"] for j in get("/jobs").json()
                if j["job_id"] in body["job_ids"]}
        running = [s for s in mine.values() if s not in ("queued", "done", "failed")]
        most_at_once = max(most_at_once, len(running))
        if all(s in ("done", "failed") for s in mine.values()):
            break
    check_true("every queued lecture finished",
               bool(mine) and all(s in ("done", "failed") for s in mine.values()))
    check_true(f"never more than one ran at a time (saw {most_at_once})",
               most_at_once <= 1)


def test_playlists_and_duplicates():
    """Playlist expansion, and never paying to transcribe the same lecture twice.

    A course playlist is how twenty lectures actually arrive, and re-pasting one
    after it gains a video must not re-transcribe the rest - at roughly 70 credits
    a minute, that mistake is expensive rather than merely annoying.
    """
    print("\n=== playlists and duplicate lectures", flush=True)

    sys.path.insert(0, "/app")
    from downloader import is_playlist_url, is_youtube_url, youtube_video_id

    check("a playlist link is recognised",
          is_playlist_url("https://www.youtube.com/playlist?list=PLabc123"), True)
    # This one names a single video and happens to sit in a list. Treating it as a
    # playlist would queue the whole course when one lecture was asked for.
    check("watch?v=...&list=... is a video, not a playlist",
          is_playlist_url("https://www.youtube.com/watch?v=ewlCskAJzHY&list=PLabc"), False)
    check("...and is still accepted as a video",
          is_youtube_url("https://www.youtube.com/watch?v=ewlCskAJzHY&list=PLabc"), True)
    check("a playlist link is not a video link",
          is_youtube_url("https://www.youtube.com/playlist?list=PLabc123"), False)

    # The same lecture written three ways must give one id, or duplicate detection
    # silently fails and the lecture is transcribed again.
    ids = {youtube_video_id(u) for u in (
        "https://youtu.be/ewlCskAJzHY",
        "https://www.youtube.com/watch?v=ewlCskAJzHY",
        "https://www.youtube.com/watch?v=ewlCskAJzHY&list=PLabc&index=3&pp=xyz")}
    check("three spellings of one lecture give one id", ids, {"ewlCskAJzHY"})

    bad = post("/jobs/batch", json={"urls": "https://example.com/x"})
    check("neither video nor playlist is refused", bad.status_code, 400)

    missing = post("/jobs/batch", json={
        "urls": "https://www.youtube.com/playlist?list=PLdoesNotExistAtAll12345"})
    check("an unreadable playlist gives a readable error", missing.status_code, 400)
    check_true("...and not a bare server error",
               "could not be read" in missing.text)

    # Anything already collected must be skipped rather than queued again.
    existing = [j for j in get("/jobs").json() if j.get("source_url")]
    if existing:
        url = existing[0]["source_url"]
        vid = youtube_video_id(url)
        again = post("/jobs/batch", json={"urls": url})
        check("a lecture already collected is not queued again", again.status_code, 400)
        check_true("...and says so plainly", "already here" in again.text)
        if vid:
            other = post("/jobs/batch", json={
                "urls": f"https://www.youtube.com/watch?v={vid}&list=PLzzz&index=9"})
            check("...even written as a different kind of link", other.status_code, 400)
    else:
        print("  SKIP  no collected lecture to compare against", flush=True)


def main():
    parser = argparse.ArgumentParser(description="End-to-end checks for the backend.")
    parser.add_argument("--include-paid", action="store_true",
                        help="also run the ElevenLabs checks (~21 credits)")
    args = parser.parse_args()

    started = time.time()
    print("=" * 70)
    print("API SUITE" + ("  (including paid checks)" if args.include_paid else "  (free checks only)"))
    print("=" * 70)

    try:
        test_basics()
        test_rejections()
        test_local_job()
        if args.include_paid:
            test_elevenlabs_job_and_promote()
        else:
            print("\n=== ElevenLabs checks SKIPPED (pass --include-paid to run them)", flush=True)
        test_export_filters()
        test_english_map()
        test_batch_and_queue()
        test_playlists_and_duplicates()
    finally:
        cleanup()

    failed = [r for r in _results if not r[0]]
    print("\n" + "=" * 70)
    print(f"{len(_results) - len(failed)} passed, {len(failed)} failed, "
          f"in {time.time() - started:.0f}s")
    if failed:
        print("\nFAILURES:")
        for _, name, detail in failed:
            print(f"  {name}: {detail}")
    print("=" * 70)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
