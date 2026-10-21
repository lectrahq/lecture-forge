// All calls to the backend. Paths are relative (/api/...), so the same build
// works on any domain: Caddy forwards /api/* to the FastAPI backend.

function errorMessage(status, body) {
  if (body && body.detail) {
    // FastAPI sends either a string or a list of validation problems.
    return typeof body.detail === "string"
      ? body.detail
      : body.detail.map((d) => d.msg).join("; ");
  }
  if (status === 413) return "The file is too large (the limit is 2 GB).";
  if (status === 502 || status === 503 || status === 504)
    return "The backend is not reachable right now. Try again in a minute.";
  return `Request failed (HTTP ${status}).`;
}

async function request(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("Network error: could not reach the server.");
  }
  let body = null;
  try {
    body = await response.json();
  } catch {
    // not JSON (e.g. an error page) - handled below
  }
  if (!response.ok) throw new Error(errorMessage(response.status, body));
  return body;
}

export function createYoutubeJob(url, keyterms, engine, purpose) {
  return request("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, keyterms, engine, purpose }),
  });
}

export const getJob = (id) => request(`/api/jobs/${encodeURIComponent(id)}`);
export const getTranscript = (id) =>
  request(`/api/jobs/${encodeURIComponent(id)}/transcript`);
export const getComparison = (id) =>
  request(`/api/jobs/${encodeURIComponent(id)}/comparison`);
export const getUsage = () => request("/api/usage");

// purpose is "training" or "transcribe"; omit it for every job.
export const listJobs = (purpose) =>
  request(purpose ? `/api/jobs?purpose=${encodeURIComponent(purpose)}` : "/api/jobs");

// Turn a finished transcribe-only job into training data. Spends no credits:
// the word timings needed to cut the clips are already saved.
export const promoteJob = (id) =>
  request(`/api/jobs/${encodeURIComponent(id)}/promote`, { method: "POST" });

// Plain link target: the browser downloads the WAV itself (no need to load it into JS).
export const audioUrl = (id) => `/api/jobs/${encodeURIComponent(id)}/audio`;

// The English-to-Malayalam spelling table, commonest words first.
export const getEnglishMap = () => request("/api/english-map");

// approved=false saves the spelling without letting it count towards scoring or
// training: only a human-approved spelling may be used for either.
export const setEnglishSpelling = (word, spelling, approved) =>
  request(`/api/english-map/${encodeURIComponent(word)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ spelling, approved }),
  });

// Uploads use XMLHttpRequest instead of fetch because only XHR reports
// upload progress, which matters for files of hundreds of megabytes.
export function uploadFile(file, keyterms, engine, purpose, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    form.append("keyterms", keyterms.join(","));
    form.append("engine", engine);
    form.append("purpose", purpose);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs/upload");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      let body = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // not JSON
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new Error(errorMessage(xhr.status, body)));
    };
    xhr.onerror = () => reject(new Error("Network error during upload."));
    xhr.send(form);
  });
}
