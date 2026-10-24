# Lecture Audio Transcription Tool (Part 1)

Part 1 of the B.Tech capstone **"A Hybrid Edge-Cloud Platform for Automated Lecture
Digitization and Adaptive Learning Analytics"**.

Paste a YouTube lecture link (or upload an audio/video file). The tool extracts the
audio, transcribes it with **ElevenLabs Scribe v2**, and shows the transcript on the
website. Every job also produces **training-ready data**: the lecture is cut into
short 5–20 second WAV clips with matching text, listed in a NeMo manifest.

**How it fits the larger project:** Part 1 collects audio + transcript pairs from
real Malayalam-English (Manglish) lectures. In Part 2 these folders are copied to a
GPU server and used to fine-tune **AI4Bharat IndicConformer** (an NVIDIA NeMo speech
recognition model) for code-switched lecture speech.

---

## Architecture

```
Browser ──HTTPS──> Caddy (basic auth, HTTPS, serves the React site)
                     │  /api/*
                     ▼
                  FastAPI backend ──> yt-dlp (YouTube audio) ──> FFmpeg (16 kHz mono WAV)
                     │                                              │
                     │  ElevenLabs Scribe v2 <──────────────────────┤
                     │  AI4Bharat IndicConformer (Local ASR) <──────┘
                     ▼
                  data/<job_id>/  (audio, transcript, clips, manifest) + data/jobs.db (SQLite)
```

Job stages: `queued → downloading → transcribing → segmenting → done` (or `failed`
with an error message). Work runs asynchronously in the background.

### Key Capabilities & New Features

1. **Interactive Full Lecture Audio Player & Word-Level Karaoke Sync**:
   - Stream audio directly inside the browser with HTTP byte-range support (`/api/jobs/{job_id}/stream`).
   - Authoritative WAV header duration measurement on the server guarantees exact audio runtime (eliminates partial HTTP stream length truncation).
   - High-performance 60 FPS animation loop with `requestAnimationFrame` and custom DOM event dispatching (`forge:audiotick`, `forge:audioseek`, `forge:audiostate`), achieving **0 React re-renders** during playback.
   - **Clickable Word Seeking**: Click any word in the transcript to instantly jump playback to its exact timestamp.
   - **Zero-Jitter Highlight Engine**: Active words are highlighted in warm amber without font bolding or padding changes, ensuring text lines never reflow or jump when words are highlighted.
   - **Playback Speed Selector**: Instant speed controls for `0.5×`, `1×`, `1.25×`, `1.5×`, and `2×`.
   - **Interactive Waveform Audio Display**: 300-point normalized amplitude soundwave envelope with sub-millisecond server caching (`/api/jobs/{job_id}/waveform`). Renders a Retina-sharp Canvas track with live gradient progress, glowing cursor, dynamic hover timestamp tooltip, and click/touch scrubbing.
   - **Precision Seeking**: `−10s` rewind and `+10s` fast-forward buttons.
   - **Reading-Band Auto-Scroll**: Smoothly tracks active speech within a comfortable vertical reading band (18%–65% of viewport height).

2. **Acoustic Quality & Audio Health Inspector**:
   - Real-time diagnostic evaluation of acoustic fitness for AI4Bharat IndicConformer speech fine-tuning (`/api/jobs/{job_id}/health`).
   - Measures:
     - **Signal-to-Noise Ratio (SNR dB)** (background room noise vs speech clarity).
     - **Speech Loudness (RMS dBFS)** (microphone gain calibration).
     - **Digital Ceiling & Peak Clipping** (clipped samples count and percentage).
     - **Speech Activity Ratio** (active speech percentage vs pauses).
   - Generates actionable acoustic recommendations and fine-tuning readiness scores (Optimal, Good, Fair, Poor).

3. **Human Verification & Clip Editor**:
   - Inline segment audio player to listen to individual 5–20s speech slices.
   - In-place Malayalam text editor to correct automated transcription errors.
   - One-click synchronization: human edits save directly back to `data/<job_id>/segments.json` and update the NVIDIA NeMo training `manifest.json`.
   - Certified "Human Verified" badge to distinguish gold-standard ground truth from automated ASR.
   - Instant Revert option to reset back to original automated transcripts if desired.

4. **Permanent Lecture Deletion & Usage Counter Deduction**:
   - Complete data cleanup (`DELETE /api/jobs/{job_id}`): removes audio WAV, transcripts, clip segments, and SQLite records from the server.
   - Automatically deducts the lecture's duration from the server's local model time tracking counter (`tool_usage.json`), leaving zero residual footprint.
   - Interactive modal confirmation prevents accidental deletions.

5. **Local ASR Mathematical Terms & Grammar Sanitizer**:
   - Post-processing pipeline (`sanitize_math_transcription`) cleans IndicConformer transcripts. Spoken English mathematical terms ("x", "y", "plus", "minus", "integral", "matrix") are accurately transcribed into standard academic script without ungrammatical mangled characters.

6. **PWA-Grade Mobile & Tablet Responsive Experience**:
   - Balanced 14px gutters framing mobile displays naturally without touching curved bezels.
   - Isolated GPU hardware-composited navigation dock (`translate3d(0,0,0)`) prevents inertial scroll jumping.
   - Responsive multi-column to single-column degradation with zero horizontal clipping.

### Choosing how a lecture is transcribed

Two questions on the submit form:

**Transcribe with**

| Choice | Accuracy | Cost |
|---|---|---|
| **ElevenLabs** (default) | best | ~70 credits per minute of audio |
| **Local model** | free local ASR | **free** — runs on this server, no credits |
| **Both** | both, shown side by side | ~70 credits per minute |

The local model is AI4Bharat IndicConformer running in the `asr` container on this
server's processor. Measured on a real 41-minute lecture: **302 seconds, about 8× real
time**, so roughly **7 minutes for a 1-hour lecture**.

**Use this for** (not asked when the local model is chosen, because it cannot produce
training data)

| Choice | Produces | In exported datasets |
|---|---|---|
| **Model training** | transcript, audio clips, NeMo manifest | yes |
| **Just the transcript** | transcript only | **never** |

### Two job lists

The site keeps **Training data** and **Transcripts** separate. `export_dataset.py` only
ever includes jobs marked as training data, so a lecture transcribed just to read it can
never end up in the training set by accident.

A transcribe-only job can be promoted afterwards with the **Use for training** button.
This costs **no ElevenLabs credits**: the word timings needed to cut the clips are
already saved in `transcript.json`, so segmentation just re-runs. It takes a few seconds.
Local-model jobs cannot be promoted — they have no word timings.

> **Reading the comparison scores.** The local model writes English words in Malayalam
> letters (`portion` → പോർഷൻ) while ElevenLabs keeps them in English, so about 40% of
> words differ on spelling alone. The comparison therefore transliterates ElevenLabs'
> English first, using `data/english_map.json`. Until that table is filled in
> (`build_english_map.py --fill`), the page says the scores are not meaningful yet —
> they would be measuring alphabets rather than recognition.

A card at the top shows the **ElevenLabs credits** left on the account and when they
reset, plus how much audio this tool has transcribed across both cloud and local engines.

### ElevenLabs API key permissions
| Permission | Needed for |
|---|---|
| **Speech to Text** | Transcribing (required) |
| **User: Read** | Showing credits used / left on the website (optional; without it the card explains what is missing) |

Everything else can stay "No access". ElevenLabs does not let a key read its own
per-key credit limit, so the card shows the whole account's plan credits; a per-key
limit is visible only in the ElevenLabs dashboard. The backend asks ElevenLabs for
the numbers at most once a minute.

| Folder / file | What it is |
|---|---|
| `backend/` | FastAPI app: `main.py` (API + pipeline), `downloader.py` (yt-dlp + FFmpeg), `transcriber.py` (ElevenLabs), `local_transcriber.py` (IndicConformer + math sanitizer), `segmenter.py` (clips + manifest), `audio_health.py` (acoustic diagnostics), `db.py` (SQLite) |
| `asr/` | Local speech recognition service: AI4Bharat IndicConformer behind a small HTTP API, called only by the backend |
| `frontend/` | React (Vite) single-page site. Its Dockerfile builds the site and puts it inside the Caddy image |
| `caddy/Caddyfile` | Web server config: HTTPS, basic auth, upload limit, `/api` proxy |
| `docker-compose.yml` | Runs `backend` and `caddy` |
| `deploy.ps1` | Copies the project from the Windows PC to the VPS and rebuilds it there |
| `data/` | All job outputs (not in the project files; created on the VPS) |

### API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/jobs` | `{"url": "...", "keyterms": ["..."]}`: new YouTube job → `{"job_id"}` |
| POST | `/api/jobs/upload` | multipart `file` + optional `keyterms` (comma-separated) → `{"job_id"}` |
| GET | `/api/jobs` | Past jobs, newest first. `?purpose=training` or `?purpose=transcribe` for one of the two lists |
| GET | `/api/jobs/{job_id}` | Status, source, engine, purpose, title, duration, error |
| DELETE | `/api/jobs/{job_id}` | Permanently delete all audio, clips, transcripts, and records; deducts time from local usage counter |
| GET | `/api/jobs/{job_id}/stream` | Stream audio WAV with HTTP 206 Partial Content support for in-browser playback |
| GET | `/api/jobs/{job_id}/audio` | Download the full uncompressed `audio.wav` (16 kHz mono), named after lecture title |
| GET | `/api/jobs/{job_id}/transcript` | Full text + word-level timestamps + segments (start, end, text) |
| POST | `/api/jobs/{job_id}/transcript` | Save human verified segment text corrections & update NeMo manifest |
| POST | `/api/jobs/{job_id}/transcript/revert` | Revert human edits back to original automated ElevenLabs transcript |
| GET | `/api/jobs/{job_id}/health` | Acoustic quality diagnostics (SNR dB, RMS volume, clipping samples, speech ratio, recommendations) |
| POST | `/api/jobs/{job_id}/health/reanalyze` | Recalculate acoustic SNR and clipping characteristics on `audio.wav` |
| GET | `/api/jobs/{job_id}/comparison` | ElevenLabs vs local model, segment by segment, with WER/CER |
| GET | `/api/jobs/{job_id}/snippet` | Sub-second audio snippet player for comparison discrepancy audit |
| POST | `/api/jobs/{job_id}/promote` | Turn a transcribe-only job into training data. Spends no credits |
| GET | `/api/usage` | ElevenLabs cloud credits & local model audio processed telemetry |
| GET | `/api/health` | Service health check |

---

## Requirements

- A Linux VPS with **Docker** and **Docker Compose** (v2), ports 80 and 443 free
- A domain name pointing to the VPS (here: `forge.rejected.cc`)
- An ElevenLabs account and API key
- On the Windows PC (for deploying): OpenSSH client (built into Windows) and an SSH
  host alias `lecture-vps` in `%USERPROFILE%\.ssh\config` that logs in with a key
  (see "SSH key login" below)

## Environment variables

**One file: `.env` in the project root**, created on the VPS only. docker-compose reads
it automatically. Template: `.env.example`.

| Variable | Used by | Purpose | Example |
|---|---|---|---|
| `DOMAIN` | Caddy | Public domain to get a certificate for | `forge.rejected.cc` |
| `BASIC_AUTH_USER` | Caddy | Website login name | `admin` |
| `BASIC_AUTH_HASH` | Caddy | bcrypt hash of the website password, **in single quotes** | `'$2a$12$...'` |
| `CADDY_CONTAINER_NAME` | Caddy | Container name the proxy forwards to. **Must match the Proxy Host in Nginx Proxy Manager.** live `forge-caddy`, test `lecscript-caddy` |
| `CADDY_HOST_PORT` | Caddy | Localhost-only port for curling this stack directly. live `8083`, test `8080` |
| `BACKEND_HOST_PORT` | backend | Localhost-only port for the API. live `8000`, test `8001` |
| `ELEVENLABS_API_KEY` | backend | ElevenLabs API key | `sk_...` |
| `YTDLP_COOKIES` | backend | Optional cookies file for yt-dlp (path inside the container) | `/data/youtube-cookies.txt` |
| `HF_TOKEN` | GPU scripts | Hugging Face token; the model checkpoint is gated | `hf_...` |

Although there is one file, **each container is given only its own variables**: the
backend never receives the website password or the Hugging Face token, and Caddy never
receives the ElevenLabs key. That way a problem in one container cannot expose the
other's secrets. `docker-compose.yml` is where that split is defined.

The file is never copied into a Docker image (`.dockerignore`), never sent by
`deploy.ps1`, and should be `chmod 600`.

> Note: `docker compose config` prints every value in full. Do not paste its raw output
> into a chat, an issue or a report.

---

## Local test on the Windows PC (Docker Desktop)

Before deploying, the same Docker setup can run on the PC at **http://localhost**
(plain HTTP, no certificate):

1. Start Docker Desktop.
2. Create `.env` from `.env.example`. Set `DOMAIN=:80` (meaning "any address, plain HTTP
   on port 80"), paste your ElevenLabs key, and paste a hash from
   `docker run --rm caddy:2 caddy hash-password --plaintext 'local-password'` in single quotes.
3. In PowerShell, in the project folder: `docker compose up -d --build`
4. Open http://localhost and log in. Stop with `docker compose down`.

The backend runs as user 1000 inside the container. On Docker Desktop the bind mount is
handled by the VM, so `data\` works without any ownership changes; on Linux the folder
must be owned by uid 1000 (see the deployment steps).

Job data goes to the PC's `data\` folder. `deploy.ps1` never copies it or the `.env` files.

---

## Collecting many lectures

Lectures are **queued and transcribed one at a time**. Submitting twenty at once does not
start twenty downloads — they wait their turn, so a long collection run cannot exhaust the
backend container's memory. A lecture still waiting when the server restarts stays queued
and is picked up again; only a lecture that was mid-transcription is marked failed.

Use the **Many links** tab to paste a list of YouTube links, one per line, up to 50:

- **Paste a playlist link and every video in it is added.** Mix playlists and
  individual links freely
- The engine and purpose chosen below the box apply to **every** link in the list
- Repeated links are ignored, including the same lecture written different ways
  (`youtu.be/ID`, `watch?v=ID`, `watch?v=ID&list=…`)
- **Lectures already collected are skipped**, so re-pasting a playlist after it
  gains a video costs nothing
- If any line is neither a video nor a playlist link, the whole paste is refused
  rather than partly accepted

### The one choice that matters for Part 2

Set **Transcribe with** to `ElevenLabs` or `Both`, and **Use this for** to
`Model training`.

A lecture transcribed with `Local model` can *never* become training data: the local
model returns plain text with no word timings, so there is no way to cut it into aligned
clips. `export_dataset.py` only ever exports jobs whose purpose is `training`. Collecting
hours of audio with the wrong engine selected is the most expensive mistake available
here, because the only fix is transcribing it all again.

A transcribe-only job made with ElevenLabs or Both **can** be promoted later for free —
the word timings are already saved.

---

## Working on this with other people

The source code lives in a **private** GitHub repository. It is a backup and a history
only — the server is still updated by `deploy.ps1`, never by pulling from GitHub.

### Getting set up (new contributor)

```powershell
git clone https://github.com/<owner>/<repo>.git
cd <repo>
```

The repository deliberately contains **no secrets**, so a fresh clone cannot run yet.
Copy `.env.example` to `.env` and fill in the real values, which the project owner
passes to you **outside** the repository — never in a commit, an issue or a pull
request. `.gitignore` already excludes `.env`, so there is no way to commit it by
accident.

### Making a change

```powershell
git checkout -b short-description-of-change   # e.g. fix-upload-size-limit
# ... edit, then test on the VPS ...
git add -A
git commit -m "What changed and why"
git push -u origin short-description-of-change
```

Then open a pull request on GitHub so someone else reads it before it joins `main`.
Run `bash tests/run_all.sh` on the VPS first — a pull request that fails the suite
wastes the reviewer's time.

### The one rule that matters

**Pull before you deploy.**

```powershell
git pull --rebase
.\deploy.ps1
```

`deploy.ps1` copies *your* folder over the one on the server. If someone else has
pushed work you have not pulled, deploying would put an older copy on the server and
undo their change, with nothing on screen to say so. The script now refuses to run
when GitHub is ahead of your PC and tells you to pull; `-SkipGitCheck` overrides it,
and should be needed only when you are offline.

Deploying your own *uncommitted* work is fine and is not blocked — that is the normal
way to test something before committing it.

---

## Deployment to forge.rejected.cc

### 0. SSH key login (one time, on the Windows PC)
`deploy.ps1` runs without prompts, so the VPS must accept an SSH key instead of a password.
```powershell
# create a key just for this server (no passphrase)
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\lecture_vps
```
Add to `%USERPROFILE%\.ssh\config`:
```
Host lecture-vps
    HostName <your-vps-host>
    User squad
    Port <your-ssh-port>
    IdentityFile ~/.ssh/lecture_vps
    IdentitiesOnly yes
```
Install the key on the VPS (asks for the VPS password once):
```powershell
type $env:USERPROFILE\.ssh\lecture_vps.pub | ssh lecture-vps "umask 077; mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```
Check: `ssh lecture-vps hostname` should log in without a password.

### 1. DNS
At your DNS provider, add an **A record**: name `panel`, value = the VPS's public IPv4
address. Check from the VPS: `getent hosts forge.rejected.cc` should print that IP.
If the domain is on Cloudflare, either set the record to **DNS only** (grey cloud),
or keep it proxied with SSL mode **Full (strict)**.

### 2. Firewall (on the VPS)
Allow only SSH, HTTP and HTTPS. This VPS's SSH runs on a non-standard port, not 22: allow
that port **before** enabling ufw, or you will lock yourself out.
```bash
sudo ufw allow <your-ssh-port>/tcp   # SSH on this VPS
sudo ufw allow 22/tcp      # only if SSH also listens on 22
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp     # HTTP/3 (optional)
sudo ufw enable
sudo ufw status
```
Note: Docker publishes ports directly through iptables, so ufw does not hide them.
The backend is therefore bound to `127.0.0.1:8000` only and cannot be reached from
the internet.

### 3. Copy the project to the VPS (from the Windows PC)
Docker projects on this VPS live under `/opt/docker`. Create the project folder once
(on the VPS; `/opt/docker` belongs to root and the `dockeradmin` group):
```bash
sudo install -d -o squad -g dockeradmin -m 2770 /opt/docker/lecture-transcriber
```
Then, in PowerShell on the PC, in the project folder:
```powershell
.\deploy.ps1
```
This packs the project (without `data/` and `.env` files), copies it with `scp` to
`/opt/docker/lecture-transcriber` on the VPS, and runs `docker compose up -d --build` there.
On the very first run the build fails until the `.env` file from step 4 exists.
Create it, then run `.\deploy.ps1` again.

### 4. Create the `.env` file (on the VPS)
```bash
cd /opt/docker/lecture-transcriber

# Generate the bcrypt hash of the website password:
docker run --rm caddy:2 caddy hash-password --plaintext 'choose-a-strong-password'

cp .env.example .env
nano .env        # DOMAIN, BASIC_AUTH_USER, the hash, the ElevenLabs key, the HF token
chmod 600 .env

# The backend runs as user 1000, so it must own the data folder:
sudo chown -R 1000:1000 data
```
Keep the hash inside **single quotes**: `BASIC_AUTH_HASH='$2a$12$...'`.
Without the quotes, docker-compose treats parts of the hash as variables and the
login silently never works.

### 5. Start
```bash
docker compose up -d --build
docker compose ps
curl -s http://127.0.0.1:8000/api/health        # backend directly (VPS only)
curl -s -u admin:yourpassword https://forge.rejected.cc/api/health   # through Caddy
```
Then open https://forge.rejected.cc and log in.

### Everyday commands (on the VPS, in `/opt/docker/lecture-transcriber`)
| Task | Command |
|---|---|
| Update after code changes | `.\deploy.ps1` on the PC (or `.\deploy.ps1 -Service backend` to rebuild only the backend) |
| Rebuild on the VPS | `docker compose up -d --build` |
| Update yt-dlp (do this when YouTube downloads start failing) | `docker compose build --no-cache backend && docker compose up -d backend` |
| Backend logs | `docker compose logs -f backend` |
| Caddy logs | `docker compose logs -f caddy` |
| Status | `docker compose ps` |
| Restart | `docker compose restart` (or `docker compose restart backend`) |
| Apply a Caddyfile change | `docker compose restart caddy` |
| Stop | `docker compose down` (data and certificates are kept) |

Jobs that are running when the backend restarts are marked **failed** and must be
submitted again.

---

## Data folder format

Each job gets a folder `data/<job_id>/` (job ids look like `20261019-153012-a3f9c1`):

| File | What it is |
|---|---|
| `audio.wav` | The whole lecture: 16 kHz, mono, 16-bit PCM WAV |
| `transcript.json` | The full, unmodified response from ElevenLabs (text, language, every word with start/end time and confidence) |
| `transcript.txt` | Plain transcript text |
| `segments/<job_id>_0001.wav`, … | Short training clips (about 5–20 s), cut at the longest pauses between words, never mid-word, with up to 0.15 s of padding into the surrounding silence |
| `manifest.jsonl` | NeMo training manifest, one line per clip |
| `segments.json` | Clip start/end times and text (used by the website) |

The original download/upload is deleted after it is converted to `audio.wav`.
`data/jobs.db` is the SQLite job list.

Example `manifest.jsonl` line:
```json
{"audio_filepath": "segments/20261019-153012-a3f9c1_0001.wav", "duration": 12.48, "text": "ഇന്ന് നമ്മൾ Fourier transform നോക്കാം"}
```
`audio_filepath` is **relative to the job folder**, so a job folder can be copied
anywhere as-is. In Part 2, NeMo's training scripts read these manifests (all job
manifests can be concatenated after prefixing each path with its job folder, or NeMo
can be run from inside each folder). The text is the raw Scribe output, so
normalisation (punctuation, casing) is done in Part 2.

## Copying data to the GPU server (Part 2)

From the VPS (or any machine that can reach both):
```bash
# everything, resumable, only new/changed files on later runs
rsync -avP /opt/docker/lecture-transcriber/data/ user@gpu-server:/datasets/lectures/ --exclude jobs.db

# or a single job with scp
scp -r /opt/docker/lecture-transcriber/data/20261019-153012-a3f9c1 user@gpu-server:/datasets/lectures/
```
To bring data down to the Windows PC instead:
`scp -r lecture-vps:/opt/docker/lecture-transcriber/data/<job_id> .`

---

# Part 2 — Model training

Part 1 collects lectures. Part 2 uses them to measure and improve an open speech
recognition model, **AI4Bharat IndicConformer** (Malayalam,
`ai4bharat/indicconformer_stt_ml_hybrid_ctc_rnnt_large`):

1. **Baseline** — run the pretrained model on the same audio and see how far it is
   from the ElevenLabs transcripts.
2. **Fine-tune** — train it on the collected lectures and measure the change.
3. **Show** — put ElevenLabs, baseline and fine-tuned side by side on the website.

All model work happens on a **rented GPU machine** that is started for a run and
destroyed afterwards. The VPS has no GPU; it stores the data and hosts the website.

## What the scores mean (important)

Unless a test lecture has been corrected by a human, the "reference" is just the
ElevenLabs transcript, which has its own mistakes. A score against it therefore
measures **agreement with ElevenLabs**, not accuracy. Only lectures with a
`transcript_corrected.json` count as **human-verified**, and `dataset_info.json`
records which case applies (`human_verified_test_references`). The website labels
every score the same way.

## Why English words are written in Malayalam letters

The Malayalam IndicConformer can only produce **256 different symbols**, all
Malayalam — no `a`–`z`, no `0`–`9` (checked in the published vocabulary of the same
model). It physically cannot write "matrix", and about **40% of the words in these
lectures are English**.

Rebuilding the model's output to add English letters needs far more than the few
hours of audio available here, and it would also ruin the comparison: the
pretrained model would score 0% on every English word purely because the letters
are missing from its alphabet, so "fine-tuning improved the score" would mostly
mean "the model can now form Latin letters".

So English words are written in Malayalam letters in the training text, using a
**fixed table** (`data/english_map.json`) with one spelling per word. A fixed table
is used rather than converting on the fly, because the same word spelled two ways
teaches the model contradictions. The table can also be used backwards, to show
`matrix` again on screen.

## Scripts (run inside the backend container)

| Command | What it does |
|---|---|
| `docker compose exec backend python analyze_script.py` | Reports how much of the speech is Malayalam vs English, and the most common English words |
| `docker compose exec backend python build_english_map.py --collect` | Lists every English word used and adds blank entries to `data/english_map.json` |
| `docker compose exec backend python build_english_map.py --report` | Shows how much of the English speech has a spelling yet |
| `docker compose exec backend python export_dataset.py` | Builds a training dataset in `data/datasets/<date>/` |
| `docker compose exec backend python -m unittest test_normalise -v` | Runs the text clean-up tests |

Useful export options: `--dry-run` (report only), `--name NAME`, `--pin-test JOB_ID`
(force a lecture into the test split), `--exclude JOB_ID`, `--train/--val/--test`
(split shares, must add up to 1.0), `--copy` (copy clips instead of hard-linking).

## Checking nothing is broken

Run this on the VPS, from `/opt/docker/lecture-transcriber`, after any change:

```bash
bash tests/run_all.sh
```

It exits non-zero if anything fails, and covers four things in order:

| Part | What it checks |
|---|---|
| sync check | the code inside the running containers matches the files on disk — a change that was never actually deployed is the commonest false result |
| unit tests | text clean-up, alignment and chunk planning |
| `tests/api_suite.py` | every endpoint end to end: a local-only job, promotion, exports, rejections. Creates jobs and deletes them again afterwards |
| `tests/run_ui_check.sh` | loads the real site in a headless browser at phone and desktop width, in light and dark, and fails on any console error, blocked request or sideways scrolling. Screenshots land in `tests/screenshots/` |

The API suite skips the ElevenLabs calls by default, because they cost credits. To
include them (about 21 credits):

```bash
bash tests/run_all.sh --include-paid
```

A check that cannot run prints `SKIPPED` rather than passing quietly — for example the
two-transcript check needs a finished job that used both engines.

### Filling in the English spellings

`--collect` only lists the words; proposing Malayalam spellings needs AI4Bharat's
transliteration library, which is too large to add to the backend image. Run it
once in a throwaway container on the VPS:

```bash
cd /opt/docker/lecture-transcriber
docker run --rm -v "$PWD/data:/data" -v "$PWD/backend:/app" -w /app python:3.11-slim \
  sh -c "pip install -q ai4bharat-transliteration && DATA_DIR=/data python build_english_map.py --fill"
```

There is also `--derive`, which needs no extra library: it lines up the two
transcripts of any lecture run with `Both`, and where ElevenLabs wrote an English
word and the local model wrote Malayalam at the same place, it records that pair as
a proposal, keeping every spelling it saw with a count.

```bash
docker compose exec backend python build_english_map.py --derive
```

Then review the proposals on the website, in the **English spellings** panel at the
bottom of the page: words are listed commonest first, with how often each was said,
the proposed spelling, and the other spellings the model produced for the same word
(click one to use it). Press **Approve** to accept a spelling.

**Only approved spellings are used.** The proposals come from the very model being
measured, so scoring against them would let it mark its own work, and training on
them would teach it its own mistakes. `load_english_map()` ignores anything not
approved, and so do the comparison scores and `export_dataset.py`. The coverage line
reports the share of English **actually spoken** that is covered, not the share of
words in the table — in one lecture the fifty commonest words accounted for 64% of
all English spoken, so approving the common ones first is worth far more.

The file `data/english_map.json` can still be edited by hand if preferred; `checked`
means approved. Re-run `export_dataset.py` afterwards so the dataset uses the table.

## Dataset folder

`export_dataset.py` writes a self-contained folder that can be copied to the GPU
machine as it is:

```
data/datasets/<name>/
  train.jsonl  val.jsonl  test.jsonl   NeMo manifests (audio_filepath, duration, text)
  audio/<clip>.wav                     the clips (hard-linked, so they cost no extra disk)
  english_map.json                     the spelling table used, for reproducibility
  dataset_info.json                    lectures per split, hours, clips, what was dropped
```

Lectures are split **whole**, never clip by clip: if clips from one lecture were in
both training and testing, the model would be tested on a voice and a topic it had
already studied. That means **at least 3 lectures are needed** before a dataset is
usable; `dataset_info.json` records `usable_for_training` and the export prints a
warning when it is false. Clips shorter than 1 s or longer than 25 s, and clips with
no text, are dropped and counted in `dataset_info.json`.

The same text clean-up (`backend/normalise.py`) is applied to the reference text here
and to the model's output at scoring time, so the comparison is fair. It fixes the two
ways Malayalam chillu letters can be written, which look identical on screen but are
different characters and would otherwise be counted as mistakes.

## The GPU side (`gpu/`)

All model work happens inside one image built from `gpu/Dockerfile`. It is based on
`nvcr.io/nvidia/pytorch:24.01-py3` — the image AI4Bharat's own fork is built
against — plus the fork itself (branch `nemo-v2`). The **official `nemo_toolkit`
cannot load the IndicConformer checkpoint**, so the fork is required.

Only NeMo's speech-recognition parts are installed. The fork's own Dockerfile also
compiles Megatron-LM, Apex and Transformer Engine, which serve large language
models, take hours to compile, and IndicConformer does not use.

### Building the image

**A GPU is not needed to build it.** A CUDA image is just files; installing PyTorch
copies CUDA libraries to disk without ever running them. Build on the VPS, from the
**project root** (the image also needs `backend/normalise.py`):

```bash
cd /opt/docker/lecture-transcriber
docker build -f gpu/Dockerfile -t lecture-indicconformer:v1 .
```

The finished image is about **11 GB**, but the build needs roughly **35 GB free**,
because the base image is unpacked into the build cache alongside it. On this 58 GB
VPS that is tight: check `df -h /` first, and afterwards run
`docker builder prune -af`, which reclaims the build cache (tens of GB) without
touching the finished image.

If the build fails with `no space left on device`, prune and try again. Running out
of disk here once filled the drive completely; Part 1 survived, but it is worth
avoiding. Building on the GPU pod, which usually has far more disk, avoids the
problem entirely.

### Scripts inside the image (`/app`)

| Script | What it does | Needs a GPU? |
|---|---|---|
| `pull_data.sh` | Copies a dataset from the VPS with rsync over SSH | No |
| `baseline.py` | Scores the **pretrained** model on val + test, with both decoders | No, just slow |
| `finetune.py` | Fine-tunes from the Malayalam checkpoint; keeps the best version | **Yes** |
| `evaluate.py` | Scores the **fine-tuned** model, in the same file format as the baseline | No, just slow |
| `push_results.sh` | Sends predictions, metrics, logs and the best checkpoint back | No |
| `run_all.sh` | All of the above in order, stopping at the first failure | **Yes** |

### Environment variables

| Variable | Purpose | Example |
|---|---|---|
| `VPS_HOST` / `VPS_USER` / `VPS_PORT` | Where to fetch the dataset and send results | your VPS host / `gpupull` / your SSH port |
| `DATASET_ID` | Dataset folder name on the VPS | `2026-11-02_1130` |
| `RUN_ID` | Name for this run (default: date + dataset) | `2026-11-02_run1` |
| `HF_TOKEN` | Hugging Face token; **the checkpoint is gated** | `hf_...` |
| `SSH_KEY` | Key mounted into the pod, never built into the image | `/workspace/ssh/id_gpu` |
| `BATCH_SIZE` / `EPOCHS` | Overrides; batch size otherwise fits itself to the GPU | `8` / `20` |
| `KEEP_CHECKPOINT` | `0` to skip sending the ~500 MB model back | `1` |

Secrets are only ever passed as environment variables or mounted files. **No token
or key is written into the image, the logs or the results.**

`HF_TOKEN` lives in the same single `.env` as everything else. Pass just that one
variable through, rather than the whole file, so the GPU container never receives the
website password:

```bash
cd /opt/docker/lecture-transcriber
set -a; . ./.env; set +a          # load the settings into this shell
docker run --rm -e HF_TOKEN  ...  # -e NAME with no value passes it through
```

### A restricted SSH user on the VPS for the pod

The pod needs to reach the VPS, but it is a rented machine you do not control, so
give it its own key and its own account rather than yours. On the VPS:

```bash
sudo adduser --disabled-password --gecos "" gpupull
sudo usermod -aG dockeradmin gpupull                 # read the data folder
sudo -u gpupull mkdir -p /home/gpupull/.ssh
ssh-keygen -t ed25519 -f ~/gpu_key -N ""             # the key pair for the pod
sudo tee /home/gpupull/.ssh/authorized_keys < ~/gpu_key.pub
sudo chown -R gpupull:gpupull /home/gpupull/.ssh
sudo chmod 700 /home/gpupull/.ssh && sudo chmod 600 /home/gpupull/.ssh/authorized_keys
```

Copy the **private** key (`~/gpu_key`) to the pod and mount it at
`/workspace/ssh/id_gpu`. **When the run is finished, remove the access:**

```bash
sudo rm /home/gpupull/.ssh/authorized_keys     # revoke immediately
sudo deluser --remove-home gpupull             # or remove the account entirely
rm ~/gpu_key ~/gpu_key.pub
```

### Running on a rented pod

Without a container registry the pod cannot pull the image, so either:

* **build it on the pod** — rsync the `gpu/` and `backend/` folders across and run
  the same `docker build` there (about 15–25 minutes of paid GPU time), or
* **ship the built image** — `docker save lecture-indicconformer:v1 | gzip | ssh pod 'gunzip | docker load'`
  (roughly 10 GB over the network), or
* **push it to a registry** once you have an account, which makes pod startup almost instant.

Then, inside the image:

```bash
export VPS_HOST=... VPS_USER=gpupull VPS_PORT=...
export DATASET_ID=... HF_TOKEN=...
/app/run_all.sh
```

`run_all.sh` refuses to start if `dataset_info.json` says the dataset is not usable,
so a pod is never paid for to train on an invalid split. Everything it prints is
also saved to `logs/run_all.log` in the run folder. When it finishes, destroy the pod.

### What a run produces

```
data/results/<run_id>/
  baseline/predictions.json   baseline/metrics.json     pretrained model
  finetuned/predictions.json  finetuned/metrics.json    after training
  run_log.json                what was trained, on what GPU, for how long, and why
  logs/run_all.log
  best.nemo                   the fine-tuned model (~500 MB)
```

`metrics.json` holds WER and CER overall and **per lecture**, for both the CTC and
RNNT decoders. `predictions.json` holds every clip's reference and prediction, which
is what the website's comparison page reads.

> The website's comparison page is still to come; it will read these files directly.

---

## Troubleshooting

**"YouTube blocked the download (Sign in to confirm you're not a bot)"**
YouTube often blocks downloads from datacenter IPs. Options:
1. Use the **Upload file** tab instead. This always works.
2. Give yt-dlp cookies from a **throwaway** Google account (never your main
   account; it may get flagged): export `cookies.txt` from a private browser
   window logged into that account (e.g. with a "Get cookies.txt LOCALLY"
   extension), copy it to `/opt/docker/lecture-transcriber/data/youtube-cookies.txt`, set
   `YTDLP_COOKIES=/data/youtube-cookies.txt` in `.env`, `chmod 600` the cookies file
   (it is a live Google session), then
   `docker compose up -d backend`.
3. Update yt-dlp: `docker compose build --no-cache backend && docker compose up -d backend`.

**"ElevenLabs rejected the API key (401)"**: the key in `.env` is wrong or
revoked. Fix it, then `docker compose up -d backend` (a plain restart does not
re-read `env_file`; `up -d` recreates the container).

**"ElevenLabs account is out of credits"**: top up or upgrade the ElevenLabs plan;
then resubmit the job.

**Disk full**: check with `df -h` and `du -sh /opt/docker/lecture-transcriber/data/*`. Each hour
of lecture uses about 230 MB (audio.wav + the same again for clips). Copy finished
jobs to the GPU server, then delete their folders. Also `docker system prune` removes
old images.

**HTTPS certificate not issued / browser shows a certificate error**: check
`docker compose logs caddy`. Usual causes: the `panel` A record doesn't point to this
VPS yet (`getent hosts forge.rejected.cc`); ports 80/443 are blocked by a firewall
(ufw or the VPS provider's); or Cloudflare proxy is on with SSL mode "Flexible" (use
"Full (strict)" or "DNS only"). Caddy retries automatically. Keep the `caddy_data`
volume so certificates aren't requested again on every rebuild.

**Login prompt keeps coming back**: the hash in `.env` is not in single quotes, or
was generated for a different password. Regenerate it and run `docker compose up -d caddy`.

**Upload fails at 413**: the file is over 2 GB (limit set in `caddy/Caddyfile`).
