import { useCallback, useEffect, useRef, useState } from "react";
import { getJobWaveform } from "./api.js";

function formatTime(sec) {
  if (isNaN(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s < 10 ? "0" : ""}${s}`;
}

export function WaveformScrubber({
  jobId,
  currentTime,
  duration,
  isSeeking,
  seekValue,
  onSeekStart,
  onSeekChange,
  onSeekEnd,
}) {
  const canvasRef = useRef(null);
  const containerRef = useRef(null);
  const [peaks, setPeaks] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [hoverTime, setHoverTime] = useState(null);
  const [hoverX, setHoverX] = useState(null);
  const isPointerDownRef = useRef(false);

  // Fetch peaks from server
  useEffect(() => {
    let active = true;
    if (!jobId) return;
    setIsLoading(true);
    getJobWaveform(jobId)
      .then((res) => {
        if (active && res && Array.isArray(res.peaks)) {
          setPeaks(res.peaks);
        }
      })
      .catch(() => {
        if (active) {
          // Clean synthesized fallback so UI is always visual and responsive
          const fallback = [];
          for (let i = 0; i < 200; i++) {
            const val = 0.12 + 0.35 * Math.abs(Math.sin(i * 0.12)) + 0.2 * Math.sin(i * 0.3);
            fallback.push(Math.min(1.0, Math.max(0.08, val)));
          }
          setPeaks(fallback);
        }
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });

    return () => {
      active = false;
    };
  }, [jobId]);

  const activeTime = isSeeking ? seekValue : currentTime;
  const progress = duration > 0 ? Math.min(1, Math.max(0, activeTime / duration)) : 0;

  // Render waveform bars onto Canvas
  const drawWaveform = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    const dpr = window.devicePixelRatio || 1;
    const barWidth = 2.2 * dpr;
    const barGap = 1.4 * dpr;
    const totalBarSlot = barWidth + barGap;
    const numBars = Math.floor(width / totalBarSlot);

    if (numBars <= 0) return;

    const data = peaks.length > 0 ? peaks : [];
    const sourceLen = data.length || 1;

    // Played gradient: vibrant accent purple to cyan/teal
    const gradient = ctx.createLinearGradient(0, 0, Math.max(1, width * progress), 0);
    gradient.addColorStop(0, "#8b5cf6");
    gradient.addColorStop(1, "#38bdf8");

    const unplayedColor = "rgba(148, 163, 184, 0.28)";

    const centerY = height / 2;
    const maxBarHeight = height * 0.86;

    for (let i = 0; i < numBars; i++) {
      const srcIdx = Math.floor((i / numBars) * sourceLen);
      const amp = data.length > 0 ? (data[srcIdx] || 0.08) : (0.12 + 0.25 * Math.sin(i * 0.18));

      const barH = Math.max(2 * dpr, amp * maxBarHeight);
      const x = i * totalBarSlot;
      const y = centerY - barH / 2;

      const isPlayed = (x + barWidth) / width <= progress;

      ctx.fillStyle = isPlayed ? gradient : unplayedColor;

      ctx.beginPath();
      if (ctx.roundRect) {
        ctx.roundRect(x, y, barWidth, barH, 1.2 * dpr);
      } else {
        ctx.rect(x, y, barWidth, barH);
      }
      ctx.fill();
    }

    // Playhead glowing pin
    const cursorX = width * progress;
    ctx.beginPath();
    ctx.fillStyle = "#ffffff";
    ctx.shadowColor = "rgba(139, 92, 246, 0.8)";
    ctx.shadowBlur = 5 * dpr;
    ctx.arc(cursorX, centerY, 3 * dpr, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;
  }, [peaks, progress]);

  // Sync canvas size on mount/resize
  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width <= 0 || height <= 0) continue;
        const dpr = window.devicePixelRatio || 1;
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
        drawWaveform();
      }
    });

    resizeObserver.observe(container);
    return () => resizeObserver.disconnect();
  }, [drawWaveform]);

  // Redraw when progress or peaks change
  useEffect(() => {
    drawWaveform();
  }, [drawWaveform]);

  // Pointer interactions for seeking
  const getTimeFromPointer = (e) => {
    const container = containerRef.current;
    if (!container || !duration) return 0;
    const rect = container.getBoundingClientRect();
    const clientX = e.clientX ?? (e.touches && e.touches[0]?.clientX) ?? 0;
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    return ratio * duration;
  };

  const handlePointerDown = (e) => {
    isPointerDownRef.current = true;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // ignore
    }
    const targetTime = getTimeFromPointer(e);
    if (onSeekStart) onSeekStart(targetTime);
  };

  const handlePointerMove = (e) => {
    const container = containerRef.current;
    if (!container || !duration) return;
    const rect = container.getBoundingClientRect();
    const clientX = e.clientX;
    const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
    const targetTime = (x / rect.width) * duration;

    setHoverX(x);
    setHoverTime(targetTime);

    if (isPointerDownRef.current) {
      if (onSeekChange) onSeekChange(targetTime);
    }
  };

  const handlePointerUp = (e) => {
    if (!isPointerDownRef.current) return;
    isPointerDownRef.current = false;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      // ignore
    }
    const targetTime = getTimeFromPointer(e);
    if (onSeekEnd) onSeekEnd(targetTime);
  };

  const handlePointerLeave = () => {
    if (!isPointerDownRef.current) {
      setHoverTime(null);
      setHoverX(null);
    }
  };

  return (
    <div
      ref={containerRef}
      className={`waveform-scrubber-container ${isLoading ? "loading" : ""}`}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      onPointerLeave={handlePointerLeave}
      title="Click or drag to seek anywhere in the lecture"
      role="slider"
      aria-label="Audio waveform scrubber"
      aria-valuemin={0}
      aria-valuemax={duration || 100}
      aria-valuenow={Math.round(activeTime)}
      tabIndex={0}
      onKeyDown={(e) => {
        if (!duration) return;
        if (e.key === "ArrowLeft") {
          e.preventDefault();
          const t = Math.max(0, activeTime - 5);
          if (onSeekEnd) onSeekEnd(t);
        } else if (e.key === "ArrowRight") {
          e.preventDefault();
          const t = Math.min(duration, activeTime + 5);
          if (onSeekEnd) onSeekEnd(t);
        }
      }}
    >
      <canvas ref={canvasRef} className="waveform-canvas" />

      {/* Hover timestamp tooltip */}
      {hoverTime !== null && hoverX !== null && (
        <div
          className="waveform-hover-bubble"
          style={{ left: `${Math.max(22, Math.min(hoverX, (containerRef.current?.clientWidth || 200) - 22))}px` }}
        >
          {formatTime(hoverTime)}
        </div>
      )}
    </div>
  );
}
