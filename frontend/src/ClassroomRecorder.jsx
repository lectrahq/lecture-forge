import { useEffect, useRef, useState, useCallback } from "react";

function formatStopwatch(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) {
    return `${h}:${m < 10 ? "0" : ""}${m}:${s < 10 ? "0" : ""}${s}`;
  }
  return `${m < 10 ? "0" : ""}${m}:${s < 10 ? "0" : ""}${s}`;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function getDefaultLectureTitle() {
  const d = new Date();
  const pad = (n) => (n < 10 ? `0${n}` : n);
  const dateStr = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const timeStr = `${pad(d.getHours())}-${pad(d.getMinutes())}`;
  return `Classroom_Lecture_${dateStr}_${timeStr}`;
}

export function ClassroomRecorder({ onRecordingReady, onDiscard, busy }) {
  const [status, setStatus] = useState("idle"); // idle | requesting | recording | paused | recorded
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const [recordedBlob, setRecordedBlob] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [lectureTitle, setLectureTitle] = useState(getDefaultLectureTitle);
  const [audioFileSize, setAudioFileSize] = useState(0);

  const mediaRecorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);
  const timerIntervalRef = useRef(null);
  const audioCtxRef = useRef(null);
  const analyserRef = useRef(null);
  const animFrameRef = useRef(null);
  const canvasRef = useRef(null);

  // Clean up object URLs on unmount
  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
      }
      if (timerIntervalRef.current) clearInterval(timerIntervalRef.current);
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
      if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
        audioCtxRef.current.close().catch(() => {});
      }
    };
  }, [previewUrl]);

  // Live VU Equalizer Canvas Animation
  const startVisualizer = useCallback((stream) => {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      const audioCtx = new AudioCtx();
      audioCtxRef.current = audioCtx;

      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 64;
      analyser.smoothingTimeConstant = 0.8;
      analyserRef.current = analyser;

      const source = audioCtx.createMediaStreamSource(stream);
      source.connect(analyser);

      const bufferLength = analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);

      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");

      const draw = () => {
        if (!canvasRef.current) return;
        animFrameRef.current = requestAnimationFrame(draw);

        analyser.getByteFrequencyData(dataArray);

        const width = canvas.width;
        const height = canvas.height;
        ctx.clearRect(0, 0, width, height);

        const barCount = 18;
        const barWidth = (width / barCount) * 0.65;
        const barGap = (width / barCount) * 0.35;

        for (let i = 0; i < barCount; i++) {
          const val = dataArray[i] || 0;
          const percent = val / 255;
          const barHeight = Math.max(3, percent * (height * 0.85));

          const x = i * (barWidth + barGap) + barGap / 2;
          const y = (height - barHeight) / 2;

          // Gradient color: Purple to Emerald green active pulse
          const grad = ctx.createLinearGradient(0, height, 0, 0);
          grad.addColorStop(0, "#8b5cf6");
          grad.addColorStop(1, "#10b981");

          ctx.fillStyle = grad;
          ctx.beginPath();
          if (ctx.roundRect) {
            ctx.roundRect(x, y, barWidth, barHeight, 2);
          } else {
            ctx.rect(x, y, barWidth, barHeight);
          }
          ctx.fill();
        }
      };

      draw();
    } catch (e) {
      console.warn("Could not start audio visualizer:", e);
    }
  }, []);

  const stopVisualizer = useCallback(() => {
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = null;
    }
    if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    const canvas = canvasRef.current;
    if (canvas) {
      const ctx = canvas.getContext("2d");
      if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  }, []);

  // Start Live Recording
  async function startRecording() {
    setError("");
    setStatus("requesting");

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setError("Microphone recording is not supported in this browser. Please use Chrome, Firefox, or Safari.");
      setStatus("idle");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      streamRef.current = stream;

      // Detect best supported MIME type
      let mimeType = "audio/webm;codecs=opus";
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        if (MediaRecorder.isTypeSupported("audio/mp4")) {
          mimeType = "audio/mp4";
        } else if (MediaRecorder.isTypeSupported("audio/ogg")) {
          mimeType = "audio/ogg";
        } else {
          mimeType = ""; // Browser default
        }
      }

      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);

      mediaRecorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          chunksRef.current.push(e.data);
        }
      };

      recorder.onstop = () => {
        const recordedMime = recorder.mimeType || "audio/webm";
        const blob = new Blob(chunksRef.current, { type: recordedMime });
        setRecordedBlob(blob);
        setAudioFileSize(blob.size);

        const url = URL.createObjectURL(blob);
        setPreviewUrl(url);

        // Package into standard File object
        const ext = recordedMime.includes("mp4") ? ".mp4" : ".webm";
        const filename = `${lectureTitle.trim() || getDefaultLectureTitle()}${ext}`;
        const file = new File([blob], filename, { type: recordedMime });

        if (onRecordingReady) {
          onRecordingReady(file);
        }

        setStatus("recorded");
        stopVisualizer();
      };

      // Collect audio chunks every 250ms for reliable streaming buffer
      recorder.start(250);
      setStatus("recording");
      setElapsed(0);

      timerIntervalRef.current = setInterval(() => {
        setElapsed((prev) => prev + 1);
      }, 1000);

      startVisualizer(stream);
    } catch (err) {
      console.error("Microphone access error:", err);
      if (err.name === "NotAllowedError" || err.name === "PermissionDeniedError") {
        setError("Microphone permission was denied. Please allow microphone access in your browser settings to record lectures.");
      } else if (err.name === "NotFoundError" || err.name === "DevicesNotFoundError") {
        setError("No microphone device was found. Please plug in a microphone or headset.");
      } else {
        setError(`Could not access microphone: ${err.message || err.name}`);
      }
      setStatus("idle");
    }
  }

  function pauseRecording() {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
      mediaRecorderRef.current.pause();
      if (timerIntervalRef.current) clearInterval(timerIntervalRef.current);
      setStatus("paused");
    }
  }

  function resumeRecording() {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === "paused") {
      mediaRecorderRef.current.resume();
      timerIntervalRef.current = setInterval(() => {
        setElapsed((prev) => prev + 1);
      }, 1000);
      setStatus("recording");
    }
  }

  function stopRecording() {
    if (timerIntervalRef.current) clearInterval(timerIntervalRef.current);
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
    }
  }

  function discardRecording() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
    setRecordedBlob(null);
    setElapsed(0);
    setStatus("idle");
    setError("");
    if (onDiscard) onDiscard();
  }

  return (
    <div className="classroom-recorder-card">
      <div className="recorder-header">
        <div className="recorder-title-row">
          <div className="recorder-badge-group">
            <span className={`recorder-status-indicator ${status}`} />
            <strong className="recorder-title">Live Classroom Microphone</strong>
          </div>
          {status === "recording" && (
            <span className="rec-live-pill">
              <span className="rec-dot" /> REC
            </span>
          )}
          {status === "paused" && <span className="rec-paused-pill">PAUSED</span>}
          {status === "recorded" && <span className="rec-ready-pill">READY TO TRANSCRIBE</span>}
        </div>
        <p className="muted small">
          Record your professor's lecture directly from your phone or laptop. Audio is captured in high-fidelity, converted to 16 kHz uncompressed WAV, and transcribed.
        </p>
      </div>

      {/* Visualizer & Timer Display */}
      {(status === "recording" || status === "paused" || status === "requesting") && (
        <div className="recorder-active-panel">
          <div className="recorder-timer-box">
            <span className="recorder-timer-digits">{formatStopwatch(elapsed)}</span>
            <span className="recorder-timer-caption">Elapsed Lecture Time</span>
          </div>

          <div className="recorder-visualizer-box">
            <canvas ref={canvasRef} width={260} height={50} className="recorder-canvas" />
            <span className="recorder-visualizer-hint">Live microphone input level</span>
          </div>
        </div>
      )}

      {/* Post-Recording Preview Panel */}
      {status === "recorded" && (
        <div className="recorder-preview-panel">
          <div className="recorder-meta-bar">
            <span>
              <strong>Captured:</strong> {formatStopwatch(elapsed)}
            </span>
            <span>
              <strong>Size:</strong> {formatBytes(audioFileSize)}
            </span>
            <span className="ok-tag">100% Quality</span>
          </div>

          {previewUrl && (
            <div className="recorder-audio-preview">
              <audio controls src={previewUrl} className="recorder-html5-audio" />
            </div>
          )}

          <label className="recorder-title-input-label">
            Lecture title:
            <input
              type="text"
              value={lectureTitle}
              onChange={(e) => {
                setLectureTitle(e.target.value);
                if (recordedBlob && onRecordingReady) {
                  const ext = recordedBlob.type?.includes("mp4") ? ".mp4" : ".webm";
                  const filename = `${e.target.value.trim() || getDefaultLectureTitle()}${ext}`;
                  const file = new File([recordedBlob], filename, { type: recordedBlob.type });
                  onRecordingReady(file);
                }
              }}
              placeholder="e.g. KTU S3 Math Module 3 Cauchy Integral"
              disabled={busy}
            />
          </label>
        </div>
      )}

      {error && <div className="recorder-error-alert">{error}</div>}

      {/* Controls Bar */}
      <div className="recorder-controls-row">
        {status === "idle" && (
          <button
            type="button"
            className="primary recorder-start-btn"
            onClick={startRecording}
            disabled={busy}
          >
            <span className="mic-rec-dot" /> Start Live Recording
          </button>
        )}

        {status === "recording" && (
          <>
            <button
              type="button"
              className="button recorder-pause-btn"
              onClick={pauseRecording}
            >
              Pause
            </button>
            <button
              type="button"
              className="primary recorder-stop-btn"
              onClick={stopRecording}
            >
              Finish & Review Recording
            </button>
          </>
        )}

        {status === "paused" && (
          <>
            <button
              type="button"
              className="button recorder-resume-btn"
              onClick={resumeRecording}
            >
              Resume
            </button>
            <button
              type="button"
              className="primary recorder-stop-btn"
              onClick={stopRecording}
            >
              Finish & Review Recording
            </button>
          </>
        )}

        {status === "recorded" && (
          <button
            type="button"
            className="button ghost recorder-discard-btn"
            onClick={discardRecording}
            disabled={busy}
          >
            Discard & Record Again
          </button>
        )}
      </div>
    </div>
  );
}
