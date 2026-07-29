import { useEffect, useRef, useState } from "react";
import "./App.css";

const DEFAULT_OWNER = "Lukaa98";
const DEFAULT_REPO = "AI-horror-stories";
const DEFAULT_BRANCH = "v10";
const OUTPUT_BRANCH = "cars-output";
const UI_VERSION = "V10.38";
const VOICES = ["marin", "cedar", "coral", "verse", "onyx"];
const SETTINGS_MIGRATION = "default-branch-v10";
const PROGRESS_STEPS = ["Research", "Review", "Render", "Complete"];
const RESEARCH_TIMEOUT_MS = 60 * 60 * 1000;
const RENDER_TIMEOUT_MS = 30 * 60 * 1000;
const VIDEO_TEST_TIMEOUT_MS = 60 * 60 * 1000;
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

function parseIdTimestamp(id) {
  const match = String(id || "").match(/(\d{14})$/);
  if (!match) return null;
  const s = match[1];
  const iso = `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}T${s.slice(8, 10)}:${s.slice(10, 12)}:${s.slice(12, 14)}Z`;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

function ghHeaders(token) {
  return { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json" };
}

async function ghJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`GitHub API ${res.status}: ${body.slice(0, 300)}`);
  }
  return res.json();
}

async function fetchOutputTree({ owner, repo, token }) {
  const ref = await ghJson(
    `https://api.github.com/repos/${owner}/${repo}/git/refs/heads/${OUTPUT_BRANCH}`,
    { headers: ghHeaders(token) }
  );
  const commitSha = ref.object.sha;
  const commit = await ghJson(
    `https://api.github.com/repos/${owner}/${repo}/git/commits/${commitSha}`,
    { headers: ghHeaders(token) }
  );
  const treeData = await ghJson(
    `https://api.github.com/repos/${owner}/${repo}/git/trees/${commit.tree.sha}?recursive=1`,
    { headers: ghHeaders(token) }
  );
  return { commitSha, treeSha: commit.tree.sha, entries: treeData.tree || [] };
}

function groupDashboardEntries(entries) {
  const drafts = new Map();
  const tests = new Map();
  for (const item of entries) {
    if (item.type !== "blob") continue;
    let m = item.path.match(/^cars\/drafts\/([^/]+)\/(.+)$/);
    if (m) {
      const [, id, rest] = m;
      if (!drafts.has(id)) drafts.set(id, []);
      drafts.get(id).push(rest);
      continue;
    }
    m = item.path.match(/^cars\/video-tests\/([^/]+)\/(.+)$/);
    if (m) {
      const [, id, rest] = m;
      if (!tests.has(id)) tests.set(id, []);
      tests.get(id).push(rest);
    }
  }
  const items = [];
  for (const [id, files] of drafts) {
    items.push({
      type: "draft",
      id,
      files,
      timestamp: parseIdTimestamp(id),
      hasResearch: files.includes("research.json"),
      hasFinal: files.includes("final_short.mp4"),
      hasPreview: files.includes("preview_short.mp4"),
    });
  }
  for (const [id, files] of tests) {
    items.push({
      type: "video-test",
      id,
      files,
      timestamp: parseIdTimestamp(id),
      hasResult: files.includes("result.json"),
    });
  }
  items.sort((a, b) => (b.timestamp?.getTime() || 0) - (a.timestamp?.getTime() || 0));
  return items;
}

async function attachDashboardPreviews(items, owner, repo) {
  await Promise.allSettled(
    items.map(async (item) => {
      const folder = item.type === "draft" ? "drafts" : "video-tests";
      const file = item.type === "draft" ? "research.json" : "result.json";
      if (item.type === "draft" && !item.hasResearch) return;
      if (item.type === "video-test" && !item.hasResult) return;
      const res = await fetch(
        `https://raw.githubusercontent.com/${owner}/${repo}/${OUTPUT_BRANCH}/cars/${folder}/${item.id}/${file}?_=${Date.now()}`,
        { cache: "no-store" }
      );
      if (res.ok) item.preview = await res.json();
    })
  );
  return items;
}

async function loadDashboardEntries({ owner, repo, token }) {
  const { entries } = await fetchOutputTree({ owner, repo, token });
  const items = groupDashboardEntries(entries);
  await attachDashboardPreviews(items, owner, repo);
  return items;
}

async function deleteDashboardItem({ owner, repo, token, type, id }) {
  const prefix = type === "draft" ? `cars/drafts/${id}/` : `cars/video-tests/${id}/`;
  const ref = await ghJson(
    `https://api.github.com/repos/${owner}/${repo}/git/refs/heads/${OUTPUT_BRANCH}`,
    { headers: ghHeaders(token) }
  );
  const latestCommitSha = ref.object.sha;
  const commit = await ghJson(
    `https://api.github.com/repos/${owner}/${repo}/git/commits/${latestCommitSha}`,
    { headers: ghHeaders(token) }
  );
  const baseTreeSha = commit.tree.sha;
  const treeData = await ghJson(
    `https://api.github.com/repos/${owner}/${repo}/git/trees/${baseTreeSha}?recursive=1`,
    { headers: ghHeaders(token) }
  );
  const toRemove = (treeData.tree || []).filter(
    (entry) => entry.type === "blob" && entry.path.startsWith(prefix)
  );
  if (toRemove.length === 0) throw new Error("No files found to delete (already removed?).");
  const newTree = await ghJson(`https://api.github.com/repos/${owner}/${repo}/git/trees`, {
    method: "POST",
    headers: { ...ghHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({
      base_tree: baseTreeSha,
      tree: toRemove.map((entry) => ({ path: entry.path, mode: entry.mode, type: entry.type, sha: null })),
    }),
  });
  const newCommit = await ghJson(`https://api.github.com/repos/${owner}/${repo}/git/commits`, {
    method: "POST",
    headers: { ...ghHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({
      message: `Delete ${type} ${id}`,
      tree: newTree.sha,
      parents: [latestCommitSha],
    }),
  });
  await ghJson(`https://api.github.com/repos/${owner}/${repo}/git/refs/heads/${OUTPUT_BRANCH}`, {
    method: "PATCH",
    headers: { ...ghHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ sha: newCommit.sha }),
  });
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
  const [voice, setVoice] = useState("onyx");
  const [renderQuality, setRenderQuality] = useState(null);
  const [draftId, setDraftId] = useState(null);
  const [stage, setStage] = useState("idle");
  const [error, setError] = useState(null);
  const [research, setResearch] = useState(null);
  const [videoUrl, setVideoUrl] = useState(null);
  const [videoTestId, setVideoTestId] = useState(null);
  const [videoProbe, setVideoProbe] = useState(null);
  const [statusDetail, setStatusDetail] = useState("Ready for a new request");
  const [actionRun, setActionRun] = useState(null);
  const [view, setView] = useState("create");
  const [dashboardItems, setDashboardItems] = useState([]);
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [dashboardError, setDashboardError] = useState(null);
  const [selectedItem, setSelectedItem] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  const abortRef = useRef(null);
  const trackerIdRef = useRef(0);

  useEffect(() => saveSettings(settings), [settings]);

  useEffect(() => {
    if (view === "dashboard") loadDashboard();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

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

  async function handleVideoTest() {
    if (!repoOk || !make.trim() || !model.trim()) return;
    setError(null);
    setVideoProbe(null);
    setVideoUrl(null);
    const id = makeDraftId(`video-${make}-${model}`);
    setVideoTestId(id);
    setStage("video-testing");
    setStatusDetail(`Searching matching ${titleCaseWords(make)} ${titleCaseWords(model)} listings for engine videos...`);
    abortRef.current = new AbortController();
    try {
      const startedAt = Date.now();
      await dispatchWorkflow({
        owner: settings.owner,
        repo: settings.repo,
        branch: settings.branch,
        token: settings.token,
        workflow: "cars-research.yml",
        inputs: {
          request: `Video test for ${make.trim()} ${model.trim()}`,
          draft_id: id,
          mode: "video",
          make: make.trim(),
          model: model.trim(),
          query: [make, model, focus].filter(Boolean).join(" "),
          start_year: startYear,
          end_year: endYear,
        },
      });
      const workflowRun = beginRunTracking("cars-research.yml", startedAt, abortRef.current.signal);
      const resultFile = pollForFile({
        owner: settings.owner,
        repo: settings.repo,
        branch: OUTPUT_BRANCH,
        path: `cars/video-tests/${id}/result.json`,
        signal: abortRef.current.signal,
        timeoutMs: VIDEO_TEST_TIMEOUT_MS,
      });
      const response = await Promise.race([resultFile, workflowRun.then(() => resultFile)]);
      setVideoProbe(await response.json());
      setStage("video-done");
      setStatusDetail("Video extraction test complete");
    } catch (err) {
      setError(String(err.message || err));
      setStage("error");
      setStatusDetail("Video extraction test failed - open the build log for details");
    }
  }

  async function handleGenerate(quality) {
    if (!draftId) return;
    const outputName = quality === "full" ? "final_short.mp4" : "preview_short.mp4";
    const qualityLabel = quality === "full" ? "full-quality" : "quick preview";
    setError(null);
    setStage("generating");
    setRenderQuality(quality);
    setStatusDetail(`Dispatching the ${qualityLabel} ${voice} render workflow...`);
    abortRef.current = new AbortController();
    try {
      const startedAt = Date.now();
      await dispatchWorkflow({
        owner: settings.owner,
        repo: settings.repo,
        branch: settings.branch,
        token: settings.token,
        workflow: "cars-generate-from-research.yml",
        inputs: {
          draft_id: draftId,
          tts_provider: "openai",
          tts_voice: voice,
          render_quality: quality,
        },
      });
      const workflowRun = beginRunTracking("cars-generate-from-research.yml", startedAt, abortRef.current.signal);
      setStatusDetail(`Rendering ${qualityLabel} video with the ${voice} voice...`);
      await workflowRun;
      await pollForFile({
        owner: settings.owner,
        repo: settings.repo,
        branch: OUTPUT_BRANCH,
        path: `cars/drafts/${draftId}/${outputName}`,
        signal: abortRef.current.signal,
        timeoutMs: RENDER_TIMEOUT_MS,
      });
      const refreshedResearch = await fetch(
        `https://raw.githubusercontent.com/${settings.owner}/${settings.repo}/${OUTPUT_BRANCH}/cars/drafts/${draftId}/research.json?_=${Date.now()}`,
        { cache: "no-store" },
      );
      if (refreshedResearch.ok) setResearch(await refreshedResearch.json());
      setVideoUrl(
        `https://raw.githubusercontent.com/${settings.owner}/${settings.repo}/${OUTPUT_BRANCH}/cars/drafts/${draftId}/${outputName}?_=${Date.now()}`
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

  function rawVideoTestUrl(relativePath) {
    return `https://raw.githubusercontent.com/${settings.owner}/${settings.repo}/${OUTPUT_BRANCH}/cars/video-tests/${videoTestId}/${relativePath}`;
  }

  function dashboardRawUrl(item, relativePath) {
    const folder = item.type === "draft" ? "drafts" : "video-tests";
    return `https://raw.githubusercontent.com/${settings.owner}/${settings.repo}/${OUTPUT_BRANCH}/cars/${folder}/${item.id}/${relativePath}`;
  }

  async function loadDashboard() {
    if (!repoOk) {
      setDashboardError("Fill in your GitHub token + repo settings first.");
      return;
    }
    setDashboardLoading(true);
    setDashboardError(null);
    try {
      const items = await loadDashboardEntries({ owner: settings.owner, repo: settings.repo, token: settings.token });
      setDashboardItems(items);
    } catch (err) {
      setDashboardError(String(err.message || err));
    } finally {
      setDashboardLoading(false);
    }
  }

  async function handleDeleteItem(item) {
    setDeletingId(`${item.type}:${item.id}`);
    setDashboardError(null);
    try {
      await deleteDashboardItem({ owner: settings.owner, repo: settings.repo, token: settings.token, type: item.type, id: item.id });
      setDashboardItems((prev) => prev.filter((entry) => !(entry.type === item.type && entry.id === item.id)));
      setSelectedItem((current) => (current && current.type === item.type && current.id === item.id ? null : current));
    } catch (err) {
      setDashboardError(String(err.message || err));
    } finally {
      setDeletingId(null);
      setConfirmDeleteId(null);
    }
  }

  const activeStep = stage === "idle" ? 0 : stage === "researching" ? 0 : stage === "researched" ? 1 : stage === "generating" ? 2 : stage === "done" ? 3 : 0;

  return (
    <div className="page">
      <header className="hero">
        <div><span className="version">{UI_VERSION}</span><h1>Cars Ranking Studio</h1></div>
        <span className={`live-state ${stage}`}>{stage === "idle" ? "Ready" : stage}</span>
      </header>

      <nav className="view-tabs">
        <button type="button" className={view === "create" ? "active" : ""} onClick={() => setView("create")}>
          Create
        </button>
        <button type="button" className={view === "dashboard" ? "active" : ""} onClick={() => setView("dashboard")}>
          Dashboard
        </button>
      </nav>

      {view === "dashboard" && (
        <section className="dashboard-panel">
          <div className="dashboard-header">
            <div>
              <h2>Generated Drafts &amp; Video Tests</h2>
              <p className="hint">
                Reads directly from the <code>{OUTPUT_BRANCH}</code> branch, so results survive a refresh. Deleting
                an item commits a removal of its files to that branch.
              </p>
            </div>
            <button type="button" className="secondary" onClick={loadDashboard} disabled={dashboardLoading}>
              {dashboardLoading ? "Loading..." : "Refresh"}
            </button>
          </div>

          {dashboardError && <div className="error">{dashboardError}</div>}
          {!repoOk && <p className="hint">Fill in your GitHub token + repo settings above to load the dashboard.</p>}

          {!selectedItem && (
            <div className="dashboard-grid">
              {dashboardItems.map((item) => {
                const key = `${item.type}:${item.id}`;
                const title =
                  item.type === "draft"
                    ? item.preview?.title || item.id
                    : `Video test: ${item.preview?.query || item.id}`;
                const thumb =
                  item.type === "draft"
                    ? item.preview?.entries?.find((entry) => (entry.images || []).length)?.images?.[0]
                    : item.preview?.clips?.find((clip) => clip.thumbnail_url)?.thumbnail_url;
                const thumbUrl = item.type === "draft" && thumb ? dashboardRawUrl(item, thumb) : thumb;
                return (
                  <article className="dashboard-card" key={key}>
                    {thumbUrl && <img src={thumbUrl} alt={title} />}
                    <div className="dashboard-card-body">
                      <span className={`dashboard-type ${item.type}`}>{item.type === "draft" ? "Draft" : "Video Test"}</span>
                      <h3>{title}</h3>
                      <p className="hint">{item.timestamp ? item.timestamp.toLocaleString() : item.id}</p>
                      {item.type === "draft" && (
                        <p className="hint">
                          {item.hasFinal ? "Full render ready" : item.hasPreview ? "Preview render ready" : "No render yet"}
                        </p>
                      )}
                      <div className="dashboard-card-actions">
                        <button type="button" onClick={() => setSelectedItem({ type: item.type, id: item.id })}>
                          View
                        </button>
                        {confirmDeleteId === key ? (
                          <>
                            <button
                              type="button"
                              className="danger"
                              onClick={() => handleDeleteItem(item)}
                              disabled={deletingId === key}
                            >
                              {deletingId === key ? "Deleting..." : "Confirm delete"}
                            </button>
                            <button type="button" className="secondary" onClick={() => setConfirmDeleteId(null)}>
                              Cancel
                            </button>
                          </>
                        ) : (
                          <button type="button" className="secondary" onClick={() => setConfirmDeleteId(key)}>
                            Delete
                          </button>
                        )}
                      </div>
                    </div>
                  </article>
                );
              })}
              {!dashboardLoading && dashboardItems.length === 0 && !dashboardError && repoOk && (
                <p className="hint">Nothing generated yet. Research or test a video from the Create tab.</p>
              )}
            </div>
          )}

          {selectedItem && (() => {
            const item = dashboardItems.find((entry) => entry.type === selectedItem.type && entry.id === selectedItem.id);
            if (!item) {
              return (
                <div>
                  <button type="button" className="secondary" onClick={() => setSelectedItem(null)}>Back</button>
                  <p className="hint">This item is no longer available.</p>
                </div>
              );
            }
            const key = `${item.type}:${item.id}`;
            return (
              <div className="dashboard-detail">
                <button type="button" className="secondary" onClick={() => setSelectedItem(null)}>Back to list</button>

                {item.type === "draft" && item.preview && (
                  <div className="research-panel">
                    <h2>{item.preview.title}</h2>
                    <p className="rationale">{item.preview.order_rationale}</p>
                    {(item.hasFinal || item.hasPreview) && (
                      <div className="video-player">
                        <video controls src={dashboardRawUrl(item, item.hasFinal ? "final_short.mp4" : "preview_short.mp4")} width="360" />
                      </div>
                    )}
                    <div className="entries">
                      {(item.preview.entries || []).map((entry, i) => (
                        <div key={i} className="entry-card">
                          <div className="entry-rank">#{(item.preview.entries.length) - i}</div>
                          <h3>{entry.name} <span className="years">({entry.years})</span></h3>
                          <p className="stat">{entry.stat}</p>
                          <p className="label">{entry.label}</p>
                          <p className="fact">{entry.one_line_fact}</p>
                          <div className="thumbs">
                            {(entry.images || []).length === 0 && <span className="no-images">no images found</span>}
                            {(entry.images || []).map((img, j) => {
                              const review = (entry.image_reviews || []).find((r) => r.path === img);
                              const description = review?.view_description || review?.category || "verified car image";
                              return (
                                <a key={j} href={dashboardRawUrl(item, img)} target="_blank" rel="noreferrer" title={description}>
                                  <img src={dashboardRawUrl(item, img)} alt={`${entry.name} — ${description}`} />
                                  <span className="image-label">{description}</span>
                                </a>
                              );
                            })}
                          </div>
                          {(entry.engine_videos || []).length > 0 && (
                            <article className="video-probe-card entry-engine-clip">
                              <h3>{entry.name} engine clip</h3>
                              {entry.engine_clip_preview?.source?.thumbnail_url && (
                                <img
                                  className="video-probe-thumb"
                                  src={entry.engine_clip_preview.source.thumbnail_url}
                                  alt={`${entry.name} engine clip source thumbnail`}
                                />
                              )}
                              {entry.engine_clip_preview?.approved && entry.engine_clip_preview?.path ? (
                                <video controls src={dashboardRawUrl(item, entry.engine_clip_preview.path)} preload="metadata" />
                              ) : (
                                <p className="error">No usable clip: {entry.engine_clip_preview?.error || "verification rejected it"}</p>
                              )}
                              {entry.engine_clip_preview?.source?.auction_url && (
                                <a href={entry.engine_clip_preview.source.auction_url} target="_blank" rel="noreferrer">
                                  Open source listing
                                </a>
                              )}
                            </article>
                          )}
                        </div>
                      ))}
                    </div>
                    <p className="close-line">Closing line: "{item.preview.close_narration}"</p>
                    {item.preview.research_sources && (
                      <details className="settings">
                        <summary>Research sources</summary>
                        <pre className="raw-json">{JSON.stringify(item.preview.research_sources, null, 2)}</pre>
                      </details>
                    )}
                    <details className="settings">
                      <summary>Full research.json</summary>
                      <pre className="raw-json">{JSON.stringify(item.preview, null, 2)}</pre>
                    </details>
                  </div>
                )}

                {item.type === "video-test" && item.preview && (
                  <section className="video-probe-panel">
                    <h2>Video Test: {item.preview.query}</h2>
                    <p className="rationale">
                      Found {item.preview.videos_discovered} video embeds across {item.preview.listings_considered?.length || 0} matching listings.
                    </p>
                    <div className="video-probe-grid">
                      {(item.preview.clips || []).map((clip) => (
                        <article className="video-probe-card" key={clip.index}>
                          <h3>Candidate {clip.index}: {clip.source_title || "Listing video"}</h3>
                          {clip.thumbnail_url && (
                            <img className="video-probe-thumb" src={clip.thumbnail_url} alt={`Candidate ${clip.index} source thumbnail`} />
                          )}
                          {clip.clip ? (
                            <video controls src={dashboardRawUrl(item, clip.clip)} preload="metadata" />
                          ) : (
                            <p className="error">No usable clip: {clip.error || "verification rejected it"}</p>
                          )}
                          <dl>
                            <div><dt>Scene</dt><dd>{clip.scene_review?.scene_type?.replaceAll("_", " ") || "unknown"}</dd></div>
                            <div><dt>Detected event</dt><dd>{clip.detected_onset_seconds ?? "?"}s</dd></div>
                            <div><dt>Engine candidate</dt><dd>{clip.approved ? "Yes" : "No"}</dd></div>
                          </dl>
                          {clip.source_listing && (
                            <a href={clip.source_listing} target="_blank" rel="noreferrer">Open source listing</a>
                          )}
                        </article>
                      ))}
                    </div>
                    <details className="settings">
                      <summary>Full result.json</summary>
                      <pre className="raw-json">{JSON.stringify(item.preview, null, 2)}</pre>
                    </details>
                  </section>
                )}

                {!item.preview && <p className="hint">No JSON data found for this item (files: {item.files.join(", ")}).</p>}

                <div className="dashboard-card-actions">
                  {confirmDeleteId === key ? (
                    <>
                      <button type="button" className="danger" onClick={() => handleDeleteItem(item)} disabled={deletingId === key}>
                        {deletingId === key ? "Deleting..." : "Confirm delete"}
                      </button>
                      <button type="button" className="secondary" onClick={() => setConfirmDeleteId(null)}>Cancel</button>
                    </>
                  ) : (
                    <button type="button" className="secondary" onClick={() => setConfirmDeleteId(key)}>Delete this item</button>
                  )}
                </div>
              </div>
            );
          })()}
        </section>
      )}

      {view === "create" && (
      <>
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
        <div className="request-actions">
          <button onClick={handleResearch} disabled={!repoOk || !effectiveRequest || stage === "researching" || stage === "generating" || stage === "video-testing"}>
            {stage === "researching" ? "Researching..." : "Research"}
          </button>
          <button
            className="secondary"
            onClick={handleVideoTest}
            disabled={!repoOk || !make.trim() || !model.trim() || stage === "researching" || stage === "generating" || stage === "video-testing"}
          >
            {stage === "video-testing" ? "Testing Videos..." : "Test Videos Only"}
          </button>
        </div>
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
                  {(entry.images || []).map((img, j) => {
                    const review = (entry.image_reviews || []).find((item) => item.path === img);
                    const description = review?.view_description || review?.category || "verified car image";
                    return (
                      <a key={j} href={rawUrl(img)} target="_blank" rel="noreferrer" title={description}>
                        <img src={rawUrl(img)} alt={`${entry.name} — ${description}`} />
                        <span className="image-label">{description}</span>
                      </a>
                    );
                  })}
                </div>
                {entry.image_coverage && !entry.image_coverage.target_met && (
                  <p className="coverage-warning">
                    Limited coverage: {entry.image_coverage.approved_count}/{entry.image_coverage.target_count} preferred unique photos.
                  </p>
                )}
                <p className="hint">
                  Engine video candidates: {(entry.engine_videos || []).length}
                </p>
                {(entry.engine_videos || []).length > 0 && (
                  <article className="video-probe-card entry-engine-clip">
                    <h3>{entry.name} engine clip</h3>
                    {entry.engine_clip_preview?.source?.thumbnail_url && (
                      <img
                        className="video-probe-thumb"
                        src={entry.engine_clip_preview.source.thumbnail_url}
                        alt={`${entry.name} engine clip source thumbnail`}
                      />
                    )}
                    {entry.engine_clip_preview?.approved && entry.engine_clip_preview?.path ? (
                      <video controls src={rawUrl(entry.engine_clip_preview.path)} preload="metadata" />
                    ) : (
                      <p className="error">
                        No usable clip: {entry.engine_clip_preview?.error || "verification rejected it"}
                      </p>
                    )}
                    <dl>
                      <div><dt>Scene</dt><dd>{entry.engine_clip_preview?.scene_review?.scene_type?.replaceAll("_", " ") || "unknown"}</dd></div>
                      <div><dt>Detected event</dt><dd>{entry.engine_clip_preview?.detected_onset_seconds ?? "?"}s</dd></div>
                      <div><dt>Audio jump</dt><dd>{entry.engine_clip_preview?.engine_event_score !== null && entry.engine_clip_preview?.engine_event_score !== undefined ? `${entry.engine_clip_preview.engine_event_score}×` : "n/a"}</dd></div>
                      <div><dt>Engine candidate</dt><dd>{entry.engine_clip_preview?.approved ? "Yes" : "No"}</dd></div>
                    </dl>
                    {entry.engine_clip_preview?.scene_review?.reason && (
                      <p className="hint">{entry.engine_clip_preview.scene_review.reason}</p>
                    )}
                    {entry.engine_clip_preview?.source?.auction_url && (
                      <a href={entry.engine_clip_preview.source.auction_url} target="_blank" rel="noreferrer">
                        Open source listing
                      </a>
                    )}
                  </article>
                )}
              </div>
            ))}
          </div>
          <p className="close-line">Closing line: "{research.close_narration}"</p>

          <label>
            Narration voice
            <select value={voice} onChange={(event) => setVoice(event.target.value)} disabled={stage === "generating"}>
              {VOICES.map((option) => <option key={option} value={option}>{titleCaseWords(option)}</option>)}
            </select>
          </label>
          <div className="render-actions">
            <button
              className="generate-btn"
              onClick={() => handleGenerate("quick")}
              disabled={stage === "generating" || research.entries.some((entry) => !(entry.images || []).length)}
            >
              {stage === "generating" && renderQuality === "quick" ? "Rendering Quick Preview..." : "Quick Preview"}
            </button>
            <button
              className="generate-btn secondary"
              onClick={() => handleGenerate("full")}
              disabled={stage === "generating" || research.entries.some((entry) => !(entry.images || []).length)}
            >
              {stage === "generating" && renderQuality === "full" ? "Rendering Full Quality..." : "Full Quality Render"}
            </button>
          </div>
          <p className="hint">
            Both modes use the same approved photos, script, Onyx narration, and verified cold-start clips when available.
          </p>
          {research.entries.some((entry) => !(entry.images || []).length) && (
            <p className="hint">Can&apos;t generate - at least one entry has no images. Try a different request.</p>
          )}
        </div>
      )}

      {stage === "generating" && <p className="status">Rendering the video. This can take several minutes...</p>}

      {videoUrl && (
        <div className="video-panel">
          <h2>Done</h2>
          <div className="video-result">
            <div className="video-player">
              <video controls src={videoUrl} width="360" />
              <p>
                <a href={videoUrl} target="_blank" rel="noreferrer">Open video directly</a>
              </p>
            </div>
            {research && (
              <section className="narration-box" aria-labelledby="narration-title">
                <h3 id="narration-title">Narration</h3>
                {(research.engine_clips || []).length > 0 && (
                  <p className="hint">
                    Engine clips inserted: {(research.engine_clips || []).filter((clip) => clip.approved).length}
                  </p>
                )}
                <div className="narration-scroll">
                  {research.entries.map((entry, index) => (
                    <div className="narration-entry" key={entry.name}>
                      <strong>#{4 - index} {entry.name}</strong>
                      <p>{entry.narration || entry.one_line_fact}</p>
                    </div>
                  ))}
                  <div className="narration-entry narration-close">
                    <strong>Closing line</strong>
                    <p>{research.close_narration}</p>
                  </div>
                </div>
              </section>
            )}
          </div>
        </div>
      )}
      {stage === "video-testing" && (
        <p className="status">Discovering listing videos, detecting engine events, and cutting short previews...</p>
      )}

      {videoProbe && (
        <section className="video-probe-panel">
          <h2>Video Test: {videoProbe.query}</h2>
          <p className="rationale">
            Found {videoProbe.videos_discovered} video embeds across {videoProbe.listings_considered?.length || 0} matching listings.
            {" "}Approved {(videoProbe.clips || []).length} clip{(videoProbe.clips || []).length === 1 ? "" : "s"} after checking {videoProbe.attempts_made ?? (videoProbe.clips || []).length} listing video{(videoProbe.attempts_made ?? 0) === 1 ? "" : "s"}
            {videoProbe.listings_with_video_attempted ? ` across ${videoProbe.listings_with_video_attempted} listings` : ""}.
          </p>
          <div className="video-probe-grid">
            {(videoProbe.clips || []).map((clip) => (
              <article className="video-probe-card" key={clip.index}>
                <h3>Candidate {clip.index}: {clip.source_title || "Listing video"}</h3>
                {clip.thumbnail_url && (
                  <img className="video-probe-thumb" src={clip.thumbnail_url} alt={`Candidate ${clip.index} source thumbnail`} />
                )}
                {clip.clip ? (
                  <video controls src={rawVideoTestUrl(clip.clip)} preload="metadata" />
                ) : (
                  <p className="error">No usable clip: {clip.error || "verification rejected it"}</p>
                )}
                <dl>
                  <div><dt>Scene</dt><dd>{clip.scene_review?.scene_type?.replaceAll("_", " ") || "unknown"}</dd></div>
                  <div><dt>Detected event</dt><dd>{clip.detected_onset_seconds ?? "?"}s</dd></div>
                  <div><dt>Audio jump</dt><dd>{clip.engine_event_score !== null && clip.engine_event_score !== undefined ? `${clip.engine_event_score}×` : "n/a"}</dd></div>
                  <div><dt>Engine candidate</dt><dd>{clip.approved ? "Yes" : "No"}</dd></div>
                </dl>
                {clip.scene_review?.reason && <p className="hint">{clip.scene_review.reason}</p>}
                {clip.source_listing && (
                  <a href={clip.source_listing} target="_blank" rel="noreferrer">Open source listing</a>
                )}
              </article>
            ))}
          </div>
          {(videoProbe.clips || []).length === 0 && (
            <p>
              {videoProbe.videos_discovered
                ? `No engine-relevant clip was approved out of ${videoProbe.attempts_made ?? videoProbe.videos_discovered} listing videos checked.`
                : "No embedded listing videos were discovered."}
            </p>
          )}
        </section>
      )}
      </>
      )}
    </div>
  );
}
