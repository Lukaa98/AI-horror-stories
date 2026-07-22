import { useEffect, useRef, useState } from "react";
import "./App.css";

const DEFAULT_OWNER = "Lukaa98";
const DEFAULT_REPO = "AI-horror-stories";
const DEFAULT_BRANCH = "v7";
// Bump this for every deployed UI change so the live site is easy to verify.
const UI_VERSION = "V7";
const SETTINGS_MIGRATION = "feature-branch-v7";
const PROGRESS_STEPS = ["Research", "Review", "Render", "Complete"];
const RESEARCH_TIMEOUT_MS = 20 * 60 * 1000;
const RENDER_TIMEOUT_MS = 30 * 60 * 1000;

function loadSettings() {
  try {
    const settings = JSON.parse(localStorage.getItem("cars-ui-settings") || "{}");
    // V4 browsers retained `main` in localStorage even though this UI is being
    // tested from the feature branch. Migrate once without discarding the PAT.
    if (settings.settingsMigration !== SETTINGS_MIGRATION) {
      settings.branch = DEFAULT_BRANCH;
      settings.settingsMigration = SETTINGS_MIGRATION;
    }
    return settings;
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

async function pollForFile({ owner, repo, branch, path, signal, intervalMs = 6000, timeoutMs }) {
  const start = Date.now();
  const url = `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${path}`;
  while (Date.now() - start < timeoutMs) {
    if (signal.aborted) throw new Error("Cancelled");
    const res = await fetch(`${url}?_=${Date.now()}`, { cache: "no-store" });
    if (res.ok) return res;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  const timeoutMinutes = Math.round(timeoutMs / 60000);
  throw new Error(`Timed out after ${timeoutMinutes} minutes waiting for ${path}`);
}

async function trackWorkflowRun({ owner, repo, branch, token, workflow, startedAt, signal, onUpdate }) {
  const endpoint = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/runs?branch=${encodeURIComponent(branch)}&event=workflow_dispatch&per_page=10`;
  while (!signal.aborted) {
    const res = await fetch(endpoint, {
      headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json" },
      cache: "no-store",
    });
    if (res.ok) {
      const data = await res.json();
      const run = data.workflow_runs?.find((item) => new Date(item.created_at).getTime() >= startedAt - 10000);
      if (run) {
        onUpdate({
          url: run.html_url,
          status: run.status,
          conclusion: run.conclusion,
          runNumber: run.run_number,
        });
        if (run.status === "completed") return;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 5000));
  }
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
  const [actionRun, setActionRun] = useState(null);
  const abortRef = useRef(null);
  const trackerIdRef = useRef(0);

  useEffect(() => saveSettings(settings), [settings]);

  const repoOk = settings.token && settings.owner && settings.repo && settings.branch;

  function beginRunTracking(workflow, startedAt, signal) {
    const trackerId = ++trackerIdRef.current;
    setActionRun(null);
    trackWorkflowRun({
      owner: settings.owner,
      repo: settings.repo,
      branch: settings.branch,
      token: settings.token,
      workflow,
      startedAt,
      signal,
      onUpdate: (run) => {
        if (trackerIdRef.current === trackerId) setActionRun(run);
      },
    }).catch(() => {
      // File polling remains the source of truth if Actions status is unavailable.
    });
  }

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
      const startedAt = Date.now();
      await dispatchWorkflow({
        owner: settings.owner,
        repo: settings.repo,
        branch: settings.branch,
        token: settings.token,
        workflow: "cars-research.yml",
        inputs: { request, draft_id: id },
      });
      beginRunTracking("cars-research.yml", startedAt, abortRef.current.signal);
      setStatusDetail("Researching facts and sourcing exterior, rear, interior, and highlight photos…");
      const res = await pollForFile({
        owner: settings.owner,
        repo: settings.repo,
        branch: settings.branch,
        path: `cars/drafts/${id}/research.json`,
        signal: abortRef.current.signal,
        timeoutMs: RESEARCH_TIMEOUT_MS,
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
      const startedAt = Date.now();
      await dispatchWorkflow({
        owner: settings.owner,
        repo: settings.repo,
        branch: settings.branch,
        token: settings.token,
        workflow: "cars-generate-from-research.yml",
        inputs: { draft_id: draftId, tts_provider: "openai" },
      });
      beginRunTracking("cars-generate-from-research.yml", startedAt, abortRef.current.signal);
      setStatusDetail("Rendering video with the Onyx voice…");
      await pollForFile({
        owner: settings.owner,
        repo: settings.repo,
        branch: settings.branch,
        path: `cars/drafts/${draftId}/final_short.mp4`,
        signal: abortRef.current.signal,
        timeoutMs: RENDER_TIMEOUT_MS,
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
        <div><span className="version">{UI_VERSION}</span><h1>Cars Ranking Studio</h1></div>
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
        <p className="branch-target">Active branch: <code>{settings.branch}</code></p>
        {actionRun && (
          <a className="build-link" href={actionRun.url} target="_blank" rel="noreferrer">
            <span className={`build-dot ${actionRun.conclusion || actionRun.status}`} />
            GitHub build #{actionRun.runNumber}: {actionRun.conclusion || actionRun.status.replace("_", " ")}
            <strong>View build ↗</strong>
          </a>
        )}
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
        <p className="status">AI is researching facts and gathering varied, verified model photos. This can take a few minutes...</p>
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
