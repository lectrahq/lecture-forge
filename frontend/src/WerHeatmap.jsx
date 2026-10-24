import React, { useState, useMemo, useRef, useEffect, useCallback } from "react";
import { audioSnippetUrl } from "./api";

// SVG Icons
function IconVolume() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
      <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
      <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
    </svg>
  );
}

function IconRefresh() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2" />
    </svg>
  );
}

function IconSearch() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

function IconLayers() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="12 2 2 7 12 12 22 7 12 2" />
      <polyline points="2 17 12 22 22 17" />
      <polyline points="2 12 12 17 22 12" />
    </svg>
  );
}

function formatTime(s) {
  if (s == null || isNaN(s)) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

export default function WerHeatmap({
  comparison,
  jobId,
  onRefresh,
  refreshing = false,
}) {
  const { segments = [], overall = {}, metrics = {}, heatmap_ribbon = [], oov_heatmap = [] } = comparison || {};

  const [viewMode, setViewMode] = useState("diff"); // 'diff' | 'side-by-side' | 'oov'
  const [filterType, setFilterType] = useState("all"); // 'all' | 'disagreed' | 'matched' | 'subs' | 'dels'
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedSegmentIdx, setSelectedSegmentIdx] = useState(null);
  const [hoveredRibbonItem, setHoveredRibbonItem] = useState(null);
  const [playingSnippet, setPlayingSnippet] = useState(null);

  const audioRef = useRef(null);
  const segmentRefs = useRef({});

  // Stop audio on unmount
  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
    };
  }, []);

  const playSegment = useCallback(
    (start, end) => {
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
    },
    [jobId, playingSnippet]
  );

  const scrollToSegment = (idx) => {
    setSelectedSegmentIdx(idx);
    const el = segmentRefs.current[idx];
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  // Filtered segments
  const filteredSegments = useMemo(() => {
    return segments
      .map((s, idx) => ({ ...s, origIdx: idx }))
      .filter((s) => {
        const counts = s.alignment_counts || {};
        const wer = s.scores?.wer;

        if (filterType === "matched" && (wer !== 0)) return false;
        if (filterType === "disagreed" && (wer == null || wer === 0)) return false;
        if (filterType === "subs" && (counts.substitutions || 0) === 0) return false;
        if (filterType === "dels" && (counts.deletions || 0) === 0) return false;

        if (searchTerm.trim()) {
          const q = searchTerm.toLowerCase();
          const e = (s.elevenlabs || "").toLowerCase();
          const l = (s.local || "").toLowerCase();
          return e.includes(q) || l.includes(q);
        }
        return true;
      });
  }, [segments, filterType, searchTerm]);

  // Overall metrics
  const totalRef = metrics.total_words || 1;
  const matchRate = metrics.matches != null ? ((metrics.matches / totalRef) * 100).toFixed(1) : ((1 - (overall.wer || 0)) * 100).toFixed(1);
  const werRate = ((overall.wer || 0) * 100).toFixed(1);
  const cerRate = ((overall.cer || 0) * 100).toFixed(1);
  const malWer = metrics.malayalam_wer != null ? (metrics.malayalam_wer * 100).toFixed(1) : "–";
  const loanWer = metrics.loanword_wer != null ? (metrics.loanword_wer * 100).toFixed(1) : "–";

  return (
    <div className="wer-heatmap-container">
      {/* Header & Controls */}
      <div className="wer-header">
        <div className="wer-title-area">
          <h3 className="wer-title">
            <IconLayers /> Visual Word Error Rate & OOV Heatmap
          </h3>
          <span className="wer-subtitle">
            Token-by-token alignment, acoustic discrepancy heatmap, and technical vocabulary analysis
          </span>
        </div>
        {onRefresh && (
          <button
            type="button"
            className="secondary-btn btn-sm wer-refresh-btn"
            onClick={onRefresh}
            disabled={refreshing}
            title="Recompute alignment and heatmap"
          >
            <IconRefresh />
            {refreshing ? "Recomputing…" : "Recompute"}
          </button>
        )}
      </div>

      {/* Metric Cards */}
      <div className="compare-scorecards wer-scorecards">
        <div className="compare-scorecard scorecard-match">
          <span className="compare-scorecard-label">Exact Token Agreement</span>
          <span className="compare-scorecard-value" style={{ color: "var(--ok)" }}>
            {matchRate}%
          </span>
          <span className="scorecard-sub">{metrics.matches || 0} matching words</span>
        </div>

        <div className="compare-scorecard scorecard-wer">
          <span className="compare-scorecard-label">Word Error Rate (WER)</span>
          <span className="compare-scorecard-value" style={{ color: "var(--warn)" }}>
            {werRate}%
          </span>
          <span className="scorecard-sub">
            {(metrics.substitutions || 0) + (metrics.deletions || 0) + (metrics.insertions || 0)} total errors
          </span>
        </div>

        <div className="compare-scorecard scorecard-cer">
          <span className="compare-scorecard-label">Character Error Rate (CER)</span>
          <span className="compare-scorecard-value" style={{ color: "var(--accent)" }}>
            {cerRate}%
          </span>
          <span className="scorecard-sub">Letter-level distance</span>
        </div>

        <div className="compare-scorecard scorecard-breakdown">
          <span className="compare-scorecard-label">Acoustic Domain Split</span>
          <div className="scorecard-split-row">
            <div className="split-item" title="WER on native Malayalam words">
              <span className="split-label">Malayalam WER</span>
              <span className="split-val">{malWer}%</span>
            </div>
            <div className="split-item" title="WER on English technical loanwords">
              <span className="split-label">Tech Loanword WER</span>
              <span className="split-val highlight-loan">{loanWer}%</span>
            </div>
          </div>
        </div>
      </div>

      {/* Interactive Timeline Ribbon */}
      {heatmap_ribbon.length > 0 && (
        <div className="heatmap-ribbon-card">
          <div className="heatmap-ribbon-header">
            <span className="ribbon-title">Timeline Heatmap Ribbon</span>
            <div className="ribbon-legend">
              <span className="legend-chip legend-match">0% WER Match</span>
              <span className="legend-chip legend-low">&lt;25% Low</span>
              <span className="legend-chip legend-med">25-50% Med</span>
              <span className="legend-chip legend-high">&gt;50% High</span>
            </div>
          </div>

          <div className="heatmap-ribbon-track">
            {heatmap_ribbon.map((item) => {
              const isSelected = selectedSegmentIdx === item.idx;
              const severityClass = `ribbon-bar-${item.severity || "match"}`;
              return (
                <div
                  key={item.idx}
                  className={`ribbon-bar ${severityClass} ${isSelected ? "selected" : ""}`}
                  onClick={() => scrollToSegment(item.idx)}
                  onMouseEnter={() => setHoveredRibbonItem(item)}
                  onMouseLeave={() => setHoveredRibbonItem(null)}
                  title={`Seg #${item.idx + 1} [${formatTime(item.start)} - ${formatTime(item.end)}] · WER: ${(item.wer * 100).toFixed(0)}% (${item.errors} errors)`}
                />
              );
            })}
          </div>

          {hoveredRibbonItem && (
            <div className="ribbon-tooltip-bar">
              <span className="ribbon-tooltip-tag">
                Segment #{hoveredRibbonItem.idx + 1}
              </span>
              <span>
                {formatTime(hoveredRibbonItem.start)} – {formatTime(hoveredRibbonItem.end)}
              </span>
              <span className="ribbon-tooltip-wer">
                WER: {(hoveredRibbonItem.wer * 100).toFixed(1)}% ({hoveredRibbonItem.errors} discrepancies)
              </span>
              <span>CER: {(hoveredRibbonItem.cer * 100).toFixed(1)}%</span>
              <button
                type="button"
                className="ribbon-play-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  playSegment(hoveredRibbonItem.start, hoveredRibbonItem.end);
                }}
              >
                <IconVolume /> Play
              </button>
            </div>
          )}
        </div>
      )}

      {/* Main Tab Switcher */}
      <div className="compare-filter-bar wer-filter-bar">
        <div className="tabs" role="tablist" style={{ marginBottom: 0 }}>
          <button
            type="button"
            className={viewMode === "diff" ? "tab active" : "tab"}
            onClick={() => setViewMode("diff")}
          >
            Token Diff View
          </button>
          <button
            type="button"
            className={viewMode === "side-by-side" ? "tab active" : "tab"}
            onClick={() => setViewMode("side-by-side")}
          >
            Side-by-Side Table
          </button>
          <button
            type="button"
            className={viewMode === "oov" ? "tab active" : "tab"}
            onClick={() => setViewMode("oov")}
          >
            OOV & Technical Heatmap ({oov_heatmap.length})
          </button>
        </div>

        {viewMode !== "oov" && (
          <div className="wer-filter-actions">
            <div className="pill-filters">
              <button
                type="button"
                className={`pill-btn ${filterType === "all" ? "active" : ""}`}
                onClick={() => setFilterType("all")}
              >
                All ({segments.length})
              </button>
              <button
                type="button"
                className={`pill-btn ${filterType === "disagreed" ? "active" : ""}`}
                onClick={() => setFilterType("disagreed")}
              >
                Discrepancies
              </button>
              <button
                type="button"
                className={`pill-btn ${filterType === "subs" ? "active" : ""}`}
                onClick={() => setFilterType("subs")}
              >
                Substitutions ({metrics.substitutions || 0})
              </button>
              <button
                type="button"
                className={`pill-btn ${filterType === "dels" ? "active" : ""}`}
                onClick={() => setFilterType("dels")}
              >
                Deletions ({metrics.deletions || 0})
              </button>
              <button
                type="button"
                className={`pill-btn ${filterType === "matched" ? "active" : ""}`}
                onClick={() => setFilterType("matched")}
              >
                Exact Matches
              </button>
            </div>

            <div className="search-input-wrap wer-search">
              <IconSearch />
              <input
                type="text"
                placeholder="Search words…"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
              {searchTerm && (
                <button type="button" className="search-clear-btn" onClick={() => setSearchTerm("")}>
                  ×
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* VIEW 1: TOKEN DIFF VIEW */}
      {viewMode === "diff" && (
        <div className="wer-diff-list">
          {filteredSegments.length === 0 ? (
            <div className="empty-state-card">No segments match the active filter.</div>
          ) : (
            filteredSegments.map((s) => {
              const counts = s.alignment_counts || {};
              const werPct = s.scores?.wer != null ? (s.scores.wer * 100).toFixed(0) : "–";
              const isPlaying = playingSnippet === `${s.start}-${s.end}`;

              return (
                <div
                  key={s.origIdx}
                  ref={(el) => (segmentRefs.current[s.origIdx] = el)}
                  className={`wer-diff-card ${selectedSegmentIdx === s.origIdx ? "highlighted-seg" : ""}`}
                >
                  <div className="wer-card-header">
                    <div className="wer-card-left">
                      <span className="seg-num-badge">#{s.origIdx + 1}</span>
                      <span className="seg-timestamp">
                        {formatTime(s.start)} – {formatTime(s.end)}
                      </span>
                      {jobId && (
                        <button
                          type="button"
                          className={`compare-audio-btn ${isPlaying ? "playing" : ""}`}
                          onClick={() => playSegment(s.start, s.end)}
                          title="Play segment audio"
                        >
                          <IconVolume />
                          <span>{isPlaying ? "Playing…" : "Play Audio"}</span>
                        </button>
                      )}
                    </div>

                    <div className="wer-card-badges">
                      <span className={`badge ${s.scores?.wer === 0 ? "done" : "warn"}`}>
                        WER: {werPct}%
                      </span>
                      {counts.substitutions > 0 && (
                        <span className="badge badge-sub" title="Substituted words">
                          {counts.substitutions} S
                        </span>
                      )}
                      {counts.deletions > 0 && (
                        <span className="badge badge-del" title="Deleted / omitted words">
                          {counts.deletions} D
                        </span>
                      )}
                      {counts.insertions > 0 && (
                        <span className="badge badge-ins" title="Inserted words">
                          {counts.insertions} I
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Token Diff Paragraph */}
                  <div className="wer-diff-tokens">
                    {s.alignment && s.alignment.length > 0 ? (
                      s.alignment.map((tok, tIdx) => {
                        if (tok.type === "match") {
                          return (
                            <span
                              key={tIdx}
                              className={`diff-tok tok-match ${tok.is_loanword ? "tok-loan" : ""}`}
                              title={tok.is_loanword ? "Technical loanword match" : "Exact match"}
                            >
                              {tok.ref}
                            </span>
                          );
                        }
                        if (tok.type === "substitution") {
                          return (
                            <span
                              key={tIdx}
                              className={`diff-tok tok-sub ${tok.is_loanword ? "tok-loan-sub" : ""}`}
                              title={`Expected: "${tok.ref}" → Local predicted: "${tok.hyp}" (Edit distance: ${tok.char_dist})`}
                            >
                              <span className="tok-sub-expected">{tok.ref}</span>
                              <span className="tok-sub-arrow">→</span>
                              <span className="tok-sub-hyp">{tok.hyp}</span>
                            </span>
                          );
                        }
                        if (tok.type === "deletion") {
                          return (
                            <span
                              key={tIdx}
                              className="diff-tok tok-del"
                              title={`Missed/Omitted by local model: "${tok.ref}"`}
                            >
                              <del>{tok.ref}</del>
                            </span>
                          );
                        }
                        if (tok.type === "insertion") {
                          return (
                            <span
                              key={tIdx}
                              className="diff-tok tok-ins"
                              title={`Inserted / extra prediction: "${tok.hyp}"`}
                            >
                              +{tok.hyp}
                            </span>
                          );
                        }
                        return null;
                      })
                    ) : (
                      <span className="raw-fallback">{s.elevenlabs}</span>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}

      {/* VIEW 2: SIDE-BY-SIDE TABLE VIEW */}
      {viewMode === "side-by-side" && (
        <div className="scroll desktop-table-view">
          <table className="segments compare">
            <thead>
              <tr>
                <th>#</th>
                <th>Time</th>
                <th>ElevenLabs (Transliterated Reference)</th>
                <th>Local IndicConformer (Hypothesis)</th>
                <th>WER</th>
              </tr>
            </thead>
            <tbody>
              {filteredSegments.map((s) => (
                <tr key={s.origIdx}>
                  <td className="num">{s.origIdx + 1}</td>
                  <td className="time">
                    {formatTime(s.start)} – {formatTime(s.end)}
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
                  <td className="time">
                    {s.scores?.wer == null ? (
                      "–"
                    ) : s.scores.wer === 0 ? (
                      <span className="badge done">0% Match</span>
                    ) : (
                      <span className="badge warn">{(s.scores.wer * 100).toFixed(0)}%</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* VIEW 3: OUT-OF-VOCABULARY (OOV) ERROR HEATMAP */}
      {viewMode === "oov" && (
        <div className="oov-heatmap-view">
          <div className="oov-intro-card">
            <h4>Recurring Technical Discrepancies & Out-of-Vocabulary Terms</h4>
            <p>
              These are the highest-frequency technical terms and loanwords where the local model struggled.
              Use this analysis to guide dictionary additions or Part 2 NeMo model fine-tuning targets.
            </p>
          </div>

          <div className="oov-grid">
            {oov_heatmap.length === 0 ? (
              <div className="empty-state-card">No recurring vocabulary errors detected.</div>
            ) : (
              oov_heatmap.map((item, idx) => (
                <div key={idx} className="oov-card">
                  <div className="oov-card-top">
                    <span className="oov-word">{item.word}</span>
                    <span className="oov-badge">{item.errors} / {item.occurrences} errors</span>
                  </div>

                  <div className="oov-progress-bar">
                    <div
                      className="oov-progress-fill"
                      style={{ width: `${Math.min(100, item.error_rate * 100)}%` }}
                    />
                  </div>

                  <div className="oov-details">
                    <span className="oov-rate">{(item.error_rate * 100).toFixed(0)}% error rate</span>
                    {item.top_confusions && item.top_confusions.length > 0 && (
                      <div className="oov-confusions">
                        <span className="conf-label">Predicted instead:</span>
                        {item.top_confusions.map((c, cIdx) => (
                          <span key={cIdx} className="conf-chip">{c}</span>
                        ))}
                      </div>
                    )}
                  </div>

                  <button
                    type="button"
                    className="oov-filter-btn"
                    onClick={() => {
                      setSearchTerm(item.word);
                      setViewMode("diff");
                    }}
                  >
                    View in Diff →
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
