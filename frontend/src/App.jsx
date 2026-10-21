import { useCallback, useEffect, useRef, useState } from "react";
import {
  audioUrl,
  createYoutubeJob,
  getComparison,
  getEnglishMap,
  getJob,
  getTranscript,
  getUsage,
  listJobs,
  promoteJob,
  setEnglishSpelling,
  uploadFile,
} from "./api.js";
import { THEMES, applyTheme, loadTheme, saveTheme } from "./theme.js";

const POLL_MS = 3000;
const FINAL = ["done", "failed"];
// Which stages a job passes through depends on what was asked for.
function stagesFor(job) {
  if (!job) return ["queued", "downloading", "transcribing", "segmenting", "done"];
  if (job.engine === "local") return ["queued", "downloading", "transcribing_local", "done"];
  const stages = ["queued", "downloading", "transcribing", "segmenting"];
  if (job.engine === "both") stages.push("transcribing_local");
  stages.push("done");
  return stages;
}
const STAGE_LABELS = {
  queued: "Queued",
  downloading: "Getting audio",
  transcribing: "Transcribing",
  segmenting: "Segmenting",
  transcribing_local: "Local model",
  done: "Done",
  failed: "Failed",
};
const ENGINE_LABELS = {
  elevenlabs: "ElevenLabs",
  local: "Local model",
  both: "Both",
};
// Measured from this project's own usage: about 70 credits per minute of audio.
const CREDITS_PER_MINUTE = 70;
// Same list as the backend (backend/main.py ALLOWED_EXTENSIONS).
const ALLOWED_EXTENSIONS = [
  ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wma", ".amr",
  ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".3gp", ".mpeg", ".mpg", ".ts",
];

// 3725.4 -> "1:02:05", 65.2 -> "1:05"
function formatTime(seconds) {
  if (seconds == null) return "–";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

// 12.345 -> "0:12.3" (segment table needs sub-second precision)
function formatPrecise(seconds) {
  const tenths = Math.floor((seconds % 1) * 10);
  return `${formatTime(seconds)}.${tenths}`;
}

function parseKeyterms(text) {
  return text.split(",").map((t) => t.trim()).filter(Boolean);
}

export default function App() {
  const [jobs, setJobs] = useState([]);
  const [currentId, setCurrentId] = useState(null);
  const [job, setJob] = useState(null);
  const [transcript, setTranscript] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [usage, setUsage] = useState(null);
  const jobRef = useRef(null);   // the opened job's panel, so it can be scrolled to
  const [comparison, setComparison] = useState(null);

  // Reload the job lists and the credit usage (on start, when a job ends, and on "Refresh").
  const refreshJobs = useCallback(() => {
    listJobs().then(setJobs).catch(() => {});
    getUsage().then(setUsage).catch(() => {});
  }, []);

  useEffect(refreshJobs, [refreshJobs]);

  // Follow the selected job: poll its status every 3 s until it is done or failed.
  useEffect(() => {
    if (!currentId) return;
    let stopped = false;
    let timer = null;

    async function poll() {
      try {
        const latest = await getJob(currentId);
        if (stopped) return;
        setJob(latest);
        setLoadError("");
        if (latest.status === "done") {
          const t = await getTranscript(currentId);
          if (!stopped) setTranscript(t);
          if (latest.has_comparison) {
            try {
              const c = await getComparison(currentId);
              if (!stopped) setComparison(c);
            } catch {
              // the comparison is a bonus; never block showing the transcript
            }
          }
          refreshJobs();
        } else if (latest.status === "failed") {
          refreshJobs();
        } else {
          timer = setTimeout(poll, POLL_MS);
        }
      } catch (err) {
        if (stopped) return;
        setLoadError(err.message);
        timer = setTimeout(poll, POLL_MS); // keep trying (e.g. backend restarting)
      }
    }

    setJob(null);
    setTranscript(null);
    setComparison(null);
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [currentId, refreshJobs]);

  function openJob(id) {
    setCurrentId(id);
  }

  // Bring the opened job into view. It sits above the job list but below the
  // form, so scrolling to the top of the page (what this used to do) left the
  // job you had just clicked on still off-screen.
  useEffect(() => {
    if (!currentId || !jobRef.current) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    jobRef.current.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "start" });
  }, [currentId]);

  return (
    <div className="page">
      <header>
        <div className="header-row">
          <h1>Lecture Transcription</h1>
          <ThemeSwitch />
        </div>
        <p className="subtitle">
          YouTube lecture or audio/video file → ElevenLabs Scribe transcript →
          training-ready clips for IndicConformer.
        </p>
      </header>

      {usage && <UsageCard usage={usage} />}

      <SubmitForm
        onCreated={(id) => {
          openJob(id);
          refreshJobs();
        }}
      />

      {currentId && (
        <section className="card" ref={jobRef}>
          {loadError && <p className="error">{loadError}</p>}
          {job ? (
            <JobStatus
              job={job}
              onPromoted={(updated) => {
                setJob(updated);
                refreshJobs();
              }}
            />
          ) : (
            <p className="muted">Loading job…</p>
          )}
          {transcript && <TranscriptView transcript={transcript} comparison={comparison} />}
        </section>
      )}

      <JobLists jobs={jobs} currentId={currentId} onOpen={openJob} onRefresh={refreshJobs} />

      <SpellingTable />
    </div>
  );
}

function UsageCard({ usage }) {
  const el = usage.elevenlabs;
  const tool = usage.tool;
  const percentUsed = el?.available && el.credits_limit > 0
    ? Math.min(100, Math.round((el.credits_used / el.credits_limit) * 100))
    : null;

  return (
    <section className="card usage" aria-label="ElevenLabs credits">
      <div className="usage-main">
        <h2 className="usage-title">ElevenLabs credits</h2>
        {el?.available ? (
          <>
            <p className="usage-numbers">
              <strong>{el.credits_left.toLocaleString()}</strong> left of{" "}
              {el.credits_limit.toLocaleString()}
              <span className="muted"> · {el.credits_used.toLocaleString()} used ({percentUsed}%)</span>
            </p>
            <div className={percentUsed >= 90 ? "meter low" : "meter"}>
              <div style={{ width: `${percentUsed}%` }} />
            </div>
            <p className="muted small">
              Whole account{el.tier && `, plan "${el.tier}"`}
              {el.resets_at && ` · resets ${new Date(el.resets_at * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`}
              {" · "}a per-key credit limit is only shown in the ElevenLabs dashboard
            </p>
          </>
        ) : (
          <p className="muted small">{el?.reason || "Credit information not available."}</p>
        )}
      </div>
      <div className="usage-tool">
        <span className="muted small">Transcribed with this tool</span>
        <strong>{formatTime(tool.audio_seconds)}</strong>
        <span className="muted small">{tool.jobs_done} finished job{tool.jobs_done === 1 ? "" : "s"}</span>
      </div>
    </section>
  );
}

const THEME_LABELS = { system: "System", light: "Light", dark: "Dark" };

function ThemeSwitch() {
  const [theme, setTheme] = useState(loadTheme);

  function choose(next) {
    setTheme(next);
    saveTheme(next);
    applyTheme(next);
  }

  return (
    <div className="theme-switch" role="group" aria-label="Colour theme">
      {THEMES.map((t) => (
        <button key={t} type="button" aria-pressed={theme === t}
          className={theme === t ? "active" : ""} onClick={() => choose(t)}>
          {THEME_LABELS[t]}
        </button>
      ))}
    </div>
  );
}

function SubmitForm({ onCreated }) {
  const [mode, setMode] = useState("youtube");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState(null);
  const [keyterms, setKeyterms] = useState("");
  const [engine, setEngine] = useState("elevenlabs");
  const [purpose, setPurpose] = useState("training");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState("");

  // The local model returns no word timings, so it can never produce training clips.
  const canChoosePurpose = engine !== "local";

  async function submit(e) {
    e.preventDefault();
    setError("");
    const terms = parseKeyterms(keyterms);

    if (mode === "youtube") {
      if (!/^https?:\/\/(www\.|m\.|music\.)?(youtube\.com|youtu\.be)\//i.test(url.trim())) {
        setError("Please paste a YouTube video link (youtube.com/watch?v=… or youtu.be/…).");
        return;
      }
    } else {
      if (!file) {
        setError("Please choose an audio or video file.");
        return;
      }
      const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
      if (!ALLOWED_EXTENSIONS.includes(ext)) {
        setError(`Unsupported file type "${ext}". Allowed: ${ALLOWED_EXTENSIONS.join(" ")}`);
        return;
      }
    }

    setBusy(true);
    try {
      const chosenPurpose = canChoosePurpose ? purpose : "transcribe";
      const result =
        mode === "youtube"
          ? await createYoutubeJob(url.trim(), terms, engine, chosenPurpose)
          : await uploadFile(file, terms, engine, chosenPurpose, setProgress);
      onCreated(result.job_id);
      setUrl("");
      setFile(null);
      e.target.reset();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={mode === "youtube"}
          className={mode === "youtube" ? "tab active" : "tab"}
          onClick={() => { setMode("youtube"); setError(""); }}>
          YouTube link
        </button>
        <button type="button" role="tab" aria-selected={mode === "upload"}
          className={mode === "upload" ? "tab active" : "tab"}
          onClick={() => { setMode("upload"); setError(""); }}>
          Upload file
        </button>
      </div>

      {mode === "youtube" ? (
        <label>
          YouTube video link
          <input type="url" placeholder="https://www.youtube.com/watch?v=…" value={url}
            onChange={(e) => setUrl(e.target.value)} disabled={busy} />
        </label>
      ) : (
        <label>
          Audio or video file (up to 2 GB)
          <input type="file" accept={["audio/*", "video/*", ...ALLOWED_EXTENSIONS].join(",")}
            onChange={(e) => setFile(e.target.files[0] || null)} disabled={busy} />
        </label>
      )}

      <label>
        Key terms <span className="muted">(optional, comma-separated)</span>
        <input type="text" placeholder="e.g. Fourier transform, eigenvalue, KTU" value={keyterms}
          onChange={(e) => setKeyterms(e.target.value)} disabled={busy} />
        <small className="muted">Technical words that appear in the lecture, to help recognition.</small>
      </label>

      <div className="choice">
        <span className="choice-label">Transcribe with</span>
        <div className="pills" role="group" aria-label="Transcription engine">
          {["elevenlabs", "local", "both"].map((value) => (
            <button key={value} type="button" disabled={busy}
              aria-pressed={engine === value}
              className={engine === value ? "active" : ""}
              onClick={() => setEngine(value)}>
              {ENGINE_LABELS[value]}
            </button>
          ))}
        </div>
        <small className="muted">
          {engine === "elevenlabs" && `Most accurate. Uses about ${CREDITS_PER_MINUTE} ElevenLabs credits per minute of audio.`}
          {engine === "local" && "Runs on this server. Free, but less accurate — and it cannot produce training data."}
          {engine === "both" && `Transcribes twice so you can compare them. Still uses about ${CREDITS_PER_MINUTE} credits per minute.`}
        </small>
      </div>

      {canChoosePurpose && (
        <div className="choice">
          <span className="choice-label">Use this for</span>
          <div className="pills" role="group" aria-label="What the job is for">
            <button type="button" disabled={busy} aria-pressed={purpose === "training"}
              className={purpose === "training" ? "active" : ""}
              onClick={() => setPurpose("training")}>
              Model training
            </button>
            <button type="button" disabled={busy} aria-pressed={purpose === "transcribe"}
              className={purpose === "transcribe" ? "active" : ""}
              onClick={() => setPurpose("transcribe")}>
              Just the transcript
            </button>
          </div>
          <small className="muted">
            {purpose === "training"
              ? "Cuts the lecture into short clips with a training manifest, and includes it in exported datasets."
              : "Transcript only — no clips, and never included in a training dataset. You can change your mind later, for free."}
          </small>
        </div>
      )}

      {error && <p className="error">{error}</p>}

      <div className="actions">
        <button type="submit" className="primary" disabled={busy}>
          {busy ? (mode === "upload" ? "Uploading…" : "Submitting…") : "Transcribe"}
        </button>
        {progress != null && (
          <span className="progress">
            <progress max="100" value={progress} /> {progress}%
          </span>
        )}
      </div>
    </form>
  );
}

function JobStatus({ job, onPromoted }) {
  const stages = stagesFor(job);
  const currentStage = stages.indexOf(job.status);
  const [promoting, setPromoting] = useState(false);
  const [promoteError, setPromoteError] = useState("");

  async function promote() {
    setPromoting(true);
    setPromoteError("");
    try {
      onPromoted(await promoteJob(job.job_id));
    } catch (err) {
      setPromoteError(err.message);
    } finally {
      setPromoting(false);
    }
  }

  return (
    <div>
      <h2>{job.title}</h2>
      <p className="muted">
        {job.source === "youtube" ? "YouTube" : "Uploaded file"}
        {job.duration != null && ` · ${formatTime(job.duration)} of audio`}
        {job.segment_count != null && ` · ${job.segment_count} segments`}
        {job.keyterms.length > 0 && ` · key terms: ${job.keyterms.join(", ")}`}
        {` · job ${job.job_id}`}
      </p>

      <p className="badges">
        <span className="badge engine">{ENGINE_LABELS[job.engine] || job.engine}</span>
        <span className={job.purpose === "training" ? "badge training" : "badge"}>
          {job.purpose === "training" ? "Training data" : "Transcript only"}
        </span>
      </p>

      {job.status === "done" && job.purpose === "transcribe" && (
        <div className="promote">
          {job.can_promote ? (
            <>
              <button type="button" onClick={promote} disabled={promoting}>
                {promoting ? "Cutting clips…" : "Use for training"}
              </button>
              <small className="muted">
                Cuts this lecture into training clips using the word timings already saved.
                Takes a few seconds and costs no ElevenLabs credits.
              </small>
            </>
          ) : (
            <small className="muted">
              {job.engine === "local"
                ? "The local model gives no word timings, so this cannot become training data. Transcribe it with ElevenLabs to use it for training."
                : "This job cannot be turned into training data."}
            </small>
          )}
          {promoteError && <p className="error">{promoteError}</p>}
        </div>
      )}

      {(job.source_url || job.audio_available) && (
        <div className="job-links">
          {job.source_url && (
            <a href={job.source_url} target="_blank" rel="noopener noreferrer" className="source-link">
              {job.source_url} ↗
            </a>
          )}
          {job.audio_available && (
            <a href={audioUrl(job.job_id)} download className="button">
              Download audio (WAV, 16 kHz mono)
            </a>
          )}
        </div>
      )}

      <ol className="stages">
        {stages.map((stage, i) => {
          let state = "";
          if (job.status === "failed") state = "";
          else if (i < currentStage || job.status === "done") state = "complete";
          else if (i === currentStage) state = "current";
          return <li key={stage} className={state}>{STAGE_LABELS[stage]}</li>;
        })}
      </ol>

      {job.status === "failed" && <p className="error">Failed: {job.error}</p>}

      {job.progress && job.progress.total > 0 && (
        <JobProgress progress={job.progress} />
      )}

      {(job.elevenlabs_seconds != null || job.local_seconds != null) && (
        <TranscriberTimes job={job} />
      )}

      {!FINAL.includes(job.status) && !job.progress && (
        <p className="muted">Working… this page updates every 3 seconds. Long lectures can take several minutes.</p>
      )}
    </div>
  );
}

function TranscriberTimes({ job }) {
  // "x times real time" says more than raw seconds: 4 minutes is fast for an
  // hour of audio and slow for a one-minute clip.
  const speed = (seconds) =>
    job.duration && seconds ? `${(job.duration / seconds).toFixed(1)}× real time` : null;

  const rows = [
    { name: "ElevenLabs", seconds: job.elevenlabs_seconds, note: "paid, uses credits" },
    { name: "Local model", seconds: job.local_seconds, note: "free, runs on this server" },
  ].filter((r) => r.seconds != null);

  return (
    <table className="times">
      <tbody>
        {rows.map((r) => (
          <tr key={r.name}>
            <th>{r.name}</th>
            <td className="time">{formatTime(r.seconds)}</td>
            <td className="muted">{speed(r.seconds)}</td>
            <td className="muted">{r.note}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function JobProgress({ progress }) {
  const { done, total, seconds_left: left } = progress;
  const percent = total ? Math.round((done / total) * 100) : 0;
  return (
    <div className="job-progress">
      <div className="meter">
        <div style={{ width: `${percent}%` }} />
      </div>
      <p className="muted small">
        Local model: <strong>{done} of {total}</strong> pieces transcribed ({percent}%)
        {left != null && left > 0 && ` · about ${formatTime(left)} left`}
      </p>
    </div>
  );
}

// One transcript with its own heading and its own Copy button. Each pane copies
// its own text, so in "Both" mode the two transcripts are separately copyable.
function TranscriptPane({ title, note, text, className = "" }) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState("");

  function copy() {
    setCopyError("");
    navigator.clipboard.writeText(text).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      // The browser refuses the clipboard on an insecure origin or without
      // permission. Without this the button simply never changed and looked broken.
      () => setCopyError("Could not copy automatically — select the text and copy it."),
    );
  }

  return (
    <div className={className}>
      <div className="transcript-head">
        <h3>
          {title}
          {note && <span className="muted"> ({note})</span>}
        </h3>
        <button type="button" onClick={copy} disabled={!text}>
          {copied ? "Copied" : "Copy text"}
        </button>
      </div>
      <div className="transcript-text">
        {text || <em className="muted">(no speech detected)</em>}
      </div>
      {copyError && <p className="error">{copyError}</p>}
    </div>
  );
}

function TranscriptView({ transcript, comparison }) {
  const isLocal = transcript.source === "local";

  return (
    <div className="transcript">
      <TranscriptPane
        title={isLocal ? "Transcript (local model)" : "Transcript (ElevenLabs)"}
        note={transcript.language_code}
        text={transcript.text}
      />

      {/* In "Both" mode the local transcript used to be hidden whenever a
          comparison existed — so the one mode that produces two transcripts was
          the only mode where the second could not be read or copied in full.
          The comparison below is a segment-by-segment view, not a substitute
          for the whole text. A local-only job has no local_text, so it gets no
          empty second pane. */}
      {transcript.local_text && (
        <TranscriptPane
          className="second-transcript"
          title="Transcript (local model)"
          text={transcript.local_text}
        />
      )}

      {comparison && <ComparisonTable comparison={comparison} />}

      {transcript.segments.length > 0 && (
        <details>
          <summary>Segments ({transcript.segments.length})</summary>
          <table className="segments">
            <thead>
              <tr><th>#</th><th>Time</th><th>Text</th></tr>
            </thead>
            <tbody>
              {transcript.segments.map((s, i) => (
                <tr key={i}>
                  <td className="num">{i + 1}</td>
                  <td className="time">{formatPrecise(s.start)} – {formatPrecise(s.end)}</td>
                  <td>{s.text}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}

function ComparisonTable({ comparison }) {
  const { overall, comparable, segments } = comparison;
  return (
    <div className="comparison">
      <h3>ElevenLabs vs local model</h3>

      {comparable ? (
        <p className="muted small">
          Overall agreement with ElevenLabs: <strong>{(overall.wer * 100).toFixed(1)}%</strong> of
          words differ, <strong>{(overall.cer * 100).toFixed(1)}%</strong> of characters.
          This measures agreement, not accuracy — ElevenLabs has its own mistakes.
        </p>
      ) : (
        <p className="warn-box">
          <strong>These scores are not meaningful yet.</strong> The local model writes English
          words in Malayalam letters while ElevenLabs keeps them in English, so roughly 40% of
          words differ on spelling alone rather than on what was heard. Fill in the English
          spelling table first (<code>build_english_map.py --fill</code>), then the numbers will
          compare the models rather than the alphabets.
        </p>
      )}

      <div className="scroll">
        <table className="segments compare">
          <thead>
            <tr>
              <th>#</th><th>Time</th><th>ElevenLabs</th><th>Local model</th>
              {comparable && <th>WER</th>}
            </tr>
          </thead>
          <tbody>
            {segments.map((s, i) => (
              <tr key={i}>
                <td className="num">{i + 1}</td>
                <td className="time">{formatPrecise(s.start)} – {formatPrecise(s.end)}</td>
                <td>{s.elevenlabs}</td>
                <td className="ml">{s.local || <em className="muted">(nothing)</em>}</td>
                {comparable && (
                  <td className="time">{s.scores.wer == null ? "–" : (s.scores.wer * 100).toFixed(0) + "%"}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function JobLists({ jobs, currentId, onOpen, onRefresh }) {
  const [tab, setTab] = useState("training");
  const training = jobs.filter((j) => j.purpose === "training");
  const transcripts = jobs.filter((j) => j.purpose !== "training");
  const shown = tab === "training" ? training : transcripts;

  return (
    <section className="card">
      <div className="transcript-head">
        <div className="tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === "training"}
            className={tab === "training" ? "tab active" : "tab"}
            onClick={() => setTab("training")}>
            Training data ({training.length})
          </button>
          <button type="button" role="tab" aria-selected={tab === "transcripts"}
            className={tab === "transcripts" ? "tab active" : "tab"}
            onClick={() => setTab("transcripts")}>
            Transcripts ({transcripts.length})
          </button>
        </div>
        <button type="button" onClick={onRefresh}>Refresh</button>
      </div>
      <p className="muted small">
        {tab === "training"
          ? "Lectures cut into clips with a manifest. Only these are exported as training data."
          : "Transcripts only — never included in a training dataset. Open one to turn it into training data."}
      </p>
      <JobList
        jobs={shown}
        currentId={currentId}
        onOpen={onOpen}
        empty={tab === "training"
          ? "No training lectures yet. Submit one above with “Model training” selected, or open a transcript and press “Use for training”."
          : "No transcript-only lectures yet. Submit one above with “Just the transcript” selected."}
      />
    </section>
  );
}

// Cards rather than a table: a table of six columns has to hide half of them on a
// phone, and the title is the only column anyone scans by.
function JobList({ jobs, currentId, onOpen, empty }) {
  if (jobs.length === 0) {
    return <p className="empty">{empty}</p>;
  }

  return (
    <ul className="job-cards">
      {jobs.map((j) => {
        const meta = [
          j.source_url ? "YouTube" : "Upload",
          ENGINE_LABELS[j.engine] || j.engine,
          j.duration != null ? formatTime(j.duration) : null,
          new Date(j.created_at).toLocaleString(),
        ].filter(Boolean);

        return (
          <li key={j.job_id}>
            <button
              type="button"
              onClick={() => onOpen(j.job_id)}
              className={j.job_id === currentId ? "job-card selected" : "job-card"}
              aria-current={j.job_id === currentId ? "true" : undefined}
            >
              <span className="job-card-top">
                <span className="job-card-title">{j.title}</span>
                <span className={`badge ${j.status}`}>{STAGE_LABELS[j.status] || j.status}</span>
              </span>
              <span className="job-card-meta">{meta.join(" · ")}</span>
            </button>
            {/* Outside the card button: a link inside a button is invalid HTML and
                needed a click-stopping workaround to not also open the job. */}
            {j.source_url && (
              <a className="job-card-link" href={j.source_url} title={j.source_url}
                target="_blank" rel="noopener noreferrer">
                Open on YouTube ↗
              </a>
            )}
          </li>
        );
      })}
    </ul>
  );
}

// How many spellings to show before "Show more". The table can run to hundreds
// of words, and the commonest ones are the ones worth reviewing.
const SPELLING_PAGE = 25;
// Alternative spellings shown per word before the rest are summarised.
const MAX_ALTERNATIVES = 4;

/* The English spelling table.

   The local model cannot write Latin letters at all, so every English word it
   hears comes out in Malayalam script. Without a spelling for each English word,
   comparing it against ElevenLabs measures the difference between two alphabets
   rather than between two transcriptions. A spelling only counts once a human has
   approved it: the proposals are derived from the model being measured, so using
   them unapproved would let it mark its own work. */
function SpellingTable() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [drafts, setDrafts] = useState({});
  const [saving, setSaving] = useState("");
  const [rowErrors, setRowErrors] = useState({});
  const [filter, setFilter] = useState("todo");
  const [shown, setShown] = useState(SPELLING_PAGE);

  useEffect(() => {
    getEnglishMap().then(setData, (err) => setError(err.message));
  }, []);

  async function save(word, approved) {
    const entry = data.entries.find((e) => e.word === word);
    const spelling = (drafts[word] ?? entry.spelling).trim();
    setSaving(word);
    setRowErrors((r) => ({ ...r, [word]: "" }));
    try {
      const res = await setEnglishSpelling(word, spelling, approved);
      setData((d) => ({
        coverage: res.coverage,
        entries: d.entries.map((e) =>
          e.word === word
            ? { ...e, spelling: res.spelling, status: res.status }
            : e),
      }));
      setDrafts((d) => {
        const next = { ...d };
        delete next[word];
        return next;
      });
    } catch (err) {
      setRowErrors((r) => ({ ...r, [word]: err.message }));
    } finally {
      setSaving("");
    }
  }

  if (error) {
    return (
      <section className="card">
        <h2>English spellings</h2>
        <p className="error">{error}</p>
      </section>
    );
  }
  if (!data) {
    return (
      <section className="card">
        <h2>English spellings</h2>
        <p className="muted">Loading…</p>
      </section>
    );
  }

  const { coverage } = data;
  const counted = {
    todo: data.entries.filter((e) => e.status !== "checked").length,
    approved: data.entries.filter((e) => e.status === "checked").length,
    all: data.entries.length,
  };
  const entries = data.entries.filter((e) =>
    filter === "approved" ? e.status === "checked"
      : filter === "todo" ? e.status !== "checked"
        : true);
  const visible = entries.slice(0, shown);

  return (
    <section className="card">
      <h2>English spellings</h2>
      <p className="muted small">
        How each English word is written in Malayalam letters. Until a word is approved
        here, the comparison scores count it as wrong purely because the two transcripts
        used different alphabets.
      </p>

      <div className="coverage">
        <div className="meter">
          <div style={{ width: `${coverage.coverage_percent}%` }} />
        </div>
        <p className="muted small">
          <strong>{coverage.coverage_percent}%</strong> of the English actually spoken is
          covered — {coverage.words_approved} of {coverage.words_total} words approved,
          accounting for {coverage.occurrences_covered} of {coverage.occurrences_total}{" "}
          times an English word was spoken. Approving the commonest words first is worth
          far more than approving the most words.
        </p>
      </div>

      {data.entries.length === 0 ? (
        <p className="empty">
          No English words collected yet. Transcribe a lecture with ElevenLabs, then run{" "}
          <code>build_english_map.py --collect</code> on the server to gather the words it
          heard in English.
        </p>
      ) : (
        <>
          <div className="pills spelling-filter">
            {[["todo", "Needs review"], ["approved", "Approved"], ["all", "All"]].map(
              ([value, label]) => (
                <button key={value} type="button"
                  className={filter === value ? "active" : ""}
                  onClick={() => { setFilter(value); setShown(SPELLING_PAGE); }}>
                  {label} ({counted[value]})
                </button>
              ))}
          </div>

          {entries.length === 0 ? (
            <p className="empty">
              {filter === "todo"
                ? "Every collected word has been approved."
                : "Nothing approved yet."}
            </p>
          ) : (
            <ul className="spellings">
              {visible.map((e) => (
                <SpellingRow
                  key={e.word}
                  entry={e}
                  draft={drafts[e.word]}
                  busy={saving === e.word}
                  error={rowErrors[e.word]}
                  onDraft={(value) => setDrafts((d) => ({ ...d, [e.word]: value }))}
                  onSave={(approved) => save(e.word, approved)}
                />
              ))}
            </ul>
          )}

          {visible.length < entries.length && (
            <button type="button" className="show-more"
              onClick={() => setShown((n) => n + SPELLING_PAGE)}>
              Show {Math.min(SPELLING_PAGE, entries.length - visible.length)} more
              {" "}({entries.length - visible.length} left)
            </button>
          )}
        </>
      )}
    </section>
  );
}

function SpellingRow({ entry, draft, busy, error, onDraft, onSave }) {
  const value = draft ?? entry.spelling;
  const approved = entry.status === "checked";
  const changed = draft != null && draft !== entry.spelling;
  // More than one spelling means the model wrote the same word different ways,
  // which is a reason to look at this one rather than wave it through. Only the
  // commonest few are worth showing: a word said eighty times can have twenty
  // one-off misreadings behind it, and listing them all buries the row.
  const everyAlternative = Object.entries(entry.alternatives || {})
    .filter(([spelling]) => spelling !== value)
    .sort((a, b) => b[1] - a[1]);
  const alternatives = everyAlternative.slice(0, MAX_ALTERNATIVES);
  const rarer = everyAlternative.length - alternatives.length;

  return (
    <li className={approved ? "spelling approved" : "spelling"}>
      <div className="spelling-word">
        <strong>{entry.word}</strong>
        <span className="muted small">said {entry.occurrences}×</span>
      </div>

      <input
        type="text"
        className="ml"
        lang="ml"
        value={value}
        placeholder="Malayalam spelling"
        aria-label={`Malayalam spelling for ${entry.word}`}
        onChange={(ev) => onDraft(ev.target.value)}
      />

      <div className="spelling-actions">
        <button type="button" onClick={() => onSave(true)}
          disabled={busy || !value.trim() || (approved && !changed)}>
          {busy ? "Saving…" : approved && !changed ? "Approved" : "Approve"}
        </button>
        {approved && !changed && (
          <button type="button" onClick={() => onSave(false)} disabled={busy}
            title="Stop this spelling being used for scoring and training">
            Unapprove
          </button>
        )}
      </div>

      {alternatives.length > 0 && (
        <p className="spelling-alts small muted">
          Also heard as:{" "}
          {alternatives.map(([spelling, n]) => (
            <button key={spelling} type="button" className="alt"
              onClick={() => onDraft(spelling)}>
              <span className="ml">{spelling}</span> ×{n}
            </button>
          ))}
          {rarer > 0 && <span>and {rarer} rarer {rarer === 1 ? "spelling" : "spellings"}</span>}
        </p>
      )}
      {error && <p className="error spelling-error">{error}</p>}
    </li>
  );
}
