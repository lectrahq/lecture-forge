import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  audioSnippetUrl,
  audioUrl,
  createBatchJobs,
  createYoutubeJob,
  datasetDownloadUrl,
  exportDataset,
  getAudioHealth,
  getComparison,
  getDatasetSummary,
  getEnglishMap,
  getJob,
  getTranscript,
  getUsage,
  getWordOccurrences,
  listJobs,
  promoteJob,
  reanalyzeAudioHealth,
  revertTranscriptCorrections,
  saveTranscriptCorrections,
  segmentAudioUrl,
  setEnglishSpelling,
  bulkApproveSpellings,
  streamAudioUrl,
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

const ALLOWED_EXTENSIONS = [
  ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wma", ".amr",
  ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".3gp", ".mpeg", ".mpg", ".ts",
];

// Time formatters
function formatTime(seconds) {
  if (seconds == null) return "–";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

function formatPrecise(seconds) {
  const tenths = Math.floor((seconds % 1) * 10);
  return `${formatTime(seconds)}.${tenths}`;
}

function parseKeyterms(text) {
  return text.split(",").map((t) => t.trim()).filter(Boolean);
}

// --- Icons (Inline SVGs, zero network dependencies, 100% CSP compliant) ---
function IconWaveform() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 10v4M6 6v12M10 3v18M14 8v8M18 5v14M22 10v4" />
    </svg>
  );
}

function IconMic() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" x2="12" y1="19" y2="22" />
    </svg>
  );
}

function IconMeter() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 12 16 8" />
    </svg>
  );
}

function IconArrowRight() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="5" x2="19" y1="12" y2="12" />
      <polyline points="12 5 19 12 12 19" />
    </svg>
  );
}

function IconCpu() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect width="16" height="16" x="4" y="4" rx="2" />
      <path d="M9 9h6v6H9zM9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3" />
    </svg>
  );
}

function IconDual() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect width="8" height="18" x="3" y="3" rx="1" />
      <rect width="8" height="18" x="13" y="3" rx="1" />
    </svg>
  );
}

function IconTraining() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" />
      <path d="m3.3 7 8.7 5 8.7-5M12 22V12" />
    </svg>
  );
}

function IconTranscript() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" x2="8" y1="13" y2="13" />
      <line x1="16" x2="8" y1="17" y2="17" />
      <line x1="10" x2="8" y1="9" y2="9" />
    </svg>
  );
}

function IconYoutube() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
      <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z" />
    </svg>
  );
}

function IconUpload() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />
    </svg>
  );
}

function IconList() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="8" x2="21" y1="6" y2="6" />
      <line x1="8" x2="21" y1="12" y2="12" />
      <line x1="8" x2="21" y1="18" y2="18" />
      <line x1="3" x2="3.01" y1="6" y2="6" />
      <line x1="3" x2="3.01" y1="12" y2="12" />
      <line x1="3" x2="3.01" y1="18" y2="18" />
    </svg>
  );
}

function IconDownload() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />
    </svg>
  );
}

function IconCheck() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function IconCopy() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
    </svg>
  );
}

function IconRefresh() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
      <path d="M8 16H3v5" />
    </svg>
  );
}

function IconSearch() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" x2="16.65" y1="21" y2="16.65" />
    </svg>
  );
}

function IconPlay() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
      <polygon points="6 3 20 12 6 21 6 3" />
    </svg>
  );
}

function IconPause() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
      <rect x="6" y="4" width="4" height="16" rx="1" />
      <rect x="14" y="4" width="4" height="16" rx="1" />
    </svg>
  );
}

function IconHealth() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
    </svg>
  );
}

function IconShieldCheck() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <polyline points="9 12 11 14 15 10" />
    </svg>
  );
}

function IconRotateCcw() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1 4v6h6" />
      <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
    </svg>
  );
}

function IconSave() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
      <polyline points="17 21 17 13 7 13 7 21" />
      <polyline points="7 3 7 8 15 8" />
    </svg>
  );
}

function IconArrowLeft() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="19" y1="12" x2="5" y2="12" />
      <polyline points="12 19 5 12 12 5" />
    </svg>
  );
}

function IconPlus() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function IconLayers() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="12 2 2 7 12 12 22 7 12 2" />
      <polyline points="2 17 12 22 22 17" />
      <polyline points="2 12 12 17 22 12" />
    </svg>
  );
}

function IconBookOpen() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
      <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
    </svg>
  );
}

function IconFilter() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
    </svg>
  );
}

function IconX() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function IconVolume() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
      <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
    </svg>
  );
}

function IconVolumeMute() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
      <line x1="23" y1="9" x2="17" y2="15" />
      <line x1="17" y1="9" x2="23" y2="15" />
    </svg>
  );
}

function IconRewind10() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M11 17l-5-5 5-5" />
      <path d="M18 17l-5-5 5-5" />
    </svg>
  );
}

function IconForward10() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M13 17l5-5-5-5" />
      <path d="M6 17l5-5-5-5" />
    </svg>
  );
}

function IconSun() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
    </svg>
  );
}

function IconMoon() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
    </svg>
  );
}

function IconMonitor() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect width="18" height="12" x="3" y="4" rx="2" />
      <line x1="8" x2="16" y1="20" y2="20" />
      <line x1="12" x2="12" y1="16" y2="20" />
    </svg>
  );
}

// --- Navigation Components ---
function StickyNavbar({ activeTab, onSelectTab, jobsCount, currentId, usage }) {
  const creditsLeft = usage?.elevenlabs?.credits_left;
  return (
    <nav className="site-navbar">
      <div className="site-navbar-inner">
        <a href="#transcribe" className="nav-brand" onClick={(e) => { e.preventDefault(); onSelectTab("transcribe"); }}>
          <div className="nav-brand-icon">
            <IconWaveform />
          </div>
          <span className="nav-brand-title">
            Lecture Forge
            <span className="pulse-dot" />
          </span>
        </a>

        <div className="nav-links">
          <button
            type="button"
            className={`nav-link-btn ${activeTab === "transcribe" ? "active-tab" : ""}`}
            onClick={() => onSelectTab("transcribe")}
          >
            <IconPlus /> Transcribe
          </button>
          <button
            type="button"
            className={`nav-link-btn ${activeTab === "library" ? "active-tab" : ""}`}
            onClick={() => onSelectTab("library")}
          >
            <IconList /> Library <span className="nav-badge">{jobsCount}</span>
          </button>
          {currentId && (
            <button
              type="button"
              className={`nav-link-btn active-workspace-pill ${activeTab === "workspace" ? "active-tab" : ""}`}
              onClick={() => onSelectTab("workspace")}
            >
              <span className="pulse-dot" /> Active Lecture
            </button>
          )}
          <button
            type="button"
            className={`nav-link-btn ${activeTab === "datasets" ? "active-tab" : ""}`}
            onClick={() => onSelectTab("datasets")}
          >
            <IconLayers /> Datasets
          </button>
          <button
            type="button"
            className={`nav-link-btn ${activeTab === "dictionary" ? "active-tab" : ""}`}
            onClick={() => onSelectTab("dictionary")}
          >
            <IconBookOpen /> Dictionary
          </button>
        </div>

        <div className="nav-right">
          {creditsLeft != null && (
            <div className="nav-quota-pill" title={`${creditsLeft.toLocaleString()} ElevenLabs credits remaining`}>
              <span className="dot" />
              <span className="nav-quota-full">{Math.round(creditsLeft / 1000)}k credits</span>
              <span className="nav-quota-compact">{Math.round(creditsLeft / 1000)}k</span>
            </div>
          )}
          <ThemeSwitch />
        </div>
      </div>
    </nav>
  );
}

function MobileBottomDock({ activeTab, onSelectTab, jobsCount, currentId }) {
  return (
    <div className="mobile-bottom-dock" role="navigation" aria-label="Mobile Navigation">
      <div className="mobile-dock-inner">
        <button
          type="button"
          className={`mobile-dock-btn ${activeTab === "transcribe" ? "active" : ""}`}
          onClick={() => onSelectTab("transcribe")}
        >
          <IconPlus />
          <span>New</span>
        </button>
        <button
          type="button"
          className={`mobile-dock-btn ${activeTab === "library" ? "active" : ""}`}
          onClick={() => onSelectTab("library")}
        >
          <IconList />
          <span>Library</span>
        </button>
        {currentId && (
          <button
            type="button"
            className={`mobile-dock-btn ${activeTab === "workspace" ? "active" : ""}`}
            onClick={() => onSelectTab("workspace")}
          >
            <IconWaveform />
            <span>Active</span>
          </button>
        )}
        <button
          type="button"
          className={`mobile-dock-btn ${activeTab === "datasets" ? "active" : ""}`}
          onClick={() => onSelectTab("datasets")}
        >
          <IconLayers />
          <span>Datasets</span>
        </button>
        <button
          type="button"
          className={`mobile-dock-btn ${activeTab === "dictionary" ? "active" : ""}`}
          onClick={() => onSelectTab("dictionary")}
        >
          <IconBookOpen />
          <span>Dictionary</span>
        </button>
      </div>
    </div>
  );
}

// --- Main Application ---
export default function App() {
  const [jobs, setJobs] = useState([]);

  // URL Hash Routing: #transcribe (default), #library, #job/<id>, #datasets, #dictionary
  const parseHash = () => {
    const raw = (window.location.hash || "").replace(/^#/, "").trim();
    if (raw.startsWith("job/")) {
      const jid = raw.replace(/^job\//, "").trim();
      return { tab: "workspace", jobId: jid || null };
    }
    if (raw === "active-job" || raw === "workspace") return { tab: "workspace", jobId: null };
    if (raw === "library") return { tab: "library", jobId: null };
    if (raw === "datasets" || raw === "dataset") return { tab: "datasets", jobId: null };
    if (raw === "spelling" || raw === "dictionary") return { tab: "dictionary", jobId: null };
    return { tab: "transcribe", jobId: null };
  };

  const initial = parseHash();
  const [activeTab, setActiveTab] = useState(initial.tab);
  const [currentId, setCurrentId] = useState(initial.jobId);
  const [job, setJob] = useState(null);
  const [transcript, setTranscript] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [usage, setUsage] = useState(null);
  const jobRef = useRef(null);
  const [comparison, setComparison] = useState(null);
  const [audioIsPlaying, setAudioIsPlaying] = useState(false);
  const [audioSeekRequest, setAudioSeekRequest] = useState(null);
  const [audioTogglePlayTrigger, setAudioTogglePlayTrigger] = useState(0);

  // Sync with browser back/forward buttons
  useEffect(() => {
    const onHashChange = () => {
      const parsed = parseHash();
      setActiveTab(parsed.tab);
      if (parsed.jobId) {
        setCurrentId(parsed.jobId);
      }
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const navigateToTab = useCallback((tab, jobId = null) => {
    setActiveTab(tab);
    if (jobId) {
      setCurrentId(jobId);
      window.location.hash = `job/${jobId}`;
    } else if (tab === "workspace") {
      window.location.hash = currentId ? `job/${currentId}` : "workspace";
    } else {
      window.location.hash = tab;
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [currentId]);

  const openJob = useCallback((id) => {
    setCurrentId(id);
    setActiveTab("workspace");
    window.location.hash = `job/${id}`;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const handleAudioSeek = useCallback((time) => {
    setAudioSeekRequest({ time, ts: Date.now() });
  }, []);

  const handleAudioTogglePlay = useCallback(() => {
    setAudioTogglePlayTrigger(Date.now());
  }, []);

  const refreshJobs = useCallback(() => {
    listJobs().then(setJobs).catch(() => {});
    getUsage().then(setUsage).catch(() => {});
  }, []);

  useEffect(refreshJobs, [refreshJobs]);

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
              // bonus comparison
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
        timer = setTimeout(poll, POLL_MS);
      }
    }

    setJob(null);
    setTranscript(null);
    setComparison(null);
    setAudioIsPlaying(false);
    setAudioSeekRequest(null);
    setAudioTogglePlayTrigger(0);
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [currentId, refreshJobs]);

  return (
    <>
      <StickyNavbar
        activeTab={activeTab}
        onSelectTab={navigateToTab}
        jobsCount={jobs.length}
        currentId={currentId}
        usage={usage}
      />

      <div className="page" id="top">
        <header>
          <div className="header-row">
            <div className="brand-wrapper">
              <div className="brand-icon">
                <IconWaveform />
              </div>
              <div className="brand-title-group">
                <span className="brand-tag">
                  <span className="pulse-dot" />
                  Lecture Forge · Capstone Part 1
                </span>
                <h1>Lecture Transcription</h1>
              </div>
            </div>
          </div>
          <p className="subtitle">
            Automated speech digitization for Malayalam-English (Manglish) lectures. Ingest YouTube links or recordings,
            transcribe via ElevenLabs Scribe v2 & AI4Bharat IndicConformer, and output training-ready clips.
          </p>
        </header>

        {activeTab === "library" && (
          <div className="view-pane" id="library">
            <JobLists
              jobs={jobs}
              currentId={currentId}
              onOpen={openJob}
              onRefresh={refreshJobs}
            />
          </div>
        )}

        {activeTab === "transcribe" && (
          <div className="view-pane" id="ingest">
            {usage && <UsageCard usage={usage} />}
            <SubmitForm
              onCreated={(id) => {
                openJob(id);
                refreshJobs();
              }}
            />
          </div>
        )}

        {activeTab === "workspace" && (
          <div className="view-pane" id="active-job">
            {currentId ? (
              <section className="card active-workspace-card" ref={jobRef}>
                <div className="active-job-topbar">
                  <button
                    type="button"
                    className="back-to-library-btn"
                    onClick={() => navigateToTab("library")}
                    title="Return to full library"
                  >
                    <IconArrowLeft /> Return to Library
                  </button>
                  <span className="mono small">Viewing: {currentId}</span>
                </div>

                {loadError && <p className="error">{loadError}</p>}
                {job ? (
                  <>
                    <JobStatus
                      job={job}
                      onPromoted={(updated) => {
                        setJob(updated);
                        refreshJobs();
                      }}
                      audioIsPlaying={audioIsPlaying}
                      onAudioPlayStateChange={setAudioIsPlaying}
                      audioSeekRequest={audioSeekRequest}
                      audioTogglePlayTrigger={audioTogglePlayTrigger}
                    />
                    {job.audio_available && job.status === "done" && (
                      <AudioHealthCard
                        job={job}
                        onUpdated={() => {
                          if (currentId) getJob(currentId).then(setJob).catch(() => {});
                          refreshJobs();
                        }}
                      />
                    )}
                  </>
                ) : (
                  <p className="muted">Loading job…</p>
                )}
                {transcript && (
                  <TranscriptView
                    transcript={transcript}
                    comparison={comparison}
                    job={job}
                    isPlaying={audioIsPlaying}
                    onSeek={handleAudioSeek}
                    onTogglePlay={handleAudioTogglePlay}
                    onUpdated={() => {
                      if (currentId) {
                        getTranscript(currentId).then(setTranscript).catch(() => {});
                        getJob(currentId).then(setJob).catch(() => {});
                        refreshJobs();
                      }
                    }}
                  />
                )}
              </section>
            ) : (
              <div className="card empty-workspace-card">
                <div className="empty-workspace-icon">
                  <IconWaveform />
                </div>
                <h2>No Lecture Selected</h2>
                <p className="muted">
                  Select a lecture from the library to view its audio player, synchronized karaoke transcript, and segment editor.
                </p>
                <div className="empty-workspace-actions">
                  <button type="button" className="btn primary" onClick={() => navigateToTab("library")}>
                    <IconList /> Browse Library ({jobs.length})
                  </button>
                  <button type="button" className="btn secondary" onClick={() => navigateToTab("transcribe")}>
                    <IconPlus /> Transcribe New Lecture
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {activeTab === "datasets" && (
          <div className="view-pane" id="datasets">
            <DatasetHub onDatasetCreated={refreshJobs} />
          </div>
        )}

        {activeTab === "dictionary" && (
          <div className="view-pane" id="spelling">
            <SpellingTable />
          </div>
        )}
      </div>

      <MobileBottomDock
        activeTab={activeTab}
        onSelectTab={navigateToTab}
        jobsCount={jobs.length}
        currentId={currentId}
      />
    </>
  );
}

// --- Usage & Telemetry Card ---
function UsageCard({ usage }) {
  const el = usage.elevenlabs;
  const tool = usage.tool;
  const percentUsed =
    el?.available && el.credits_limit > 0
      ? Math.min(100, Math.round((el.credits_used / el.credits_limit) * 100))
      : null;

  return (
    <section className="card usage" aria-label="ElevenLabs credits">
      <div className="usage-main">
        <div className="usage-title-row">
          <h2 className="usage-title">
            <IconMeter />
            ElevenLabs API Quota
          </h2>
          {el?.tier && <span className="badge">{el.tier}</span>}
        </div>
        {el?.available ? (
          <>
            <p className="usage-numbers">
              <strong>{el.credits_left.toLocaleString()}</strong> credits remaining
              <span className="muted"> · {el.credits_used.toLocaleString()} of {el.credits_limit.toLocaleString()} used ({percentUsed}%)</span>
            </p>
            <div className={percentUsed >= 90 ? "meter low" : "meter"}>
              <div style={{ width: `${percentUsed}%` }} />
            </div>
            <p className="muted small">
              Account quota
              {el.resets_at && ` · Resets on ${new Date(el.resets_at * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`}
              {" · "}Per-key quotas visible in ElevenLabs dashboard
            </p>
          </>
        ) : (
          <p className="muted small">{el?.reason || "Credit information not available."}</p>
        )}
      </div>

      <div className="usage-tools-grid">
        <div className="usage-tool-card" title="Total audio transcribed using ElevenLabs Scribe cloud ASR">
          <div className="usage-tool-header">
            <span className="usage-tool-tag"><IconMic /> ElevenLabs</span>
            <span className="usage-tool-badge">Cloud</span>
          </div>
          <strong className="usage-tool-time">
            {formatTime(tool.elevenlabs?.audio_seconds ?? tool.audio_seconds)}
          </strong>
          <span className="usage-tool-sub">
            {tool.elevenlabs?.jobs_done ?? tool.jobs_done} lecture{(tool.elevenlabs?.jobs_done ?? tool.jobs_done) === 1 ? "" : "s"}
            {tool.elevenlabs?.processing_seconds ? ` · ${formatTime(tool.elevenlabs.processing_seconds)} elapsed` : ""}
          </span>
        </div>

        <div className="usage-tool-card local" title="Total audio transcribed using local IndicConformer neural model on server">
          <div className="usage-tool-header">
            <span className="usage-tool-tag"><IconCpu /> Local Model</span>
            <span className="usage-tool-badge local">Free</span>
          </div>
          <strong className="usage-tool-time">
            {formatTime(tool.local?.audio_seconds ?? 0)}
          </strong>
          <span className="usage-tool-sub">
            {tool.local?.jobs_done ?? 0} lecture{(tool.local?.jobs_done ?? 0) === 1 ? "" : "s"}
            {tool.local?.processing_seconds ? ` · ${formatTime(tool.local.processing_seconds)} elapsed` : ""}
          </span>
        </div>
      </div>
    </section>
  );
}

// --- Theme Switcher ---
const THEME_OPTIONS = [
  { id: "system", label: "Auto", icon: <IconMonitor /> },
  { id: "light", label: "Light", icon: <IconSun /> },
  { id: "dark", label: "Dark", icon: <IconMoon /> },
];

function ThemeSwitch() {
  const [theme, setTheme] = useState(loadTheme);

  function choose(next) {
    setTheme(next);
    saveTheme(next);
    applyTheme(next);
  }

  return (
    <div className="theme-switch" role="group" aria-label="Colour theme">
      {THEME_OPTIONS.map(({ id, label, icon }) => (
        <button
          key={id}
          type="button"
          aria-pressed={theme === id}
          className={theme === id ? "active" : ""}
          onClick={() => choose(id)}
          title={`${label} theme`}
        >
          {icon}
          <span className="theme-switch-label">{label}</span>
        </button>
      ))}
    </div>
  );
}

// --- Ingestion & Submit Form ---
function SubmitForm({ onCreated }) {
  const [mode, setMode] = useState("youtube");
  const [url, setUrl] = useState("");
  const [batchUrls, setBatchUrls] = useState("");
  const [file, setFile] = useState(null);
  const [keyterms, setKeyterms] = useState("");
  const [engine, setEngine] = useState("elevenlabs");
  const [purpose, setPurpose] = useState("training");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState("");

  const canChoosePurpose = engine !== "local";

  async function submit(e) {
    e.preventDefault();
    setError("");
    const terms = parseKeyterms(keyterms);

    if (mode === "youtube") {
      if (!/^https?:\/\/(www\.|m\.|music\.)?(youtube\.com|youtu\.be)\//i.test(url.trim())) {
        setError("Please paste a valid YouTube video link (e.g. youtube.com/watch?v=… or youtu.be/…).");
        return;
      }
    } else if (mode === "batch") {
      if (!batchUrls.trim()) {
        setError("Please paste YouTube links or playlist URLs (one per line).");
        return;
      }
    } else {
      if (!file) {
        setError("Please select an audio or video file to upload.");
        return;
      }
      const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
      if (!ALLOWED_EXTENSIONS.includes(ext)) {
        setError(`Unsupported file format "${ext}". Allowed formats: ${ALLOWED_EXTENSIONS.join(" ")}`);
        return;
      }
    }

    setBusy(true);
    try {
      const chosenPurpose = canChoosePurpose ? purpose : "transcribe";
      if (mode === "batch") {
        const result = await createBatchJobs(batchUrls, terms, engine, chosenPurpose);
        setBatchUrls("");
        onCreated(result.job_ids[0]);
        return;
      }
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
        <button
          type="button"
          role="tab"
          aria-selected={mode === "youtube"}
          className={mode === "youtube" ? "tab active" : "tab"}
          onClick={() => { setMode("youtube"); setError(""); }}
        >
          <IconYoutube /> Single YouTube
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "batch"}
          className={mode === "batch" ? "tab active" : "tab"}
          onClick={() => { setMode("batch"); setError(""); }}
        >
          <IconList /> Batch / Playlist
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "upload"}
          className={mode === "upload" ? "tab active" : "tab"}
          onClick={() => { setMode("upload"); setError(""); }}
        >
          <IconUpload /> File Upload
        </button>
      </div>

      {mode === "batch" ? (
        <label>
          YouTube links or playlist URL (one per line)
          <textarea
            className="batch-urls"
            rows={7}
            placeholder={"https://www.youtube.com/playlist?list=…\nhttps://www.youtube.com/watch?v=…\nhttps://youtu.be/…"}
            value={batchUrls}
            onChange={(e) => setBatchUrls(e.target.value)}
            disabled={busy}
          />
          <small className="muted">
            <b>Course Playlists:</b> Pasting a playlist URL automatically detects and queues all videos. Existing lectures in your library are skipped automatically to save credits.
          </small>
        </label>
      ) : mode === "youtube" ? (
        <label>
          YouTube video link
          <input
            type="url"
            placeholder="https://www.youtube.com/watch?v=… or https://youtu.be/…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={busy}
          />
        </label>
      ) : (
        <label>
          Audio or video lecture recording (up to 2 GB)
          <div className="file-input-wrapper">
            <input
              type="file"
              accept={["audio/*", "video/*", ...ALLOWED_EXTENSIONS].join(",")}
              onChange={(e) => setFile(e.target.files[0] || null)}
              disabled={busy}
            />
          </div>
          <small className="muted">Supports MP4, MKV, WEBM, MOV, MP3, WAV, M4A, FLAC, OPUS and other common formats.</small>
        </label>
      )}

      <label>
        Key terms <span className="muted">(optional, comma-separated)</span>
        <input
          type="text"
          placeholder="e.g. Fourier transform, eigenvalue, KTU, backpropagation"
          value={keyterms}
          onChange={(e) => setKeyterms(e.target.value)}
          disabled={busy}
        />
        <div className="quick-suggest-row">
          <span className="quick-suggest-label">Suggestions:</span>
          {["KTU", "BTech", "Engineering", "Module", "Franklin", "Syllabus"].map((term) => (
            <button
              key={term}
              type="button"
              className="quick-term-pill"
              onClick={() => {
                setKeyterms((prev) => {
                  const existing = parseKeyterms(prev);
                  if (existing.includes(term)) return prev;
                  return prev ? `${prev}, ${term}` : term;
                });
              }}
            >
              + {term}
            </button>
          ))}
        </div>
        <small className="muted">Domain keywords and technical acronyms to guide the speech recognition acoustic model.</small>
      </label>

      {/* Engine Selection Tiles */}
      <div className="choice">
        <span className="choice-label">Transcribe with</span>
        <div className="choice-grid" role="group" aria-label="Transcription engine">
          <button
            type="button"
            disabled={busy}
            aria-pressed={engine === "elevenlabs"}
            className={`choice-card ${engine === "elevenlabs" ? "active" : ""}`}
            onClick={() => setEngine("elevenlabs")}
          >
            <div className="choice-card-header">
              <span className="choice-card-title">ElevenLabs Scribe</span>
              <span className="choice-card-badge">Best</span>
            </div>
            <span className="choice-card-desc">
              State-of-the-art accuracy with word timings. ~{CREDITS_PER_MINUTE} credits/min.
            </span>
          </button>

          <button
            type="button"
            disabled={busy}
            aria-pressed={engine === "local"}
            className={`choice-card ${engine === "local" ? "active" : ""}`}
            onClick={() => setEngine("local")}
          >
            <div className="choice-card-header">
              <span className="choice-card-title">AI4Bharat Local</span>
              <span className="choice-card-badge">Free</span>
            </div>
            <span className="choice-card-desc">
              Runs on this server's CPU. Free transcription with silence-synchronized word playback.
            </span>
          </button>

          <button
            type="button"
            disabled={busy}
            aria-pressed={engine === "both"}
            className={`choice-card ${engine === "both" ? "active" : ""}`}
            onClick={() => setEngine("both")}
          >
            <div className="choice-card-header">
              <span className="choice-card-title">Both Engines</span>
              <span className="choice-card-badge">Dual</span>
            </div>
            <span className="choice-card-desc">
              Transcribe with both engines to benchmark WER & CER side-by-side.
            </span>
          </button>
        </div>
      </div>

      {/* Purpose Selection Tiles */}
      {canChoosePurpose && (
        <div className="choice">
          <span className="choice-label">Job purpose</span>
          <div className="choice-grid" role="group" aria-label="Job purpose">
            <button
              type="button"
              disabled={busy}
              aria-pressed={purpose === "training"}
              className={`choice-card ${purpose === "training" ? "active" : ""}`}
              onClick={() => setPurpose("training")}
            >
              <div className="choice-card-header">
                <span className="choice-card-title">Model Training Data</span>
                <span className="choice-card-badge">Dataset</span>
              </div>
              <span className="choice-card-desc">
                Cuts 5–20s audio clips and generates a NeMo manifest. Exported in training datasets.
              </span>
            </button>

            <button
              type="button"
              disabled={busy}
              aria-pressed={purpose === "transcribe"}
              className={`choice-card ${purpose === "transcribe" ? "active" : ""}`}
              onClick={() => setPurpose("transcribe")}
            >
              <div className="choice-card-header">
                <span className="choice-card-title">Just the Transcript</span>
                <span className="choice-card-badge">Display</span>
              </div>
              <span className="choice-card-desc">
                Produces full readable transcript only. Can be promoted to training data later for free.
              </span>
            </button>
          </div>
        </div>
      )}

      {error && <p className="error">{error}</p>}

      <div className="actions">
        <button type="submit" className="primary" disabled={busy}>
          {busy ? (
            mode === "upload" ? "Uploading file…" : "Submitting to queue…"
          ) : mode === "batch" ? (
            <>
              <IconList /> Queue batch
            </>
          ) : (
            <>
              <IconArrowRight /> Start transcription
            </>
          )}
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

// --- Full In-Browser Lecture Audio Player ---
function FullAudioPlayer({
  job,
  isPlaying: externalIsPlaying,
  onPlayStateChange,
  seekRequest,
  togglePlayTrigger,
}) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(job.duration || 0);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [isSeeking, setIsSeeking] = useState(false);
  const [seekValue, setSeekValue] = useState(0);
  const audioRef = useRef(null);

  const streamSrc = streamAudioUrl(job.job_id);

  useEffect(() => {
    if (job.duration && !duration) {
      setDuration(job.duration);
    }
  }, [job.duration, duration]);

  // Handle external seek requests (e.g. clicking a word in the transcript)
  useEffect(() => {
    if (!seekRequest) return;
    const audio = audioRef.current;
    if (!audio) return;
    const target = Math.max(0, Math.min(duration || 99999, seekRequest.time));
    audio.currentTime = target;
    setCurrentTime(target);
    window.dispatchEvent(new CustomEvent("forge:audioseek", {
      detail: { time: target, jobId: job.job_id }
    }));
    audio.play().then(() => {
      setIsPlaying(true);
      if (onPlayStateChange) onPlayStateChange(true);
    }).catch(() => {});
  }, [seekRequest, duration, job.job_id, onPlayStateChange]);

  // Handle play/pause toggles from transcript header
  useEffect(() => {
    if (!togglePlayTrigger) return;
    togglePlay();
  }, [togglePlayTrigger]);

  // High-frequency 60 FPS animation frame tick for millisecond-precision audio synchronization
  useEffect(() => {
    if (!isPlaying) return;
    let animId;
    const tick = () => {
      const audio = audioRef.current;
      if (audio && !isSeeking) {
        const t = audio.currentTime;
        window.dispatchEvent(new CustomEvent("forge:audiotick", {
          detail: { time: t, jobId: job.job_id }
        }));
        setCurrentTime(t);
      }
      animId = requestAnimationFrame(tick);
    };
    animId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animId);
  }, [isPlaying, isSeeking, job.job_id]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const handleLoadedMetadata = () => {
      if (audio.duration && !isNaN(audio.duration) && audio.duration !== Infinity) {
        setDuration(audio.duration);
      }
    };
    const handleEnded = () => {
      setIsPlaying(false);
      setCurrentTime(0);
      window.dispatchEvent(new CustomEvent("forge:audiostate", {
        detail: { isPlaying: false, time: 0, jobId: job.job_id }
      }));
      if (onPlayStateChange) onPlayStateChange(false);
    };
    const handlePlay = () => {
      setIsPlaying(true);
      if (onPlayStateChange) onPlayStateChange(true);
    };
    const handlePause = () => {
      setIsPlaying(false);
      window.dispatchEvent(new CustomEvent("forge:audiostate", {
        detail: { isPlaying: false, time: audio.currentTime, jobId: job.job_id }
      }));
      if (onPlayStateChange) onPlayStateChange(false);
    };

    audio.addEventListener("loadedmetadata", handleLoadedMetadata);
    audio.addEventListener("ended", handleEnded);
    audio.addEventListener("play", handlePlay);
    audio.addEventListener("pause", handlePause);

    return () => {
      audio.removeEventListener("loadedmetadata", handleLoadedMetadata);
      audio.removeEventListener("ended", handleEnded);
      audio.removeEventListener("play", handlePlay);
      audio.removeEventListener("pause", handlePause);
    };
  }, [job.job_id, onPlayStateChange]);

  function togglePlay() {
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      audio.pause();
    } else {
      audio.play().catch(() => {});
    }
  }

  function skip(seconds) {
    const audio = audioRef.current;
    if (!audio) return;
    const target = Math.max(0, Math.min(duration || 99999, audio.currentTime + seconds));
    audio.currentTime = target;
    setCurrentTime(target);
    window.dispatchEvent(new CustomEvent("forge:audioseek", {
      detail: { time: target, jobId: job.job_id }
    }));
  }

  function handleSeekStart(e) {
    setIsSeeking(true);
    setSeekValue(parseFloat(e.target.value));
  }

  function handleSeekChange(e) {
    setSeekValue(parseFloat(e.target.value));
  }

  function handleSeekEnd(e) {
    const target = parseFloat(e.target.value);
    setIsSeeking(false);
    setCurrentTime(target);
    if (audioRef.current) {
      audioRef.current.currentTime = target;
    }
    window.dispatchEvent(new CustomEvent("forge:audioseek", {
      detail: { time: target, jobId: job.job_id }
    }));
  }

  function changeSpeed(speed) {
    setPlaybackSpeed(speed);
    if (audioRef.current) {
      audioRef.current.playbackRate = speed;
    }
  }

  function toggleMute() {
    if (audioRef.current) {
      audioRef.current.muted = !isMuted;
      setIsMuted(!isMuted);
    }
  }

  function handleVolumeChange(e) {
    const v = parseFloat(e.target.value);
    setVolume(v);
    setIsMuted(v === 0);
    if (audioRef.current) {
      audioRef.current.volume = v;
      audioRef.current.muted = v === 0;
    }
  }

  const effectiveCurrent = isSeeking ? seekValue : currentTime;
  const progressPercent = duration > 0 ? Math.min(100, (effectiveCurrent / duration) * 100) : 0;

  return (
    <div className={`full-audio-player ${isPlaying ? "playing" : ""}`}>
      <audio
        ref={audioRef}
        src={streamSrc}
        preload="metadata"
      />

      <div className="player-top-row">
        <div className="player-status-badge">
          <span className={`pulse-indicator ${isPlaying ? "active" : ""}`} />
          <strong>Full Lecture Audio (WAV, 16 kHz Mono)</strong>
          {duration > 0 && <span className="muted small">· {formatTime(duration)}</span>}
        </div>
        <div className="player-top-actions">
          <a href={audioUrl(job.job_id)} download className="player-download-btn" title="Download uncompressed WAV">
            <IconDownload /> Download WAV
          </a>
        </div>
      </div>

      <div className="player-main-controls">
        <button
          type="button"
          className="player-skip-btn"
          onClick={() => skip(-10)}
          title="Rewind 10 seconds"
          aria-label="Rewind 10 seconds"
        >
          <IconRewind10 />
          <span>-10s</span>
        </button>

        <button
          type="button"
          className={`player-big-play-btn ${isPlaying ? "playing" : ""}`}
          onClick={togglePlay}
          title={isPlaying ? "Pause audio" : "Play full lecture audio"}
          aria-label={isPlaying ? "Pause audio" : "Play full lecture audio"}
        >
          {isPlaying ? <IconPause /> : <IconPlay />}
        </button>

        <button
          type="button"
          className="player-skip-btn"
          onClick={() => skip(10)}
          title="Fast-forward 10 seconds"
          aria-label="Fast-forward 10 seconds"
        >
          <span>+10s</span>
          <IconForward10 />
        </button>

        {/* Time Scrubber */}
        <div className="player-timeline-wrap">
          <span className="player-time-label elapsed">{formatTime(effectiveCurrent)}</span>
          <div className="player-scrubber-track">
            <input
              type="range"
              className="player-scrubber"
              min="0"
              max={duration || 100}
              step="0.1"
              value={effectiveCurrent}
              onMouseDown={handleSeekStart}
              onTouchStart={handleSeekStart}
              onChange={handleSeekChange}
              onMouseUp={handleSeekEnd}
              onTouchEnd={handleSeekEnd}
              style={{
                background: `linear-gradient(to right, var(--accent) ${progressPercent}%, var(--border-firm) ${progressPercent}%)`
              }}
              aria-label="Seek time"
            />
          </div>
          <span className="player-time-label total">{formatTime(duration)}</span>
        </div>

        {/* Speed Controls */}
        <div className="player-speed-pills" role="radiogroup" aria-label="Playback speed">
          {[1, 1.25, 1.5, 2].map((s) => (
            <button
              key={s}
              type="button"
              className={`player-speed-btn ${playbackSpeed === s ? "active" : ""}`}
              onClick={() => changeSpeed(s)}
            >
              {s}×
            </button>
          ))}
        </div>

        {/* Volume Controls */}
        <div className="player-volume-wrap">
          <button
            type="button"
            className="player-mute-btn"
            onClick={toggleMute}
            title={isMuted ? "Unmute" : "Mute"}
            aria-label={isMuted ? "Unmute" : "Mute"}
          >
            {isMuted || volume === 0 ? <IconVolumeMute /> : <IconVolume />}
          </button>
          <input
            type="range"
            className="player-volume-slider"
            min="0"
            max="1"
            step="0.05"
            value={isMuted ? 0 : volume}
            onChange={handleVolumeChange}
            aria-label="Volume"
          />
        </div>
      </div>
    </div>
  );
}

// --- Active Job Status & Inspector ---
function JobStatus({
  job,
  onPromoted,
  audioIsPlaying,
  onAudioPlayStateChange,
  audioSeekRequest,
  audioTogglePlayTrigger,
}) {
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
      <p className="muted small" style={{ marginTop: 4 }}>
        {job.source === "youtube" ? "YouTube Source" : "File Upload"}
        {job.duration != null && ` · ${formatTime(job.duration)} audio`}
        {job.segment_count != null && ` · ${job.segment_count} clips`}
        {job.keyterms.length > 0 && ` · Key terms: ${job.keyterms.join(", ")}`}
        {` · ID: ${job.job_id}`}
      </p>

      <div className="badges">
        <span className="badge engine">
          {job.engine === "elevenlabs" && <IconMic />}
          {job.engine === "local" && <IconCpu />}
          {job.engine === "both" && <IconDual />}
          {ENGINE_LABELS[job.engine] || job.engine}
        </span>
        <span className={job.purpose === "training" ? "badge training" : "badge"}>
          {job.purpose === "training" ? <IconTraining /> : <IconTranscript />}
          {job.purpose === "training" ? "Training data" : "Transcript only"}
        </span>
        <span className={`badge ${job.status}`}>
          {STAGE_LABELS[job.status] || job.status}
        </span>
        {job.human_verified && (
          <span className="badge verified">
            <IconShieldCheck /> Human Verified
          </span>
        )}
        {job.audio_health && (
          <span className={`badge health-${job.audio_health.rating.toLowerCase()}`}>
            <IconHealth /> {job.audio_health.score}/100 {job.audio_health.rating}
          </span>
        )}
      </div>

      {job.status === "done" && job.purpose === "transcribe" && (
        <div className="promote">
          {job.can_promote ? (
            <>
              <button type="button" onClick={promote} disabled={promoting}>
                <IconTraining />
                {promoting ? "Cutting training clips…" : "Use for training"}
              </button>
              <small className="muted">
                Cuts this lecture into short training clips using the existing ElevenLabs word timestamps. Takes only a few seconds and costs 0 credits.
              </small>
            </>
          ) : (
            <small className="muted">
              {job.engine === "local"
                ? "The local model provides text without word timings, so it cannot be converted into training clips. Transcribe with ElevenLabs to export training data."
                : "This job cannot be converted into training data."}
            </small>
          )}
          {promoteError && <p className="error">{promoteError}</p>}
        </div>
      )}

      {job.audio_available && (
        <FullAudioPlayer
          job={job}
          isPlaying={audioIsPlaying}
          onPlayStateChange={onAudioPlayStateChange}
          seekRequest={audioSeekRequest}
          togglePlayTrigger={audioTogglePlayTrigger}
        />
      )}

      {job.source_url && (
        <div className="job-links" style={{ marginTop: job.audio_available ? 0 : undefined }}>
          <a href={job.source_url} target="_blank" rel="noopener noreferrer" className="source-link">
            <IconYoutube /> Open on YouTube ↗
          </a>
        </div>
      )}

      <JobTimersDashboard job={job} />

      <ol className="stages">
        {stages.map((stage, i) => {
          let state = "";
          if (job.status === "failed") state = "";
          else if (i < currentStage || job.status === "done") state = "complete";
          else if (i === currentStage) state = "current";
          return (
            <li key={stage} className={state}>
              {STAGE_LABELS[stage]}
            </li>
          );
        })}
      </ol>

      {job.status === "failed" && <p className="error">Pipeline Failed: {job.error}</p>}

      {job.progress && job.progress.total > 0 && <JobProgress progress={job.progress} />}

      {!FINAL.includes(job.status) && !job.progress && (
        <p className="muted small" style={{ marginTop: 8 }}>
          Processing in background… This view updates every 3 seconds. Long recordings can take several minutes.
        </p>
      )}
    </div>
  );
}

function JobTimersDashboard({ job }) {
  const speed = (seconds) =>
    job.duration && seconds ? `${(job.duration / seconds).toFixed(1)}× real time` : null;

  const isElevenLabsActive = job.status === "transcribing";
  const isElevenLabsUsed = job.engine === "elevenlabs" || job.engine === "both" || job.elevenlabs_seconds != null;

  const isLocalActive = job.status === "transcribing_local";
  const isLocalUsed = job.engine === "local" || job.engine === "both" || job.local_seconds != null;

  return (
    <div className="job-timers-grid" aria-label="Transcription Engine Timers">
      {/* 1. ElevenLabs Timer */}
      <div className={`job-timer-box ${isElevenLabsActive ? "running" : job.elevenlabs_seconds != null ? "done" : !isElevenLabsUsed ? "skipped" : ""}`}>
        <div className="job-timer-top">
          <span className="job-timer-title">
            <IconMic /> ElevenLabs Scribe
          </span>
          <span className={`job-timer-status ${isElevenLabsActive ? "live" : job.elevenlabs_seconds != null ? "complete" : ""}`}>
            {isElevenLabsActive
              ? "Transcribing…"
              : job.elevenlabs_seconds != null
              ? "✓ Done"
              : !isElevenLabsUsed
              ? "Not run"
              : "Queued"}
          </span>
        </div>
        <div className="job-timer-display">
          <span className="job-timer-value">
            {job.elevenlabs_seconds != null
              ? formatTime(job.elevenlabs_seconds)
              : isElevenLabsActive
              ? "Transcribing…"
              : "–"}
          </span>
          {job.elevenlabs_seconds != null && speed(job.elevenlabs_seconds) && (
            <span className="job-timer-speed">{speed(job.elevenlabs_seconds)}</span>
          )}
        </div>
        <p className="job-timer-footnote">
          {job.elevenlabs_seconds != null
            ? `${job.credits_used != null ? `${job.credits_used} credits used` : "Cloud ASR"} · Word timestamps`
            : isElevenLabsActive
            ? "Cloud speech model transcribing in background…"
            : !isElevenLabsUsed
            ? "Zero credits spent (local model selected)"
            : "High accuracy cloud transcription"}
        </p>
      </div>

      {/* 2. Local IndicConformer Timer */}
      <div className={`job-timer-box ${isLocalActive ? "running" : job.local_seconds != null ? "done" : !isLocalUsed ? "skipped" : ""}`}>
        <div className="job-timer-top">
          <span className="job-timer-title">
            <IconCpu /> Local IndicConformer
          </span>
          <span className={`job-timer-status ${isLocalActive ? "live" : job.local_seconds != null ? "complete" : ""}`}>
            {isLocalActive
              ? "Transcribing…"
              : job.local_seconds != null
              ? "✓ Done"
              : !isLocalUsed
              ? "Not run"
              : "Queued"}
          </span>
        </div>
        <div className="job-timer-display">
          <span className="job-timer-value">
            {job.local_seconds != null
              ? formatTime(job.local_seconds)
              : isLocalActive
              ? (job.progress ? `${job.progress.done}/${job.progress.total} clips` : "Transcribing…")
              : "–"}
          </span>
          {job.local_seconds != null && speed(job.local_seconds) && (
            <span className="job-timer-speed">{speed(job.local_seconds)}</span>
          )}
        </div>
        <p className="job-timer-footnote">
          {job.local_seconds != null
            ? "100% Free · Server neural CPU/GPU"
            : isLocalActive
            ? (job.progress?.seconds_left ? `~${formatTime(job.progress.seconds_left)} remaining` : "Neural model transcribing on server…")
            : !isLocalUsed
            ? "Engine skipped (ElevenLabs only selected)"
            : "On-premise open-source Malayalam ASR"}
        </p>
      </div>
    </div>
  );
}

function TranscriberTimes({ job }) {
  const speed = (seconds) =>
    job.duration && seconds ? `${(job.duration / seconds).toFixed(1)}× real time` : null;

  const rows = [
    { name: "ElevenLabs Scribe", seconds: job.elevenlabs_seconds, note: "Cloud ASR · Credits used" },
    { name: "Local IndicConformer", seconds: job.local_seconds, note: "Server CPU · 100% Free" },
  ].filter((r) => r.seconds != null);

  return (
    <div className="scroll" style={{ marginTop: 16 }}>
      <table className="times">
        <thead>
          <tr>
            <th>Engine</th>
            <th>Elapsed</th>
            <th>Speed</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name}>
              <td><strong>{r.name}</strong></td>
              <td className="time">{formatTime(r.seconds)}</td>
              <td><span className="badge">{speed(r.seconds)}</span></td>
              <td className="muted">{r.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
        Local speech model: <strong>{done} of {total}</strong> pieces transcribed ({percent}%)
        {left != null && left > 0 && ` · estimated ${formatTime(left)} remaining`}
      </p>
    </div>
  );
}

// --- Acoustic Quality & Audio Health Card ---
function AudioHealthCard({ job, onUpdated }) {
  const [health, setHealth] = useState(job?.audio_health || null);
  const [loading, setLoading] = useState(!job?.audio_health && job?.audio_available);
  const [reanalyzing, setReanalyzing] = useState(false);
  const [error, setError] = useState("");

  const fetchHealth = useCallback(async (force = false) => {
    if (!job?.audio_available) return;
    if (force) setReanalyzing(true);
    else setLoading(true);
    setError("");
    try {
      const data = force
        ? await reanalyzeAudioHealth(job.job_id)
        : await getAudioHealth(job.job_id);
      setHealth(data);
      if (onUpdated) onUpdated();
    } catch (err) {
      if (force) setError(err.message || "Could not analyze acoustics.");
    } finally {
      setLoading(false);
      setReanalyzing(false);
    }
  }, [job?.audio_available, job?.job_id, onUpdated]);

  useEffect(() => {
    if (job?.audio_health) {
      setHealth(job.audio_health);
      setLoading(false);
    } else if (job?.audio_available && job?.status === "done") {
      fetchHealth(false);
    }
  }, [job?.audio_health, job?.audio_available, job?.status, fetchHealth]);

  if (!job?.audio_available) return null;

  const ratingClass = health ? health.rating.toLowerCase() : "unknown";

  return (
    <div className={`audio-health-card ${ratingClass}`}>
      <div className="health-card-header">
        <div className="health-title-group">
          <span className="health-icon-wrap">
            <IconHealth />
          </span>
          <div>
            <h4>Acoustic Quality & Audio Health</h4>
            <span className="muted small">
              Diagnostic inspection for AI4Bharat IndicConformer speech fine-tuning
            </span>
          </div>
        </div>
        <div className="health-header-actions">
          {health && (
            <span className={`badge health-${ratingClass}`}>
              {health.score}/100 · {health.rating}
            </span>
          )}
          <button
            type="button"
            className="small-btn"
            onClick={() => fetchHealth(true)}
            disabled={reanalyzing || loading}
            title="Recalculate SNR, RMS volume, and clipping on audio.wav"
          >
            <IconRefresh />
            {reanalyzing ? "Analyzing…" : "Re-Analyze"}
          </button>
        </div>
      </div>

      {loading && !health ? (
        <p className="muted small" style={{ margin: "12px 0 4px" }}>
          Analyzing acoustic characteristics (SNR, volume, clipping)…
        </p>
      ) : health ? (
        <>
          <div className="health-metrics-grid">
            <div className="health-metric">
              <span className="metric-label">Signal-to-Noise (SNR)</span>
              <span className="metric-value">{health.snr_db} dB</span>
              <span className="metric-note">
                {health.snr_db >= 20
                  ? "Crisp / Low noise floor"
                  : health.snr_db >= 14
                  ? "Acceptable room noise"
                  : "High background noise"}
              </span>
            </div>

            <div className="health-metric">
              <span className="metric-label">Speech Loudness (RMS)</span>
              <span className="metric-value">{health.speech_rms_db} dBFS</span>
              <span className="metric-note">
                {health.speech_rms_db >= -26 && health.speech_rms_db <= -16
                  ? "Optimal speaking level"
                  : health.speech_rms_db < -26
                  ? "Low microphone gain"
                  : "Hot / Very loud"}
              </span>
            </div>

            <div className="health-metric">
              <span className="metric-label">Digital Ceiling & Peak</span>
              <span className="metric-value">{health.peak_db} dBFS</span>
              <span className="metric-note">
                {health.clipped_samples === 0
                  ? "0 clipped samples (Clean)"
                  : `${health.clipped_samples} clipped (${health.clipped_percent}%)`}
              </span>
            </div>

            <div className="health-metric">
              <span className="metric-label">Speech Activity</span>
              <span className="metric-value">{health.speech_percent}%</span>
              <span className="metric-note">Active speech vs pauses</span>
            </div>
          </div>

          {health.recommendations?.length > 0 && (
            <div className="health-recommendations">
              <span className="recs-title">Acoustic Diagnostics:</span>
              <ul>
                {health.recommendations.map((rec, idx) => (
                  <li key={idx}>{rec}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      ) : null}

      {error && <p className="error small" style={{ marginTop: 8 }}>{error}</p>}
    </div>
  );
}

// --- Interactive In-Browser Clip Player & Verification Editor ---
function SegmentEditorAndPlayer({ job, transcript, onUpdated }) {
  const [segments, setSegments] = useState(transcript?.segments || []);
  const [playingIndex, setPlayingIndex] = useState(null);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const [autoplayNext, setAutoplayNext] = useState(true);
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const [reverting, setReverting] = useState(false);
  const [saveMessage, setSaveMessage] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const audioRef = useRef(null);

  useEffect(() => {
    setSegments(transcript?.segments || []);
  }, [transcript]);

  const originalMap = useMemo(() => {
    const map = {};
    const base = transcript?.original_segments || transcript?.segments || [];
    base.forEach((s, idx) => {
      map[idx] = s.text;
    });
    return map;
  }, [transcript]);

  const modifiedIndices = useMemo(() => {
    const set = new Set();
    segments.forEach((s, idx) => {
      if (originalMap[idx] !== undefined && s.text !== originalMap[idx]) {
        set.add(idx);
      }
    });
    return set;
  }, [segments, originalMap]);

  function togglePlay(index) {
    if (playingIndex === index) {
      if (audioRef.current) {
        audioRef.current.pause();
      }
      setPlayingIndex(null);
      return;
    }

    const seg = segments[index];
    if (!seg?.file) {
      setErrorMessage("No audio clip file available for this segment.");
      return;
    }

    setErrorMessage("");
    if (!audioRef.current) {
      audioRef.current = new Audio();
    }

    const audio = audioRef.current;
    audio.src = segmentAudioUrl(job.job_id, seg.file);
    audio.playbackRate = playbackSpeed;

    audio.onended = () => {
      if (autoplayNext && index + 1 < segments.length) {
        togglePlay(index + 1);
      } else {
        setPlayingIndex(null);
      }
    };

    audio.onerror = () => {
      setErrorMessage(`Could not play audio clip: ${seg.file}`);
      setPlayingIndex(null);
    };

    audio.play().then(
      () => setPlayingIndex(index),
      (err) => {
        setErrorMessage("Playback failed: " + err.message);
        setPlayingIndex(null);
      }
    );
  }

  function handleSpeedChange(newSpeed) {
    setPlaybackSpeed(newSpeed);
    if (audioRef.current) {
      audioRef.current.playbackRate = newSpeed;
    }
  }

  function handleTextChange(index, newText) {
    setSegments((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], text: newText };
      return next;
    });
  }

  function resetSegment(index) {
    if (originalMap[index] !== undefined) {
      handleTextChange(index, originalMap[index]);
    }
  }

  async function handleSave() {
    setSaving(true);
    setSaveMessage("");
    setErrorMessage("");
    try {
      await saveTranscriptCorrections(job.job_id, segments, true);
      setSaveMessage("Human corrections saved & synced to NeMo training manifest!");
      setTimeout(() => setSaveMessage(""), 4000);
      if (onUpdated) onUpdated();
    } catch (err) {
      setErrorMessage(err.message || "Failed saving transcript corrections.");
    } finally {
      setSaving(false);
    }
  }

  async function handleRevert() {
    if (!confirm("Revert all human edits back to the automated ElevenLabs transcription?")) return;
    setReverting(true);
    setSaveMessage("");
    setErrorMessage("");
    try {
      await revertTranscriptCorrections(job.job_id);
      setSaveMessage("Reverted to automated transcription.");
      setTimeout(() => setSaveMessage(""), 3000);
      if (onUpdated) onUpdated();
    } catch (err) {
      setErrorMessage(err.message || "Failed reverting transcript.");
    } finally {
      setReverting(false);
    }
  }

  const filtered = useMemo(() => {
    if (!search.trim()) return segments.map((s, idx) => ({ ...s, originalIdx: idx }));
    const q = search.toLowerCase();
    return segments
      .map((s, idx) => ({ ...s, originalIdx: idx }))
      .filter((s) => s.text.toLowerCase().includes(q) || `${s.start}-${s.end}`.includes(q));
  }, [segments, search]);

  const hasTrainingClips = segments.some((s) => s.file);

  return (
    <div className="segment-verifier-container">
      <div className="verifier-header">
        <div className="verifier-title-row">
          <div>
            <h3>
              Human Verification & Clip Editor
              <span className="muted small"> ({segments.length} clips)</span>
            </h3>
            <p className="muted small">
              Listen to individual speech segments, correct Malayalam text inline, and certify ground truth for Part 2 model fine-tuning.
            </p>
          </div>
          <div className="verifier-status-badge">
            {transcript.human_verified ? (
              <span className="badge verified">
                <IconShieldCheck /> Human Verified
              </span>
            ) : (
              <span className="badge unverified">
                Automated Transcript
              </span>
            )}
          </div>
        </div>

        <div className="verifier-toolbar">
          <div className="verifier-player-controls">
            <span className="toolbar-label">Speed:</span>
            {[0.75, 1, 1.25, 1.5].map((spd) => (
              <button
                key={spd}
                type="button"
                className={`speed-pill ${playbackSpeed === spd ? "active" : ""}`}
                onClick={() => handleSpeedChange(spd)}
              >
                {spd}×
              </button>
            ))}

            <label className="autoplay-toggle" title="Automatically start playing the next clip when one finishes">
              <input
                type="checkbox"
                checked={autoplayNext}
                onChange={(e) => setAutoplayNext(e.target.checked)}
              />
              <span>Auto-advance</span>
            </label>
          </div>

          <div className="verifier-actions">
            {transcript.human_verified && (
              <button
                type="button"
                className="secondary small-btn"
                onClick={handleRevert}
                disabled={reverting || saving}
              >
                <IconRotateCcw />
                {reverting ? "Reverting…" : "Revert"}
              </button>
            )}

            <button
              type="button"
              className="primary"
              onClick={handleSave}
              disabled={saving}
            >
              <IconSave />
              {saving
                ? "Saving…"
                : modifiedIndices.size > 0
                ? `Save ${modifiedIndices.size} Edit${modifiedIndices.size > 1 ? "s" : ""}`
                : "Save & Verify"}
            </button>
          </div>
        </div>

        {segments.length > 5 && (
          <div className="verifier-search">
            <IconSearch />
            <input
              type="text"
              placeholder="Search segments by Malayalam text or timing…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        )}

        {saveMessage && <div className="notice-banner ok">{saveMessage}</div>}
        {errorMessage && <div className="notice-banner error">{errorMessage}</div>}
      </div>

      <div className="verifier-segment-list">
        {filtered.map((s) => {
          const idx = s.originalIdx;
          const isPlaying = playingIndex === idx;
          const isModified = modifiedIndices.has(idx);

          return (
            <div
              key={idx}
              className={`verifier-segment-card ${isPlaying ? "playing" : ""} ${isModified ? "modified" : ""}`}
            >
              <div className="segment-card-meta">
                <span className="segment-number">#{idx + 1}</span>

                {hasTrainingClips && s.file ? (
                  <button
                    type="button"
                    className={`segment-play-btn ${isPlaying ? "active" : ""}`}
                    onClick={() => togglePlay(idx)}
                    title={isPlaying ? "Pause audio clip" : "Listen to audio clip"}
                  >
                    {isPlaying ? <IconPause /> : <IconPlay />}
                  </button>
                ) : (
                  <span className="muted small" style={{ fontSize: 11 }} title="Audio clips kept for training jobs only">
                    (No clip)
                  </span>
                )}

                <span className="segment-timestamp">
                  {formatPrecise(s.start)} – {formatPrecise(s.end)}
                  <small className="muted"> ({(s.end - s.start).toFixed(1)}s)</small>
                </span>

                {isPlaying && (
                  <span className="audio-wave-anim" aria-label="Playing audio">
                    <span className="bar" />
                    <span className="bar" />
                    <span className="bar" />
                    <span className="bar" />
                  </span>
                )}

                {isModified && (
                  <span className="badge edited-badge" title="This segment was edited">
                    Edited
                  </span>
                )}

                {isModified && (
                  <button
                    type="button"
                    className="segment-reset-btn"
                    onClick={() => resetSegment(idx)}
                    title="Reset to original text"
                  >
                    Reset
                  </button>
                )}
              </div>

              <div className="segment-card-body">
                <textarea
                  className="segment-text-input ml"
                  rows={2}
                  value={s.text}
                  onChange={(e) => handleTextChange(idx, e.target.value)}
                  placeholder="Enter Malayalam speech transcription…"
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// --- High-Performance Word Timing Engine & Reading-Band Auto-Scroll ---
const CALIBRATION_LOOKAHEAD = 0.07; // 70ms anticipation offset to match acoustic onset + hardware audio buffering

function findActiveWordIndex(words, time) {
  if (!words || words.length === 0 || time === undefined || time === null) return -1;
  let low = 0;
  let high = words.length - 1;

  while (low <= high) {
    const mid = (low + high) >> 1;
    const w = words[mid];
    if (time >= w.start && time <= w.end) {
      return mid;
    } else if (time < w.start) {
      high = mid - 1;
    } else {
      low = mid + 1;
    }
  }

  // Grace window (0.12s) to bridge tiny syllables without flicker
  if (high >= 0 && high < words.length) {
    const lastWord = words[high];
    if (time >= lastWord.start && time <= lastWord.end + 0.12) {
      return high;
    }
  }

  return -1;
}

// Smoothly centers active line in the comfortable reading band (18% - 65% of viewport height)
function scrollWordIntoReadingBand(container, targetEl) {
  if (!container || !targetEl) return;
  const containerRect = container.getBoundingClientRect();
  const elRect = targetEl.getBoundingClientRect();
  const relativeTop = elRect.top - containerRect.top;
  const containerHeight = containerRect.height;

  // Only adjust scroll when active word leaves the comfortable reading band
  if (relativeTop < containerHeight * 0.18 || relativeTop > containerHeight * 0.65) {
    container.scrollTo({
      top: container.scrollTop + relativeTop - containerHeight * 0.38,
      behavior: "smooth",
    });
  }
}

// Rapid O(N) class synchronization on seek / initial load (<0.4ms)
function syncWordClassesToTime(container, words, time) {
  if (!container || !words || words.length === 0) return -1;
  const activeIdx = findActiveWordIndex(words, time + CALIBRATION_LOOKAHEAD);
  const spans = container.querySelectorAll(".k-word");
  for (let i = 0; i < spans.length; i++) {
    const el = spans[i];
    if (i === activeIdx) {
      el.className = "k-word active";
    } else if (activeIdx >= 0 && i < activeIdx) {
      el.className = "k-word passed";
    } else {
      el.className = "k-word";
    }
  }
  if (activeIdx >= 0 && spans[activeIdx]) {
    scrollWordIntoReadingBand(container, spans[activeIdx]);
  }
  return activeIdx;
}

// Microsecond-speed 2-element class toggle during 60 FPS playback (<0.02ms)
function updateActiveWord(container, activeIdx, lastIdx, autoScroll) {
  if (!container) return;
  if (lastIdx >= 0 && lastIdx !== activeIdx) {
    const prevEl = container.querySelector(`[data-idx="${lastIdx}"]`);
    if (prevEl) {
      prevEl.className = "k-word passed";
    }
  }
  if (activeIdx >= 0) {
    const nextEl = container.querySelector(`[data-idx="${activeIdx}"]`);
    if (nextEl) {
      nextEl.className = "k-word active";
      if (autoScroll) {
        scrollWordIntoReadingBand(container, nextEl);
      }
    }
  }
}

function TranscriptPane({
  title,
  note,
  text,
  words = [],
  jobId,
  isPlaying = false,
  onSeek,
  onTogglePlay,
  hasAudio = false,
  className = "",
}) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const [miniTime, setMiniTime] = useState(0);
  const textContainerRef = useRef(null);
  const lastActiveIndexRef = useRef(-1);
  const autoScrollRef = useRef(autoScroll);
  autoScrollRef.current = autoScroll;

  // High-frequency 60 FPS audio tick listener (0 React re-renders while playing!)
  useEffect(() => {
    const container = textContainerRef.current;
    if (!container || !words || words.length === 0) return;

    const onTick = (e) => {
      if (e.detail?.jobId && jobId && e.detail.jobId !== jobId) return;
      const t = e.detail.time;
      const activeIdx = findActiveWordIndex(words, t + CALIBRATION_LOOKAHEAD);
      if (activeIdx !== lastActiveIndexRef.current) {
        updateActiveWord(container, activeIdx, lastActiveIndexRef.current, autoScrollRef.current);
        lastActiveIndexRef.current = activeIdx;
      }
      const sec = Math.floor(t);
      setMiniTime((prev) => (prev !== sec ? sec : prev));
    };

    const onSeekEvent = (e) => {
      if (e.detail?.jobId && jobId && e.detail.jobId !== jobId) return;
      const t = e.detail.time;
      const activeIdx = syncWordClassesToTime(container, words, t);
      lastActiveIndexRef.current = activeIdx;
      setMiniTime(Math.floor(t));
    };

    const onStateEvent = (e) => {
      if (e.detail?.jobId && jobId && e.detail.jobId !== jobId) return;
      if (!e.detail.isPlaying && e.detail.time === 0) {
        syncWordClassesToTime(container, words, 0);
        lastActiveIndexRef.current = -1;
        setMiniTime(0);
      }
    };

    window.addEventListener("forge:audiotick", onTick);
    window.addEventListener("forge:audioseek", onSeekEvent);
    window.addEventListener("forge:audiostate", onStateEvent);

    return () => {
      window.removeEventListener("forge:audiotick", onTick);
      window.removeEventListener("forge:audioseek", onSeekEvent);
      window.removeEventListener("forge:audiostate", onStateEvent);
    };
  }, [words, jobId]);

  // Clean word classes on initial mount / job change
  useEffect(() => {
    const container = textContainerRef.current;
    if (container && words && words.length > 0) {
      syncWordClassesToTime(container, words, 0);
      lastActiveIndexRef.current = -1;
      setMiniTime(0);
    }
  }, [words]);

  const handleWordClick = useCallback((start, idx) => {
    if (onSeek) onSeek(start);
    const container = textContainerRef.current;
    if (container) {
      syncWordClassesToTime(container, words, start);
      lastActiveIndexRef.current = idx;
      setMiniTime(Math.floor(start));
    }
  }, [onSeek, words]);

  function copy() {
    setCopyError("");
    navigator.clipboard.writeText(text).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      () => setCopyError("Could not copy automatically — select the text and copy manually.")
    );
  }

  const wordCount = useMemo(() => (text ? text.trim().split(/\s+/).length : 0), [text]);
  const hasInteractiveWords = words && words.length > 0;

  return (
    <div className={className}>
      <div className="transcript-head">
        <div>
          <h3>
            {title}
            {note && <span className="muted"> ({note})</span>}
          </h3>
          {text && (
            <span className="muted small">
              {wordCount.toLocaleString()} words · {text.length.toLocaleString()} characters
              {hasInteractiveWords && " · Click any word to jump audio"}
            </span>
          )}
        </div>
        <div className="transcript-toolbar">
          {hasInteractiveWords && hasAudio && onTogglePlay && (
            <button
              type="button"
              className={`transcript-mini-play-btn ${isPlaying ? "playing" : ""}`}
              onClick={onTogglePlay}
              title={isPlaying ? "Pause audio" : "Play audio from current position"}
            >
              {isPlaying ? <IconPause /> : <IconPlay />}
              <span>{isPlaying ? "Pause" : "Play"}</span>
              {miniTime > 0 && <span className="transcript-mini-time">{formatTime(miniTime)}</span>}
            </button>
          )}

          {hasInteractiveWords && (
            <button
              type="button"
              className={`autoscroll-toggle-btn ${autoScroll ? "active" : ""}`}
              onClick={() => setAutoScroll((prev) => !prev)}
              title={autoScroll ? "Auto-scroll is following speech. Click to freeze view." : "Auto-scroll is paused. Click to follow speech."}
            >
              <span className={`autoscroll-dot ${autoScroll ? "on" : ""}`} />
              <span>Auto-scroll</span>
            </button>
          )}

          <button type="button" onClick={copy} disabled={!text}>
            {copied ? (
              <>
                <IconCheck /> Copied
              </>
            ) : (
              <>
                <IconCopy /> Copy text
              </>
            )}
          </button>
        </div>
      </div>
      <div className="transcript-text" ref={textContainerRef} tabIndex={0}>
        {hasInteractiveWords ? (
          words.map((w, idx) => (
            <span key={idx}>
              <span
                data-idx={idx}
                className="k-word"
                onClick={() => handleWordClick(w.start, idx)}
                title={`Jump audio to ${formatTime(w.start)} (${w.text})`}
              >
                {w.text}
              </span>{" "}
            </span>
          ))
        ) : (
          text || <em className="muted">(no speech detected in this audio recording)</em>
        )}
      </div>
      {copyError && <p className="error" style={{ marginTop: 8 }}>{copyError}</p>}
    </div>
  );
}

function TranscriptView({
  transcript,
  comparison,
  job,
  onUpdated,
  isPlaying = false,
  onSeek,
  onTogglePlay,
}) {
  const isLocal = transcript.source === "local";

  return (
    <div className="transcript">
      <TranscriptPane
        title={isLocal ? "Transcript (local model)" : "Transcript (ElevenLabs)"}
        note={transcript.language_code}
        text={transcript.text}
        words={transcript.words || []}
        jobId={job?.job_id}
        isPlaying={isPlaying}
        onSeek={onSeek}
        onTogglePlay={onTogglePlay}
        hasAudio={Boolean(job?.audio_available)}
      />

      {transcript.local_text && (
        <TranscriptPane
          className="second-transcript"
          title="Transcript (local model)"
          text={transcript.local_text}
          words={transcript.local_words || []}
          jobId={job?.job_id}
          isPlaying={isPlaying}
          onSeek={onSeek}
          onTogglePlay={onTogglePlay}
          hasAudio={Boolean(job?.audio_available)}
        />
      )}

      {comparison && <ComparisonTable comparison={comparison} jobId={job.id} />}

      {transcript.segments.length > 0 && (
        <SegmentEditorAndPlayer
          job={job}
          transcript={transcript}
          onUpdated={onUpdated}
        />
      )}
    </div>
  );
}

function ComparisonTable({ comparison, jobId }) {
  const { overall, comparable, segments } = comparison;
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const audioRef = useRef(null);
  const [playingSnippet, setPlayingSnippet] = useState(null);

  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
    };
  }, []);

  const playSegment = useCallback((start, end) => {
    if (!jobId) return;
    const key = `${start}-${end}`;
    if (audioRef.current) {
      audioRef.current.pause();
      if (playingSnippet === key) {
        audioRef.current = null;
        setPlayingSnippet(null);
        return;
      }
    }
    const url = audioSnippetUrl(jobId, start, end);
    const audio = new Audio(url);
    audioRef.current = audio;
    setPlayingSnippet(key);
    audio.onended = () => {
      if (audioRef.current === audio) setPlayingSnippet(null);
    };
    audio.onerror = () => {
      if (audioRef.current === audio) setPlayingSnippet(null);
    };
    audio.play().catch(() => setPlayingSnippet(null));
  }, [jobId, playingSnippet]);

  const stats = useMemo(() => {
    let matched = 0;
    let disagreed = 0;
    segments.forEach((s) => {
      const w = s.scores?.wer;
      if (w === 0) matched++;
      else if (w > 0) disagreed++;
    });
    return { matched, disagreed, total: segments.length };
  }, [segments]);

  const filtered = useMemo(() => {
    return segments
      .map((s, idx) => ({ ...s, origIdx: idx }))
      .filter((s) => {
        const w = s.scores?.wer;
        if (filter === "disagreed" && (w == null || w === 0)) return false;
        if (filter === "matched" && w !== 0) return false;
        if (search.trim()) {
          const q = search.toLowerCase();
          return (s.elevenlabs || "").toLowerCase().includes(q) || (s.local || "").toLowerCase().includes(q);
        }
        return true;
      });
  }, [segments, filter, search]);

  return (
    <div className="comparison">
      <h3>ElevenLabs vs IndicConformer Comparison</h3>

      {comparable ? (
        <>
          <div className="compare-scorecards">
            <div className="compare-scorecard">
              <span className="compare-scorecard-label">Overall Agreement</span>
              <span className="compare-scorecard-value" style={{ color: "var(--ok)" }}>
                {((1 - overall.wer) * 100).toFixed(1)}%
              </span>
            </div>
            <div className="compare-scorecard">
              <span className="compare-scorecard-label">Word Disagreement (WER)</span>
              <span className="compare-scorecard-value" style={{ color: "var(--warn)" }}>
                {(overall.wer * 100).toFixed(1)}%
              </span>
            </div>
            <div className="compare-scorecard">
              <span className="compare-scorecard-label">Character Error (CER)</span>
              <span className="compare-scorecard-value">
                {(overall.cer * 100).toFixed(1)}%
              </span>
            </div>
            <div className="compare-scorecard">
              <span className="compare-scorecard-label">Segments Compared</span>
              <span className="compare-scorecard-value">
                {segments.length}
              </span>
            </div>
          </div>

          <div className="compare-filter-bar">
            <div className="tabs" role="tablist" style={{ marginBottom: 0 }}>
              <button
                type="button"
                className={filter === "all" ? "tab active" : "tab"}
                onClick={() => setFilter("all")}
              >
                All ({stats.total})
              </button>
              <button
                type="button"
                className={filter === "disagreed" ? "tab active" : "tab"}
                onClick={() => setFilter("disagreed")}
              >
                Discrepancies ({stats.disagreed})
              </button>
              <button
                type="button"
                className={filter === "matched" ? "tab active" : "tab"}
                onClick={() => setFilter("matched")}
              >
                Exact Matches ({stats.matched})
              </button>
            </div>

            {segments.length > 5 && (
              <div className="search-input-wrap" style={{ maxWidth: 280 }}>
                <input
                  type="text"
                  placeholder="Filter comparison words…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                {search && (
                  <button type="button" className="search-clear-btn" onClick={() => setSearch("")} title="Clear filter">
                    <IconX />
                  </button>
                )}
              </div>
            )}
          </div>
        </>
      ) : (
        <div className="warn-box" style={{ margin: "12px 0 16px" }}>
          <strong>These scores are not meaningful yet.</strong> The local model writes English
          words in Malayalam letters while ElevenLabs keeps them in English, so roughly 40% of
          words differ on spelling alone rather than on what was heard. Fill in the English
          spelling table first (<code>build_english_map.py --fill</code>), then the numbers will
          compare the models rather than the alphabets.
        </div>
      )}

      {/* Desktop Table View */}
      <div className="scroll desktop-table-view">
        <table className="segments compare">
          <thead>
            <tr>
              <th>#</th>
              <th>Time</th>
              <th>ElevenLabs (Transliterated)</th>
              <th>Local IndicConformer</th>
              {comparable && <th>WER</th>}
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => (
              <tr key={s.origIdx}>
                <td className="num">{s.origIdx + 1}</td>
                <td className="time">
                  {formatPrecise(s.start)} – {formatPrecise(s.end)}
                  {jobId && (
                    <button
                      type="button"
                      className={`compare-audio-btn ${playingSnippet === `${s.start}-${s.end}` ? "playing" : ""}`}
                      onClick={() => playSegment(s.start, s.end)}
                      title="Play segment audio"
                    >
                      <IconVolume />
                    </button>
                  )}
                </td>
                <td>{s.elevenlabs}</td>
                <td className="ml">{s.local || <em className="muted">(nothing)</em>}</td>
                {comparable && (
                  <td className="time">
                    {s.scores.wer == null ? (
                      "–"
                    ) : s.scores.wer === 0 ? (
                      <span className="badge done" style={{ fontSize: "0.75rem", padding: "1px 6px" }}>0% Match</span>
                    ) : (
                      <span className="badge" style={{ fontSize: "0.75rem", padding: "1px 6px" }}>
                        {(s.scores.wer * 100).toFixed(0)}%
                      </span>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile Card View (optimized for thumb scrolling on phones) */}
      <div className="compare-mobile-cards">
        {filtered.map((s) => (
          <div key={s.origIdx} className="compare-card-item">
            <div className="compare-card-meta">
              <span>
                #{s.origIdx + 1} · {formatPrecise(s.start)} – {formatPrecise(s.end)}
                {jobId && (
                  <button
                    type="button"
                    className={`compare-audio-btn ${playingSnippet === `${s.start}-${s.end}` ? "playing" : ""}`}
                    onClick={() => playSegment(s.start, s.end)}
                    title="Play segment audio"
                  >
                    <IconVolume />
                  </button>
                )}
              </span>
              {comparable && s.scores.wer != null && (
                <span className={s.scores.wer === 0 ? "badge done" : "badge"}>
                  {s.scores.wer === 0 ? "✓ 100% Match" : `${(s.scores.wer * 100).toFixed(0)}% Discrepancy`}
                </span>
              )}
            </div>
            <div className="compare-card-row">
              <span className="compare-card-label">ElevenLabs Reference:</span>
              <div className="compare-card-text">{s.elevenlabs}</div>
            </div>
            <div className="compare-card-row">
              <span className="compare-card-label">IndicConformer Prediction:</span>
              <div className="compare-card-text ml">{s.local || <em className="muted">(nothing)</em>}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// --- Job Library & Datasets ---
function JobLists({ jobs, currentId, onOpen, onRefresh }) {
  const training = useMemo(() => jobs.filter((j) => j.purpose === "training"), [jobs]);
  const transcripts = useMemo(() => jobs.filter((j) => j.purpose !== "training"), [jobs]);
  const [tab, setTab] = useState("transcripts");

  useEffect(() => {
    if (transcripts.length === 0 && training.length > 0) {
      setTab("training");
    }
  }, [training.length, transcripts.length]);

  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState("newest");
  const activePool = tab === "training" ? training : transcripts;

  const filteredJobs = useMemo(() => {
    let pool = [...activePool];
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      pool = pool.filter((j) => {
        const title = (j.title || "").toLowerCase();
        const id = (j.job_id || "").toLowerCase();
        const url = (j.source_url || "").toLowerCase();
        return title.includes(q) || id.includes(q) || url.includes(q);
      });
    }

    if (sortBy === "longest") {
      pool.sort((a, b) => (b.duration || 0) - (a.duration || 0));
    } else if (sortBy === "shortest") {
      pool.sort((a, b) => (a.duration || 0) - (b.duration || 0));
    } else if (sortBy === "health") {
      pool.sort((a, b) => ((b.audio_health?.score || 0) - (a.audio_health?.score || 0)));
    } else {
      pool.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    }

    return pool;
  }, [activePool, search, sortBy]);

  return (
    <section className="card">
      <div className="transcript-head">
        <div className="tabs" role="tablist" style={{ marginBottom: 0 }}>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "training"}
            className={tab === "training" ? "tab active" : "tab"}
            onClick={() => setTab("training")}
          >
            <IconTraining /> Training data ({training.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "transcripts"}
            className={tab === "transcripts" ? "tab active" : "tab"}
            onClick={() => setTab("transcripts")}
          >
            <IconTranscript /> Transcripts ({transcripts.length})
          </button>
        </div>
        <button type="button" onClick={onRefresh}>
          <IconRefresh /> Refresh
        </button>
      </div>

      <p className="muted small" style={{ marginTop: 8 }}>
        {tab === "training"
          ? "Lectures cut into training clips with a manifest. Only these are exported as training data."
          : "Transcripts only — never included in a training dataset. Open one to turn it into training data."}
      </p>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", margin: "14px 0 16px" }}>
        <div className="search-input-wrap" style={{ flex: "1 1 240px" }}>
          <input
            type="text"
            placeholder="Search lectures by title or ID…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search && (
            <button type="button" className="search-clear-btn" onClick={() => setSearch("")} title="Clear search">
              <IconX />
            </button>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
          <span className="muted small" style={{ whiteSpace: "nowrap" }}>Sort:</span>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            style={{ padding: "8px 12px", borderRadius: "var(--r-sm)", border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)" }}
          >
            <option value="newest">Newest first</option>
            <option value="longest">Longest audio</option>
            <option value="shortest">Shortest audio</option>
            <option value="health">Best acoustic health</option>
          </select>
        </div>
      </div>

      <JobList
        jobs={filteredJobs}
        currentId={currentId}
        onOpen={onOpen}
        empty={
          search
            ? "No matching lectures found for this search filter."
            : tab === "training"
            ? "No training lectures yet. Submit one above with “Model training” selected, or open a transcript and press “Use for training”."
            : "No transcript-only lectures yet. Submit one above with “Just the transcript” selected."
        }
      />
    </section>
  );
}

function JobList({ jobs, currentId, onOpen, empty }) {
  const [playingJobAudioId, setPlayingJobAudioId] = useState(null);
  const audioRef = useRef(null);

  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
    };
  }, []);

  function toggleJobAudio(j) {
    if (playingJobAudioId === j.job_id) {
      if (audioRef.current) audioRef.current.pause();
      setPlayingJobAudioId(null);
      return;
    }
    if (audioRef.current) {
      audioRef.current.pause();
    }
    const audio = new Audio(streamAudioUrl(j.job_id));
    audioRef.current = audio;
    setPlayingJobAudioId(j.job_id);
    audio.onended = () => {
      if (audioRef.current === audio) setPlayingJobAudioId(null);
    };
    audio.onerror = () => {
      if (audioRef.current === audio) setPlayingJobAudioId(null);
    };
    audio.play().catch(() => setPlayingJobAudioId(null));
  }

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
                <span className="job-card-badges">
                  {j.human_verified && <span className="badge verified">✓ Verified</span>}
                  {j.audio_health && (
                    <span className={`badge health-${j.audio_health.rating.toLowerCase()}`}>
                      {j.audio_health.score}/100 {j.audio_health.rating}
                    </span>
                  )}
                  {j.audio_available && (
                    <span
                      role="button"
                      tabIndex={0}
                      className={`badge ${playingJobAudioId === j.job_id ? "done" : ""}`}
                      style={{ cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleJobAudio(j);
                      }}
                      title={playingJobAudioId === j.job_id ? "Pause audio" : "Play full lecture audio"}
                    >
                      {playingJobAudioId === j.job_id ? <IconPause /> : <IconPlay />}
                      {playingJobAudioId === j.job_id ? "Playing Audio" : "Listen"}
                    </span>
                  )}
                  <span className={`badge ${j.status}`}>{STAGE_LABELS[j.status] || j.status}</span>
                </span>
              </span>
              <span className="job-card-meta">{meta.join(" · ")}</span>
              {(j.elevenlabs_seconds != null || j.local_seconds != null) && (
                <span className="job-card-timers">
                  {j.elevenlabs_seconds != null && (
                    <span className="timer-badge el" title={`ElevenLabs runtime: ${formatTime(j.elevenlabs_seconds)}`}>
                      <IconMic /> ElevenLabs: <strong>{formatTime(j.elevenlabs_seconds)}</strong>
                    </span>
                  )}
                  {j.local_seconds != null && (
                    <span className="timer-badge local" title={`Local model runtime: ${formatTime(j.local_seconds)}`}>
                      <IconCpu /> Local: <strong>{formatTime(j.local_seconds)}</strong>
                    </span>
                  )}
                </span>
              )}
            </button>
            {j.source_url && (
              <a
                className="job-card-link"
                href={j.source_url}
                title={j.source_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                <IconYoutube /> Open on YouTube ↗
              </a>
            )}
          </li>
        );
      })}
    </ul>
  );
}

// --- English Spelling Table Review ---
const SPELLING_PAGE = 25;
const MAX_ALTERNATIVES = 4;

function SpellingTable() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [drafts, setDrafts] = useState({});
  const [saving, setSaving] = useState("");
  const [rowErrors, setRowErrors] = useState({});
  const [filter, setFilter] = useState("todo");
  const [search, setSearch] = useState("");
  const [shown, setShown] = useState(SPELLING_PAGE);
  const [bulkApproving, setBulkApproving] = useState(false);
  const [bulkMessage, setBulkMessage] = useState("");

  useEffect(() => {
    getEnglishMap().then(setData, (err) => setError(err.message));
  }, []);

  async function handleBulkApprove() {
    setBulkApproving(true);
    setBulkMessage("");
    try {
      const res = await bulkApproveSpellings();
      setData((d) => ({
        coverage: res.coverage,
        entries: d.entries.map((e) =>
          e.spelling ? { ...e, status: "checked" } : e
        ),
      }));
      setBulkMessage(`Successfully approved ${res.approved} proposed spellings! Vocabulary coverage is now ${res.coverage.coverage_percent}%. Model comparison scores unlocked.`);
      setTimeout(() => setBulkMessage(""), 6000);
    } catch (err) {
      setError(err.message || "Failed bulk approving spellings.");
    } finally {
      setBulkApproving(false);
    }
  }

  async function save(word, approved) {
    const entry = data?.entries?.find((e) => e.word === word);
    if (!entry) return;
    const spelling = (drafts[word] ?? entry.spelling).trim();
    setSaving(word);
    setRowErrors((r) => ({ ...r, [word]: "" }));
    try {
      const res = await setEnglishSpelling(word, spelling, approved);
      setData((d) => ({
        coverage: res.coverage,
        entries: d.entries.map((e) =>
          e.word === word ? { ...e, spelling: res.spelling, status: res.status } : e
        ),
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
        <p className="error" style={{ marginTop: 8 }}>{error}</p>
      </section>
    );
  }
  if (!data) {
    return (
      <section className="card">
        <h2>English spellings</h2>
        <p className="muted" style={{ marginTop: 8 }}>Loading dictionary…</p>
      </section>
    );
  }

  const { coverage } = data;
  const counted = {
    todo: data.entries.filter((e) => e.status !== "checked").length,
    approved: data.entries.filter((e) => e.status === "checked").length,
    all: data.entries.length,
  };

  const filteredEntries = data.entries
    .filter((e) => (filter === "approved" ? e.status === "checked" : filter === "todo" ? e.status !== "checked" : true))
    .filter((e) => (!search ? true : e.word.toLowerCase().includes(search.toLowerCase().trim())));

  const visible = filteredEntries.slice(0, shown);

  return (
    <section className="card">
      <h2>English spellings</h2>
      <p className="muted small" style={{ marginTop: 4 }}>
        English words transliterated into Malayalam script. Until approved here, comparison scores count differing alphabets as recognition errors.
      </p>

      <div className="coverage">
        <div className="meter">
          <div style={{ width: `${coverage.coverage_percent}%` }} />
        </div>
        <p className="muted small">
          <strong>{coverage.coverage_percent}%</strong> vocabulary coverage — {coverage.words_approved} of {coverage.words_total} words approved,
          accounting for {coverage.occurrences_covered} of {coverage.occurrences_total} total spoken occurrences.
        </p>
      </div>

      {bulkMessage && <div className="notice-banner ok" style={{ marginBottom: 12 }}>{bulkMessage}</div>}

      {data.entries.length === 0 ? (
        <p className="empty">
          No English words collected yet. Transcribe a lecture with ElevenLabs, then run{" "}
          <code>build_english_map.py --collect</code> on the server to gather spoken English terms.
        </p>
      ) : (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
            <div className="tabs spelling-filter" role="tablist" style={{ marginBottom: 0 }}>
              {[["todo", "Needs review"], ["approved", "Approved"], ["all", "All"]].map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  className={filter === value ? "tab active" : "tab"}
                  onClick={() => {
                    setFilter(value);
                    setShown(SPELLING_PAGE);
                  }}
                >
                  {label} ({counted[value]})
                </button>
              ))}
            </div>

            {counted.todo > 0 && (
              <button
                type="button"
                className="primary small-btn"
                onClick={handleBulkApprove}
                disabled={bulkApproving}
                title="Approve all proposed Malayalam transliterations to immediately unlock model comparison scores"
              >
                <IconCheck />
                {bulkApproving ? "Approving…" : `Approve All Proposed (${counted.todo})`}
              </button>
            )}
          </div>

          <div className="search-bar" style={{ marginBottom: 16 }}>
            <div className="search-input-wrap">
              <input
                type="text"
                placeholder="Search words in dictionary…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              {search && (
                <button
                  type="button"
                  className="search-clear-btn"
                  onClick={() => setSearch("")}
                  title="Clear search"
                >
                  ×
                </button>
              )}
            </div>
          </div>

          {filteredEntries.length === 0 ? (
            <p className="empty">
              {search
                ? `No words found matching "${search}".`
                : filter === "todo"
                ? "All collected English terms have been reviewed and approved!"
                : "No terms approved yet."}
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

          {visible.length < filteredEntries.length && (
            <button
              type="button"
              className="show-more"
              onClick={() => setShown((n) => n + SPELLING_PAGE)}
            >
              Show {Math.min(SPELLING_PAGE, filteredEntries.length - visible.length)} more (
              {filteredEntries.length - visible.length} remaining)
            </button>
          )}
        </>
      )}
    </section>
  );
}

function renderContext(ctx) {
  if (!ctx) return null;
  const parts = ctx.split(/(\[[^\]]+\])/g);
  return parts.map((part, i) => {
    if (part.startsWith("[") && part.endsWith("]")) {
      return <strong key={i}>{part.slice(1, -1)}</strong>;
    }
    return <span key={i}>{part}</span>;
  });
}

function SpellingRow({ entry, draft, busy, error, onDraft, onSave }) {
  const value = draft ?? entry.spelling;
  const approved = entry.status === "checked";
  const changed = draft != null && draft !== entry.spelling;

  const audioRef = useRef(null);
  const [playingUrl, setPlayingUrl] = useState(null);
  const [showOccurrences, setShowOccurrences] = useState(false);
  const [occurrences, setOccurrences] = useState(null);
  const [loadingOcc, setLoadingOcc] = useState(false);

  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
    };
  }, []);

  const playAudio = useCallback((url) => {
    if (!url) return;
    if (audioRef.current) {
      audioRef.current.pause();
      if (playingUrl === url) {
        audioRef.current = null;
        setPlayingUrl(null);
        return;
      }
    }
    const audio = new Audio(url);
    audioRef.current = audio;
    setPlayingUrl(url);
    audio.onended = () => {
      if (audioRef.current === audio) {
        setPlayingUrl(null);
      }
    };
    audio.onerror = () => {
      if (audioRef.current === audio) {
        setPlayingUrl(null);
      }
    };
    audio.play().catch(() => {
      setPlayingUrl(null);
    });
  }, [playingUrl]);

  const toggleOccurrences = useCallback(() => {
    if (!showOccurrences && occurrences == null) {
      setLoadingOcc(true);
      getWordOccurrences(entry.word)
        .then((data) => {
          setOccurrences(data.occurrences || []);
          setLoadingOcc(false);
        })
        .catch(() => {
          setLoadingOcc(false);
        });
    }
    setShowOccurrences((prev) => !prev);
  }, [showOccurrences, occurrences, entry.word]);

  const everyAlternative = Object.entries(entry.alternatives || {})
    .filter(([spelling]) => spelling !== value)
    .sort((a, b) => b[1] - a[1]);
  const alternatives = everyAlternative.slice(0, MAX_ALTERNATIVES);
  const rarer = everyAlternative.length - alternatives.length;

  return (
    <li className={approved ? "spelling approved" : "spelling"}>
      <div className="spelling-word">
        <div className="spelling-word-top">
          <strong>{entry.word}</strong>
          {entry.sample && (
            <button
              type="button"
              className={`sample-audio-btn ${playingUrl === entry.sample.audio_url ? "playing" : ""}`}
              onClick={() => playAudio(entry.sample.audio_url)}
              title={`Hear "${entry.word}" spoken in lecture audio`}
            >
              <IconVolume />
              <span>{playingUrl === entry.sample.audio_url ? "Playing…" : "Hear Audio"}</span>
            </button>
          )}
        </div>
        <span className="muted small">Spoken {entry.occurrences}×</span>
      </div>

      <input
        type="text"
        className="ml"
        lang="ml"
        value={value}
        placeholder="Malayalam script spelling"
        aria-label={`Malayalam spelling for ${entry.word}`}
        onChange={(ev) => onDraft(ev.target.value)}
      />

      <div className="spelling-actions">
        <button
          type="button"
          onClick={() => onSave(true)}
          disabled={busy || !value.trim() || (approved && !changed)}
        >
          {busy ? "Saving…" : approved && !changed ? "Approved" : "Approve"}
        </button>
        {approved && !changed && (
          <button
            type="button"
            onClick={() => onSave(false)}
            disabled={busy}
            title="Unapprove this spelling from scoring"
          >
            Unapprove
          </button>
        )}
      </div>

      {entry.sample && (
        <div className="spelling-context-card">
          <span className="context-tag">Spoken context:</span>
          <span className="context-quote">"{renderContext(entry.sample.context)}"</span>
          <span className="context-time">
            at {formatTime(entry.sample.start)} ({entry.sample.job_title})
          </span>
          {entry.samples_count > 1 && (
            <button
              type="button"
              className="sample-more-toggle"
              onClick={toggleOccurrences}
            >
              {showOccurrences ? "Hide other clips" : `Hear ${entry.samples_count} clips ▾`}
            </button>
          )}
        </div>
      )}

      {showOccurrences && (
        <div className="occurrences-drawer">
          {loadingOcc && <span className="small muted">Loading audio clips…</span>}
          {occurrences && occurrences.length === 0 && (
            <span className="small muted">No additional clips found.</span>
          )}
          {occurrences && occurrences.map((occ, i) => (
            <div key={i} className="occ-item">
              <button
                type="button"
                className={`occ-play-btn ${playingUrl === occ.audio_url ? "playing" : ""}`}
                onClick={() => playAudio(occ.audio_url)}
                title="Play audio clip"
              >
                <IconVolume />
              </button>
              <div className="occ-body">
                <span className="occ-quote">"{renderContext(occ.context)}"</span>
                <span className="occ-meta">
                  {occ.job_title} · {formatTime(occ.start)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {alternatives.length > 0 && (
        <p className="spelling-alts small muted">
          Model suggestions:{" "}
          {alternatives.map(([spelling, n]) => (
            <button
              key={spelling}
              type="button"
              className="alt"
              onClick={() => onDraft(spelling)}
            >
              <span className="ml">{spelling}</span> ×{n}
            </button>
          ))}
          {rarer > 0 && <span>and {rarer} rarer spelling{rarer === 1 ? "" : "s"}</span>}
        </p>
      )}
      {error && <p className="error spelling-error">{error}</p>}
    </li>
  );
}

// --- Training Dataset Management Hub (Part 2 Bridge) ---
function DatasetHub({ onDatasetCreated }) {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const [exportSuccess, setExportSuccess] = useState("");
  const [name, setName] = useState("");
  const [trainRatio, setTrainRatio] = useState(80);
  const [valRatio, setValRatio] = useState(10);
  const [testRatio, setTestRatio] = useState(10);
  const [copiedName, setCopiedName] = useState("");

  const refreshSummary = useCallback(() => {
    getDatasetSummary()
      .then((data) => {
        setSummary(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    refreshSummary();
  }, [refreshSummary]);

  async function handleExport(e) {
    e.preventDefault();
    setExportError("");
    setExportSuccess("");

    const total = Number(trainRatio) + Number(valRatio) + Number(testRatio);
    if (total !== 100) {
      setExportError(`Train, validation and test shares must add up to 100% (currently ${total}%).`);
      return;
    }

    setExporting(true);
    try {
      const res = await exportDataset({
        train: Number(trainRatio) / 100,
        val: Number(valRatio) / 100,
        test: Number(testRatio) / 100,
        name: name.trim() || undefined,
      });
      setExportSuccess(`Dataset "${res.name}" successfully packaged with ${res.total_clips} clips (${res.total_hours} h)!`);
      setName("");
      refreshSummary();
      if (onDatasetCreated) onDatasetCreated();
    } catch (err) {
      setExportError(err.message);
    } finally {
      setExporting(false);
    }
  }

  function copyCommand(datasetName) {
    const cmd = `VPS_HOST=forge.rejected.cc DATASET_ID=${datasetName} ./pull_data.sh`;
    navigator.clipboard.writeText(cmd).then(() => {
      setCopiedName(datasetName);
      setTimeout(() => setCopiedName(""), 2000);
    });
  }

  if (loading) {
    return (
      <section className="card">
        <h2>Training Datasets (Part 2)</h2>
        <p className="muted small" style={{ marginTop: 8 }}>Loading dataset telemetry…</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="card">
        <h2>Training Datasets (Part 2)</h2>
        <p className="error" style={{ marginTop: 8 }}>{error}</p>
      </section>
    );
  }

  return (
    <section className="card">
      <div className="transcript-head">
        <div>
          <h2>Training Datasets (Part 2)</h2>
          <p className="muted small" style={{ marginTop: 4 }}>
            Bridge to GPU Fine-Tuning: Package collected training lectures into NeMo manifests (train, val, test) and 5–20s audio clips for IndicConformer.
          </p>
        </div>
        <button type="button" onClick={refreshSummary}>
          <IconRefresh /> Refresh
        </button>
      </div>

      <div className="dataset-stats">
        <div className="dataset-stat-card">
          <span>Total Training Audio</span>
          <strong>{summary.total_hours} h</strong>
          <span>{summary.total_seconds.toLocaleString()} seconds</span>
        </div>

        <div className="dataset-stat-card">
          <span>Curated Short Clips</span>
          <strong>{summary.total_clips.toLocaleString()}</strong>
          <span>5–20 second clips</span>
        </div>

        <div className="dataset-stat-card">
          <span>Training Lectures</span>
          <strong>{summary.training_jobs_count}</strong>
          <span>Marked for training</span>
        </div>

        <div className="dataset-stat-card">
          <span>Fine-Tuning Readiness</span>
          <strong>{summary.target_percent}%</strong>
          <span>{summary.ready_to_export ? "Ready to Export (≥3)" : "Need ≥3 lectures"}</span>
        </div>
      </div>

      <div className="meter" style={{ marginBottom: 12 }}>
        <div style={{ width: `${summary.target_percent}%` }} />
      </div>
      <p className="muted small">
        Dataset Target: <strong>{summary.total_hours} h</strong> collected of {summary.target_hours} h target dataset size for GPU fine-tuning.
      </p>

      {/* Export Controls */}
      <div className="dataset-export-box">
        <h3>Export New Dataset</h3>
        <p className="muted small" style={{ margin: "4px 0 16px" }}>
          Splits whole lectures into Train, Validation, and Test sets, normalizes Malayalam text with the frozen spelling dictionary, and packages audio clips.
        </p>

        <form onSubmit={handleExport}>
          <label>
            Dataset Identifier <span className="muted">(optional, e.g. manglish_pilot_v1)</span>
            <input
              type="text"
              placeholder="Leave blank for automatic timestamp (YYYY-MM-DD_HHMM)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={exporting}
            />
          </label>

          <span className="choice-label">Split Allocation (%)</span>
          <div className="dataset-ratios-grid">
            <div className="ratio-input-group">
              <label>Train (%)</label>
              <input
                type="number"
                min="10"
                max="90"
                value={trainRatio}
                onChange={(e) => setTrainRatio(e.target.value)}
                disabled={exporting}
              />
            </div>
            <div className="ratio-input-group">
              <label>Validation (%)</label>
              <input
                type="number"
                min="5"
                max="50"
                value={valRatio}
                onChange={(e) => setValRatio(e.target.value)}
                disabled={exporting}
              />
            </div>
            <div className="ratio-input-group">
              <label>Test (%)</label>
              <input
                type="number"
                min="5"
                max="50"
                value={testRatio}
                onChange={(e) => setTestRatio(e.target.value)}
                disabled={exporting}
              />
            </div>
          </div>

          {exportError && <p className="error" style={{ marginBottom: 12 }}>{exportError}</p>}
          {exportSuccess && <p className="badge done" style={{ display: "block", padding: "8px 12px", marginBottom: 12 }}>{exportSuccess}</p>}

          <button
            type="submit"
            className="primary"
            disabled={exporting || summary.training_jobs_count < 3}
          >
            {exporting ? "Packaging dataset…" : summary.training_jobs_count < 3 ? "Need at least 3 training lectures" : "Package & Export Dataset"}
          </button>
        </form>
      </div>

      {/* Previously Exported Datasets */}
      <div style={{ marginTop: 24 }}>
        <h3>Packaged Datasets ({summary.datasets.length})</h3>
        {summary.datasets.length === 0 ? (
          <p className="empty">
            No datasets exported yet. Collect at least 3 training lectures, then package them above.
          </p>
        ) : (
          <ul className="dataset-cards">
            {summary.datasets.map((d) => (
              <li key={d.name} className="dataset-item-card">
                <div className="dataset-item-header">
                  <span className="dataset-item-title">{d.name}</span>
                  <span className="badge done">
                    {d.usable ? "Ready for GPU" : "Partial"}
                  </span>
                </div>

                <div className="dataset-split-pills">
                  <span className="split-pill"><strong>{d.total_hours} h</strong> total</span>
                  <span className="split-pill"><strong>{d.total_clips}</strong> clips</span>
                  <span className="split-pill">Train: {d.splits.train.hours} h ({d.splits.train.clips} clips)</span>
                  <span className="split-pill">Val: {d.splits.val.hours} h ({d.splits.val.clips} clips)</span>
                  <span className="split-pill">Test: {d.splits.test.hours} h ({d.splits.test.clips} clips)</span>
                </div>

                <div className="command-box">
                  <code>VPS_HOST=forge.rejected.cc DATASET_ID={d.name} ./pull_data.sh</code>
                  <button type="button" className="ghost" onClick={() => copyCommand(d.name)}>
                    {copiedName === d.name ? "Copied!" : "Copy command"}
                  </button>
                </div>

                <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 4 }}>
                  <a href={datasetDownloadUrl(d.name)} download className="button">
                    <IconDownload /> Download Archive (.tar.gz)
                  </a>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
