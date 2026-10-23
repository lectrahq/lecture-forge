# CLAUDE.md — rules for working on this project

## Purpose
Part 1 of the B.Tech capstone "A Hybrid Edge-Cloud Platform for Automated Lecture
Digitization and Adaptive Learning Analytics". It is a data-collection web tool: a
YouTube lecture link or an uploaded audio/video file is turned into 16 kHz mono WAV,
transcribed with ElevenLabs Scribe v2, shown on a website, and saved as
training-ready data (short WAV clips + a NeMo `manifest.jsonl`). In Part 2 these
folders are copied to a GPU server to fine-tune AI4Bharat IndicConformer for Manglish
(code-switched Malayalam-English) lecture speech recognition. Every job's output must
be training-ready, not just display-ready.

## Environment
- Code is edited on the developer's Windows PC in `F:\LectraHQ\Lecture Forge\Lecture Forge Working Dir`.
- It is copied to the VPS with `deploy.ps1` (tar + scp + ssh) and runs **only on the
  VPS, only via Docker Compose**. The VPS host, user and SSH port are deliberately not
  recorded here; they live in `~/.ssh/config` under the alias below and nowhere in
  this repository.
  SSH alias `lecture-vps` in `~/.ssh/config` (key `~/.ssh/lecture_vps`). Docker
  projects on the VPS live under `/opt/docker`; this one is in
  `/opt/docker/lecture-transcriber`.
- Public URL: https://forge.rejected.cc (Caddy, automatic HTTPS, basic auth).
- No non-Docker run instructions. Docker Desktop on the PC may be used for quick
  local checks, but "done" means tested on the VPS containers.
- The source code is backed up to a **private** GitHub repository. `.gitignore` keeps
  `.env`, `data/` and build output out of it; never commit a secret or a lecture
  recording, and never make the repository public. Deployment is still `deploy.ps1`
  over scp, not git: the VPS does not pull from GitHub.

## Fixed tech stack (do not substitute)
- Backend: Python 3.11+, FastAPI, uvicorn (no `--reload`)
- Frontend: React (Vite), plain CSS, no UI framework; calls relative `/api` paths only
- Audio: yt-dlp (audio-only, never pinned to an old version, plus Deno as its JS runtime) + FFmpeg
- Transcription: official `elevenlabs` SDK, `client.speech_to_text.convert(...)`, `model_id="scribe_v2"`
- Job tracking: SQLite via standard-library `sqlite3` (no ORM)
- Background work: FastAPI `BackgroundTasks` (no Celery/Redis)
- Config: python-dotenv; **one** `.env` in the project root holds every secret, and
  `docker-compose.yml` hands each service only the variables it needs (the backend never
  receives the website password or `HF_TOKEN`, Caddy never receives the ElevenLabs key)
- Reverse proxy: Caddy with automatic HTTPS
- One `docker-compose.yml` for everything

## Mandatory after every change
1. **PROJECT_DIARY.md** — add a new entry at the TOP (newest first). Get the date with
   the `date` command (PowerShell: `Get-Date -Format yyyy-MM-dd`), never guess it.
   Format:
   ```
   ## YYYY-MM-DD — <short title> [MAJOR | MINOR]

   **What changed:** 2–4 plain-English sentences on what was done and why.

   **Files touched:**
   - `path/to/file` — what changed in this file, in one plain sentence

   **How to check it:** one line on how to see or test the change (if applicable)
   ```
   - MAJOR = new feature, new endpoint, architecture/Docker/deployment change, new
     dependency, change in data format.
   - MINOR = bug fix, UI tweak, text/styling change, refactor with no behaviour
     change, documentation-only change.
   - Written for a non-programmer (the faculty guide): no code snippets, explain
     jargon, e.g. "FFmpeg (an audio conversion tool)".
   - Never edit or delete old entries; if something was wrong, add a new entry.
2. **README.md** — update whenever setup, commands or environment variables change.

## Security
- The ElevenLabs API key must never appear in frontend code, Docker images, logs or
  any copied/committed file. `.dockerignore` files keep `.env` out of images and
  `deploy.ps1` never copies `.env` files or `data/`.
- Never print or echo the contents of `.env` files. `docker compose config` prints them
  in full, so never paste its raw output anywhere either.
- The backend container runs as user 1000, not root: it feeds untrusted input (downloaded
  videos, uploaded files) through yt-dlp and FFmpeg. `data/` on the VPS is owned by 1000.
- All services have memory, CPU and process limits so one bad file cannot take the
  machine down. The `asr` service receives only `HF_TOKEN`, nothing else.

## Transcription engines and job purpose
- A job chooses an engine (`elevenlabs`, `local`, `both`) and a purpose
  (`training`, `transcribe`). The local model is IndicConformer in the `asr` container,
  free but less accurate; it gives no word timings, so a local-only job can never
  produce training clips and its purpose is forced to `transcribe`.
- **Only `purpose='training'` jobs are ever exported as training data.** This is the
  guard that stops a casual transcription polluting the dataset; do not weaken it.
- A transcribe-only job can be promoted later for free, because the word timings are
  already in `transcript.json`.
- Never use local-model output as training labels: a model trained on its own output
  degrades.

## Working rules
- Test changes on the running containers (curl `127.0.0.1:8000` on the VPS, and the
  public URL through Caddy) before reporting them done.
- Keep code simple and readable, commented where logic isn't obvious. This is a
  student project reviewed by faculty: no over-engineering, no user accounts.
