import { useEffect, useRef, useState } from "react";
import "./App.css";

const DEFAULT_OWNER = "Lukaa98";
const DEFAULT_REPO = "AI-horror-stories";
const DEFAULT_BRANCH = "v7";
const OUTPUT_BRANCH = "cars-output";
const UI_VERSION = "V10";
const SETTINGS_MIGRATION = "feature-branch-v7";
const PROGRESS_STEPS = ["Research", "Review", "Render", "Complete"];
const RESEARCH_TIMEOUT_MS = 20 * 60 * 1000;
const RENDER_TIMEOUT_MS = 30 * 60 * 1000;
const YEAR_OPTIONS = Array.from({ length: new Date().getFullYear() - 1980 + 1 }, (_, index) => String(new Date().getFullYear() - index));
const WORKFLOW_OPTIONS = [
  {
    id: "overall",
    label: "Best Generations Overall",
    description: "Find 4 different generations across the full model run when available, with one representative per generation.",
  },
  {
    id: "focused",
    label: "Best Versions In One Range",
    description: "Find the 4 best trims or variants inside one generation, chassis, or year window.",
  },
];

function loadSettings() {
  try {
    const settings = JSON.parse(localStorage.getItem("cars-ui-settings") || "{}");
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

function titleCaseWords(value) {
  return String(value || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function buildStructuredRequest({ workflow, make, model, focus, startYear, endYear }) {
  const makeLabel = titleCaseWords(make);
  const modelLabel = titleCaseWords(model);
  if (!makeLabel || !modelLabel) return "";

  if (workflow === "focused") {
    const focusLabel = titleCaseWords(focus);
    const yearRange = startYear && endYear ? `${startYear} to ${endYear}` : "";
    const scope = [focusLabel, yearRange].filter(Boolean).join(" ");
    return scope
      ? `Rank the 4 best ${makeLabel} ${modelLabel} versions for ${scope}. Keep all picks inside that one generation, chassis family, or year window. Use distinctly named trims, variants, or special editions.`
      : `Rank the 4 best ${makeLabel} ${modelLabel} versions in one specific generation or year range. Use distinctly named trims, variants, or special editions.`;
  }

  return `Rank the 4 best ${makeLabel} ${modelLabel} generations overall across the full production run. Use 4 different generations when available, with one representative version from each generation. If the model has fewer than 4 true generations, use the most important era-defining versions across its history.`;
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
        if (run.status === "completed") {
          if (run.conclusion !== "success") {
            const detail = run.conclusion === "failure"
              ? "The GitHub workflow failed. Open the build log for the exact error. If it reports OpenAI insufficient_quota, verify that the OPENAI_API_KEY secret belongs to a project with active API billing and available project limits."
              : `The GitHub workflow ended with: ${run.conclusion || "unknown"}.`;
            throw new Error(detail);
          }
          return run;
        }
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
  const [workflow, setWorkflow] = useState("overall");
  const [make, setMake] = useState("");
  const [model, setModel] = useState("");
  const [focus, setFocus] = useState("");
  const [startYear, setStartYear] = useState("");
  const [endYear, setEndYear] = useState("");
  const [useCustomRequest, setUseCustomRequest] = useState(false);
  const [draftId, setDraftId] = useState(null);
  const [stage, setStage] = useState("idle");
  const [error, setError] = useState(null);
  const [research, setResearch] = useState(null);
  const [videoUrl, setVideoUrl] = useState(null);
  const [statusDetail, setStatusDetail] = useState("Ready for a new request");
  const [actionRun, setActionRun] = useState(null);
  const abortRef = useRef(null);
  const trackerIdRef = useRef(0);

  useEffect(() => saveSettings(settings), [settings]);

  const repoOk = settings.token && settings.owner && settings.repo && settings.branch;
  const builtRequest = buildStructuredRequest({ workflow, make, model, focus, startYear, endYear });
  const effectiveRequest = useCustomRequest ? request.trim() : builtRequest.trim();

  function beginRunTracking(runWorkflow, startedAt, signal) {
    const trackerId = ++trackerIdRef.current;
    setActionRun(null);
    return trackWorkflowRun({
      owner: settings.owner,
      repo: settings.repo,
      branch: settings.branch,
      token: settings.token,
      workflow: runWorkflow,
      startedAt,
      signal,
      onUpdate: (run) => {
        if (trackerIdRef.current === trackerId) setActionRun(run);
      },
    });
  }

  async function handleResearch() {
    if (!repoOk) {
      setError("Fill in your GitHub token + repo settings first.");
      return;
    }
    if (!effectiveRequest) return;
    setError(null);
    setResearch(null);
    setVideoUrl(null);
    const id = makeDraftId(effectiveRequest);
    setDraftId(id);
    setStage("researching");
    setStatusDetail("Dispatching the research workflow...");
    abortRef.current = new AbortController();
    try {
      const startedAt = Date.now();
      await dispatchWorkflow({
        owner: settings.owner,
        repo: settings.repo,
        branch: settings.branch,
        token: settings.token,
        workflow: "cars-research.yml",
        inputs: { request: effectiveRequest, draft_id: id },
      });
      const workflowRun = beginRunTracking("cars-research.yml", startedAt, abortRef.current.signal);
      setStatusDetail("Researching facts and sourcing exterior, rear, interior, and highlight photos...");
      const researchFile = pollForFile({
        owner: settings.owner,
        repo: settings.repo,
        branch: OUTPUT_BRANCH,
        path: `cars/drafts/${id}/research.json`,
        signal: abortRef.current.signal,
        timeoutMs: RESEARCH_TIMEOUT_MS,
      });
      const res = await Promise.race([researchFile, workflowRun.then(() => researchFile)]);
      const data = await res.json();
      setResearch(data);
      setStage("researched");
      setStatusDetail("Research ready for review");
    } catch (err) {
      setError(String(err.message || err));
      setStage("error");
      setStatusDetail("Research failed - check the error below and try again");
    }
  }

  async function handleGenerate() {
    if (!draftId) return;
    setError(null);
    setStage("generating");
    setStatusDetail("Dispatching the Onyx render workflow...");
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
      const workflowRun = beginRunTracking("cars-generate-from-research.yml", startedAt, abortRef.current.signal);
      setStatusDetail("Rendering video with the Onyx voice...");
      const renderedFile = pollForFile({
        owner: settings.owner,
        repo: settings.repo,
        branch: OUTPUT_BRANCH,
        path: `cars/drafts/${draftId}/final_short.mp4`,
        signal: abortRef.current.signal,
        timeoutMs: RENDER_TIMEOUT_MS,
      });
      await Promise.race([renderedFile, workflowRun.then(() => renderedFile)]);
      setVideoUrl(
        `https://raw.githubusercontent.com/${settings.owner}/${settings.repo}/${OUTPUT_BRANCH}/cars/drafts/${draftId}/final_short.mp4?_=${Date.now()}`
      );
      setStage("done");
      setStatusDetail("Video complete");
    } catch (err) {
      setError(String(err.message || err));
      setStage("error");
      setStatusDetail("Render failed - check the error below and try again");
    }
  }

  function rawUrl(relativePath) {
    return `https://raw.githubusercontent.com/${settings.owner}/${settings.repo}/${OUTPUT_BRANCH}/cars/drafts/${draftId}/${relativePath}`;
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
              <span>{index < activeStep || stage === "done" ? "OK" : index + 1}</span>
              <strong>{label}</strong>
            </div>
          ))}
        </div>
        <p className="progress-detail">{statusDetail}</p>
        <p className="branch-target">Active branch: <code>{settings.branch}</code></p>
        <p className="branch-target">Draft output branch: <code>{OUTPUT_BRANCH}</code></p>
        {actionRun && (
          <a className="build-link" href={actionRun.url} target="_blank" rel="noreferrer">
            <span className={`build-dot ${actionRun.conclusion || actionRun.status}`} />
            GitHub build #{actionRun.runNumber}: {actionRun.conclusion || actionRun.status.replace("_", " ")}
            <strong>Open build</strong>
          </a>
        )}
      </div>

      <details className="settings" open={!repoOk}>
        <summary>GitHub settings {repoOk ? "OK" : "(required)"}</summary>
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
          Stored only in this browser&apos;s localStorage.
        </p>
      </details>

      <div className="request-row">
        <div className="request-builder">
          <div className="workflow-grid">
            {WORKFLOW_OPTIONS.map((option) => (
              <button
                key={option.id}
                type="button"
                className={`workflow-card ${workflow === option.id ? "active" : ""}`}
                onClick={() => setWorkflow(option.id)}
                disabled={stage === "researching" || stage === "generating"}
              >
                <strong>{option.label}</strong>
                <span>{option.description}</span>
              </button>
            ))}
          </div>

          <div className="builder-grid">
            <label>
              Make
              <input value={make} onChange={(e) => setMake(e.target.value)} placeholder="Audi" disabled={stage === "researching" || stage === "generating"} />
            </label>
            <label>
              Model
              <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="R8" disabled={stage === "researching" || stage === "generating"} />
            </label>
            <label>
              Focus
              <input
                value={focus}
                onChange={(e) => setFocus(e.target.value)}
                placeholder={workflow === "focused" ? "C8, first gen, B7, etc." : "Used only for focused mode"}
                disabled={stage === "researching" || stage === "generating" || workflow !== "focused"}
              />
            </label>
            <label>
              Start Year
              <select value={startYear} onChange={(e) => setStartYear(e.target.value)} disabled={stage === "researching" || stage === "generating" || workflow !== "focused"}>
                <option value="">Any</option>
                {YEAR_OPTIONS.map((year) => <option key={year} value={year}>{year}</option>)}
              </select>
            </label>
            <label>
              End Year
              <select value={endYear} onChange={(e) => setEndYear(e.target.value)} disabled={stage === "researching" || stage === "generating" || workflow !== "focused"}>
                <option value="">Any</option>
                {YEAR_OPTIONS.map((year) => <option key={year} value={year}>{year}</option>)}
              </select>
            </label>
          </div>

          <label className="custom-toggle">
            <input
              type="checkbox"
              checked={useCustomRequest}
              onChange={(e) => setUseCustomRequest(e.target.checked)}
              disabled={stage === "researching" || stage === "generating"}
            />
            Use custom request text instead of the structured builder
          </label>

          <div className="request-preview">
            <span className="preview-label">{useCustomRequest ? "Custom request" : "Generated request"}</span>
            {useCustomRequest ? (
              <textarea
                className="request-input request-textarea"
                placeholder='e.g. "Rank the 4 best Audi R8 versions overall"'
                value={request}
                onChange={(e) => setRequest(e.target.value)}
                disabled={stage === "researching" || stage === "generating"}
              />
            ) : (
              <div className="request-preview-box">{builtRequest || "Choose a workflow, then enter at least make and model."}</div>
            )}
          </div>
        </div>
        <button onClick={handleResearch} disabled={!repoOk || !effectiveRequest || stage === "researching" || stage === "generating"}>
          {stage === "researching" ? "Researching..." : "Research"}
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
            disabled={stage === "generating" || research.entries.some((entry) => !(entry.images || []).length)}
          >
            {stage === "generating" ? "Generating with Onyx..." : "Generate Video with Onyx"}
          </button>
          {research.entries.some((entry) => !(entry.images || []).length) && (
            <p className="hint">Can&apos;t generate - at least one entry has no images. Try a different request.</p>
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
