import { useEffect, useRef, useState } from "react";
import "./App.css";

const DEFAULT_OWNER = "Lukaa98";
const DEFAULT_REPO = "AI-horror-stories";
const DEFAULT_BRANCH = "main";
const PROGRESS_STEPS = ["Research", "Review", "Render", "Complete"];

function loadSettings() {
  try {
    return JSON.parse(localStorage.getItem("cars-ui-settings") || "{}");
  } catch {
    return {};
  }
}

function saveSettings(settings) {
  localStorage.setItem("cars-ui-settings", JSON.stringify(settings));
}

function makeDraftId(request) {
  const slug = request
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  const stamp = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
  return `${slug || "draft"}-${stamp}`;
}

async function dispatchWorkflow({ owner, repo, branch, token, workflow, inputs }) {
  const res = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: branch, inputs }),
    }
  );
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Dispatch failed (${res.status}): ${body}`);
  }
}

async function pollForFile({ owner, repo, branch, path, signal, intervalMs = 6000, timeoutMs = 8 * 60 * 1000 }) {
  const start = Date.now();
  const url = `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${path}`;
  while (Date.now() - start < timeoutMs) {
    if (signal.aborted) throw new Error("Cancelled");
    const res = await fetch(`${url}?_=${Date.now()}`, { cache: "no-store" });
    if (res.ok) return res;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`Timed out waiting for ${path}`);
}

export default function App() {
  const [settings, setSettings] = useState(() => ({
    token: "",
    owner: DEFAULT_OWNER,
    repo: DEFAULT_REPO,
    branch: DEFAULT_BRANCH,
    ...loadSettings(),
  }));
  const [request, setRequest] = useState("");
  const [draftId, setDraftId] = useState(null);
  const [stage, setStage] = useState("idle"); // idle | researching | researched | generating | done | error
  const [error, setError] = useState(null);
  const [research, setResearch] = useState(null);
  const [videoUrl, setVideoUrl] = useState(null);
  const [statusDetail, setStatusDetail] = useState("Ready for a new request");
  const abortRef = useRef(null);

  useEffect(() => saveSettings(settings), [settings]);

  const repoOk = settings.token && settings.owner && settings.repo && settings.branch;

  async function handleResearch() {
    if (!repoOk) {
      setError("Fill in your GitHub token + repo settings first.");
      return;
    }
    if (!request.trim()) return;
    setError(null);
    setResearch(null);
    setVideoUrl(null);
    const id = makeDraftId(request);
    setDraftId(id);
    setStage("researching");
    setStatusDetail("Dispatching the research workflow…");
    abortRef.current = new AbortController();
    try {
      await dispatchWorkflow({
        owner: settings.owner,
        repo: settings.repo,
        branch: settings.branch,
        token: settings.token,
        workflow: "cars-research.yml",
        inputs: { request, draft_id: id },
      });
      setStatusDetail("Research workflow started — gathering facts and photos…");
      const res = await pollForFile({
        owner: settings.owner,
        repo: settings.repo,
        branch: settings.branch,
        path: `cars/drafts/${id}/research.json`,
        signal: abortRef.current.signal,
      });
      const data = await res.json();
      setResearch(data);
      setStage("researched");
      setStatusDetail("Research ready for review");
    } catch (err) {
      setError(String(err.message || err));
      setStage("error");
      setStatusDetail("Research failed — check the error below and try again");
    }
  }

  async function handleGenerate() {
    if (!draftId) return;
    setError(null);
    setStage("generating");
    setStatusDetail("Dispatching the Onyx render workflow…");
    abortRef.current = new AbortController();
    try {
      await dispatchWorkflow({
        owner: settings.owner,
        repo: settings.repo,
        branch: settings.branch,
        token: settings.token,
        workflow: "cars-generate-from-research.yml",
        inputs: { draft_id: draftId, tts_provider: "openai" },
      });
      setStatusDetail("Rendering video with the Onyx voice…");
      await pollForFile({
        owner: settings.owner,
        repo: settings.repo,
        branch: settings.branch,
        path: `cars/drafts/${draftId}/final_short.mp4`,
        signal: abortRef.current.signal,
        timeoutMs: 12 * 60 * 1000,
      });
      setVideoUrl(
        `https://raw.githubusercontent.com/${settings.owner}/${settings.repo}/${settings.branch}/cars/drafts/${draftId}/final_short.mp4?_=${Date.now()}`
      );
      setStage("done");
      setStatusDetail("Video complete");
    } catch (err) {
      setError(String(err.message || err));
      setStage("error");
      setStatusDetail("Render failed — check the error below and try again");
    }
  }

  function rawUrl(relativePath) {
    return `https://raw.githubusercontent.com/${settings.owner}/${settings.repo}/${settings.branch}/cars/drafts/${draftId}/${relativePath}`;
  }

  const activeStep = stage === "idle" ? 0 : stage === "researching" ? 0 : stage === "researched" ? 1 : stage === "generating" ? 2 : stage === "done" ? 3 : 0;

  return (
    <div className="page">
      <header className="hero">
        <div><span className="version">V3</span><h1>Cars Ranking Studio</h1></div>
        <span className={`live-state ${stage}`}>{stage === "idle" ? "Ready" : stage}</span>
      </header>

      <div className="progress-panel" aria-label="Generation progress">
        <div className="progress-steps">
          {PROGRESS_STEPS.map((label, index) => (
            <div className={`progress-step ${index < activeStep ? "complete" : ""} ${index === activeStep ? "active" : ""}`} key={label}>
              <span>{index < activeStep || stage === "done" ? "✓" : index + 1}</span>
              <strong>{label}</strong>
            </div>
          ))}
        </div>
        <p className="progress-detail">{statusDetail}</p>
      </div>

      <details className="settings" open={!repoOk}>
        <summary>GitHub settings {repoOk ? "✓" : "(required)"}</summary>
        <div className="settings-grid">
          <label>
            Personal Access Token
            <input
              type="password"
              placeholder="ghp_..."
              value={settings.token}
              onChange={(e) => setSettings({ ...settings, token: e.target.value })}
            />
          </label>
          <label>
            Owner
            <input value={settings.owner} onChange={(e) => setSettings({ ...settings, owner: e.target.value })} />
          </label>
          <label>
            Repo
            <input value={settings.repo} onChange={(e) => setSettings({ ...settings, repo: e.target.value })} />
          </label>
          <label>
            Branch
            <input value={settings.branch} onChange={(e) => setSettings({ ...settings, branch: e.target.value })} />
          </label>
        </div>
        <p className="hint">
          Token needs "repo" + "workflow" scope (fine-grained: Contents + Actions read/write on this repo only).
          Stored only in this browser's localStorage.
        </p>
      </details>

      <div className="request-row">
        <input
          className="request-input"
          placeholder='e.g. "ranking video of Corvette C8 trims" or "Mustang generations 2015-2024"'
          value={request}
          onChange={(e) => setRequest(e.target.value)}
          disabled={stage === "researching" || stage === "generating"}
        />
        <button onClick={handleResearch} disabled={!repoOk || !request.trim() || stage === "researching" || stage === "generating"}>
          {stage === "researching" ? "Researching…" : "Research"}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {stage === "researching" && (
        <p className="status">AI is researching real facts + gathering photos. This can take a few minutes...</p>
      )}

      {research && (
        <div className="research-panel">
          <h2>{research.title}</h2>
          <p className="rationale">{research.order_rationale}</p>
          <div className="entries">
            {research.entries.map((entry, i) => (
              <div key={i} className="entry-card">
                <div className="entry-rank">#{4 - i}</div>
                <h3>{entry.name} <span className="years">({entry.years})</span></h3>
                <p className="stat">{entry.stat}</p>
                <p className="label">{entry.label}</p>
                <p className="fact">{entry.one_line_fact}</p>
                <div className="thumbs">
                  {(entry.images || []).length === 0 && <span className="no-images">no images found</span>}
                  {(entry.images || []).map((img, j) => (
                    <a key={j} href={rawUrl(img)} target="_blank" rel="noreferrer">
                      <img src={rawUrl(img)} alt={entry.name} />
                    </a>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <p className="close-line">Closing line: "{research.close_narration}"</p>

          <button
            className="generate-btn"
            onClick={handleGenerate}
            disabled={stage === "generating" || research.entries.some((e) => !(e.images || []).length)}
          >
            {stage === "generating" ? "Generating with Onyx…" : "Generate Video with Onyx"}
          </button>
          {research.entries.some((e) => !(e.images || []).length) && (
            <p className="hint">Can't generate -- at least one entry has no images. Try a different request.</p>
          )}
        </div>
      )}

      {stage === "generating" && <p className="status">Rendering the video. This can take several minutes...</p>}

      {videoUrl && (
        <div className="video-panel">
          <h2>Done</h2>
          <video controls src={videoUrl} width="360" />
          <p>
            <a href={videoUrl} target="_blank" rel="noreferrer">Open video directly</a>
          </p>
        </div>
      )}
    </div>
  );
}
