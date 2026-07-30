import { useEffect, useRef, useState } from "react";
import "./App.css";

const DEFAULT_OWNER = "Lukaa98";
const DEFAULT_REPO = "AI-horror-stories";
const DEFAULT_BRANCH = "v10";
const OUTPUT_BRANCH = "cars-output";
const UI_VERSION = "V10.47";
const VOICES = ["marin", "cedar", "coral", "verse", "onyx"];
const SETTINGS_MIGRATION = "default-branch-v10";
const PROGRESS_STEPS = ["Research", "Review", "Render", "Complete"];
const RESEARCH_TIMEOUT_MS = 60 * 60 * 1000;
const RENDER_TIMEOUT_MS = 30 * 60 * 1000;
const VIDEO_TEST_TIMEOUT_MS = 60 * 60 * 1000;
const BATTLE_RESEARCH_TIMEOUT_MS = 60 * 60 * 1000;
const BATTLE_RENDER_TIMEOUT_MS = 20 * 60 * 1000;
const RIVAL_SUGGEST_TIMEOUT_MS = 5 * 60 * 1000;
const MIN_BATTLE_CARS = 3;
const MAX_BATTLE_CARS = 5;
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
  {
    id: "battle",
    label: "Startup Sound Battle",
    description: "You name 3-5 specific cars; we find each one's exterior-only cold-start clip and cut them together back to back, numbered, no narration.",
  },
];

// Curated instead of API-driven: live make/model APIs either return
// thousands of irrelevant entries (vPIC) or have dead endpoints for trims
// (CarQuery). This covers mainstream muscle/sports cars plus the enthusiast
// exotics people actually put in a startup battle. "Other (type manually)"
// on every level is the escape hatch for anything not listed here.
const OTHER_VALUE = "__other__";
const ENTHUSIAST_CARS = {
  Ford: {
    Mustang: ["V6", "EcoBoost", "GT", "Mach 1", "Bullitt", "Boss 302", "GT350", "GT350R", "Shelby GT500"],
    Focus: ["ST", "RS"],
    "Ford GT": [],
  },
  Chevrolet: {
    Camaro: ["LT1", "SS", "1LE", "ZL1", "ZL1 1LE", "Z/28"],
    Corvette: ["Stingray", "Z51", "Grand Sport", "Z06", "ZR1"],
  },
  Dodge: {
    Challenger: ["SXT", "R/T", "R/T Scat Pack", "SRT 392", "SRT Hellcat", "SRT Hellcat Redeye", "SRT Demon", "SRT Demon 170", "SRT Super Stock"],
    Charger: ["R/T", "Scat Pack", "SRT Hellcat", "SRT Hellcat Redeye"],
    Viper: ["SRT", "GTS", "ACR", "GTC"],
  },
  Porsche: {
    "911": ["Carrera", "Carrera S", "Carrera 4S", "Targa", "Turbo", "Turbo S", "GT3", "GT3 RS", "GT2 RS", "Speedster", "Sport Classic"],
    Cayman: ["S", "GTS", "GT4", "GT4 RS"],
    Boxster: ["S", "GTS", "Spyder"],
  },
  Ferrari: {
    "458": ["Italia", "Spider", "Speciale", "Speciale Aperta"],
    "488": ["GTB", "Spider", "Pista", "Pista Spider"],
    F8: ["Tributo", "Spider"],
    "812": ["Superfast", "GTS", "Competizione"],
    Roma: [],
    Portofino: [],
    SF90: ["Stradale", "Spider"],
    "296": ["GTB", "GTS"],
  },
  Lamborghini: {
    Huracan: ["LP580-2", "LP610-4", "Performante", "EVO", "EVO Spyder", "STO", "Tecnica"],
    Aventador: ["LP700-4", "SV", "SVJ", "Ultimae"],
    Gallardo: ["LP560-4", "Superleggera", "LP570-4"],
    Urus: ["S", "Performante"],
  },
  Nissan: {
    "GT-R": ["Premium", "Track Edition", "Nismo", "Black Edition", "T-spec"],
    "370Z": ["Base", "Touring", "Nismo"],
    Z: ["Sport", "Performance", "Nismo"],
  },
  Toyota: {
    Supra: ["2.0", "3.0", "3.0 Premium", "A90 Edition"],
    GR86: ["Base", "Premium"],
    "GR Corolla": ["Core", "Circuit", "Morizo"],
  },
  BMW: {
    M2: ["Base", "Competition", "CS"],
    M3: ["Base", "Competition", "CS", "GTS"],
    M4: ["Base", "Competition", "CS", "CSL"],
    M5: ["Base", "Competition", "CS"],
  },
  "Mercedes-Benz": {
    "AMG GT": ["Base", "S", "R", "C", "Black Series"],
    "C63 AMG": ["S", "Black Series"],
    "E63 AMG": ["S"],
  },
  Audi: {
    R8: ["V8", "V10", "V10 Plus", "V10 Performance", "V10 Decennium"],
    RS3: [],
    RS6: [],
    RS7: [],
    "TT RS": [],
  },
  Acura: { NSX: ["Base", "Type S"] },
  Subaru: { "WRX STI": [], BRZ: ["Base", "tS"] },
  Mazda: { "MX-5 Miata": ["Base", "Club", "RF"], "RX-7": ["Base", "Turbo"] },
  Honda: { "Civic Type R": [], S2000: [] },
  Mitsubishi: { "Lancer Evolution": ["VIII", "IX", "X", "X Final Edition"] },
  McLaren: { "570S": [], "720S": [], "765LT": [], Artura: [] },
  "Aston Martin": { Vantage: [], DB11: [], DBS: [] },
};

function makeBattleCarRow() {
  return { make: "", model: "", trim: "", year: "", makeCustom: "", modelCustom: "", trimCustom: "" };
}

function battleModelOptions(car) {
  return ENTHUSIAST_CARS[car.make] ? Object.keys(ENTHUSIAST_CARS[car.make]) : [];
}

function battleTrimOptions(car) {
  return (ENTHUSIAST_CARS[car.make] && ENTHUSIAST_CARS[car.make][car.model]) || [];
}

function resolveBattleCarField(selected, custom) {
  return (selected === OTHER_VALUE ? custom : selected).trim();
}

function resolveBattleCar(car) {
  return {
    make: resolveBattleCarField(car.make, car.makeCustom),
    model: resolveBattleCarField(car.model, car.modelCustom),
    trim: resolveBattleCarField(car.trim, car.trimCustom),
    year: car.year.trim(),
  };
}

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

const RIVAL_SUGGESTIONS_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["rivals"],
  properties: {
    rivals: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["make", "model", "trim", "year", "reason"],
        properties: {
          make: { type: "string" },
          model: { type: "string" },
          trim: { type: "string" },
          year: { type: "string" },
          reason: { type: "string" },
        },
      },
    },
  },
};

function extractResponseText(data) {
  if (typeof data.output_text === "string") return data.output_text;
  const parts = [];
  for (const item of data.output || []) {
    for (const content of item.content || []) {
      if (content.type === "output_text" && content.text) parts.push(content.text);
    }
  }
  return parts.join("");
}

// Direct browser -> OpenAI call, so suggestions come back in a couple
// seconds instead of paying for a GitHub Actions runner cold-start just to
// make one API call. Requires the user's own OpenAI key (stored the same
// way the GitHub token is, in localStorage) rather than the repo secret.
async function fetchRivalsDirect({ apiKey, make, model, trim, year, count }) {
  const baseLabel = `${year} ${make} ${model} ${trim}`.trim();
  const prompt = (
    `Suggest ${count} rival/competitor cars for a head-to-head cold-start-sound comparison video ` +
    `against the ${baseLabel}. Pick cars from roughly the same era (within a few model years), a ` +
    "similar price bracket, and a similar performance/segment -- genuine rivals a car enthusiast " +
    "would cross-shop or compare, not random unrelated cars. Prefer cars with well-known, distinct " +
    "exhaust notes or cold-start sounds, and avoid suggesting the same make and model as the base " +
    "car. For each rival return make, model, trim (the best-known trim/variant for that " +
    "price/performance tier, empty string if not applicable), year (a specific model year, not a " +
    "range), and a one-sentence reason it is a fair rival to the base car."
  );
  const res = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "gpt-4o-mini",
      input: prompt,
      text: { format: { type: "json_schema", name: "rival_suggestions", strict: true, schema: RIVAL_SUGGESTIONS_SCHEMA } },
    }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`OpenAI request failed (${res.status}): ${body.slice(0, 300)}`);
  }
  const data = await res.json();
  const parsed = JSON.parse(extractResponseText(data));
  return (parsed.rivals || []).slice(0, count);
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

const DASHBOARD_FOLDERS = {
  draft: { prefix: "cars/drafts", file: "research.json" },
  "video-test": { prefix: "cars/video-tests", file: "result.json" },
  battle: { prefix: "cars/battles", file: "battle.json" },
};

function groupDashboardEntries(entries) {
  const drafts = new Map();
  const tests = new Map();
  const battles = new Map();
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
      continue;
    }
    m = item.path.match(/^cars\/battles\/([^/]+)\/(.+)$/);
    if (m) {
      const [, id, rest] = m;
      if (id === "_frames") continue;
      if (!battles.has(id)) battles.set(id, []);
      battles.get(id).push(rest);
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
  for (const [id, files] of battles) {
    items.push({
      type: "battle",
      id,
      files,
      timestamp: parseIdTimestamp(id),
      hasBattle: files.includes("battle.json"),
      hasVideo: files.includes("battle_short.mp4"),
    });
  }
  items.sort((a, b) => (b.timestamp?.getTime() || 0) - (a.timestamp?.getTime() || 0));
  return items;
}

async function attachDashboardPreviews(items, owner, repo) {
  await Promise.allSettled(
    items.map(async (item) => {
      const { prefix, file } = DASHBOARD_FOLDERS[item.type];
      if (item.type === "draft" && !item.hasResearch) return;
      if (item.type === "video-test" && !item.hasResult) return;
      if (item.type === "battle" && !item.hasBattle) return;
      const res = await fetch(
        `https://raw.githubusercontent.com/${owner}/${repo}/${OUTPUT_BRANCH}/${prefix}/${item.id}/${file}?_=${Date.now()}`,
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

function dashboardFolderName(type) {
  if (type === "draft") return "drafts";
  if (type === "battle") return "battles";
  return "video-tests";
}

async function deleteDashboardItem({ owner, repo, token, type, id }) {
  const prefix = `cars/${dashboardFolderName(type)}/${id}/`;
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
    openaiKey: "",
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
  const [battleCars, setBattleCars] = useState(() => [makeBattleCarRow(), makeBattleCarRow(), makeBattleCarRow()]);
  const [rivalSuggestions, setRivalSuggestions] = useState(null);
  const [rivalSuggestLoading, setRivalSuggestLoading] = useState(false);
  const [rivalSuggestError, setRivalSuggestError] = useState(null);
  const [addedRivalIndexes, setAddedRivalIndexes] = useState(() => new Set());
  const [battleId, setBattleId] = useState(null);
  const [battle, setBattle] = useState(null);
  const [battleVideoUrl, setBattleVideoUrl] = useState(null);
  const [view, setView] = useState("create");
  const [dashboardItems, setDashboardItems] = useState([]);
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [dashboardError, setDashboardError] = useState(null);
  const [selectedItem, setSelectedItem] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  const [dashboardRenderingId, setDashboardRenderingId] = useState(null);
  const dashboardAbortRef = useRef(null);
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

  function battleRawUrl(relativePath) {
    return `https://raw.githubusercontent.com/${settings.owner}/${settings.repo}/${OUTPUT_BRANCH}/cars/battles/${battleId}/${relativePath}`;
  }

  function updateBattleCar(index, field, value) {
    setBattleCars((prev) => prev.map((car, i) => {
      if (i !== index) return car;
      if (field === "make") return { ...car, make: value, makeCustom: "", model: "", modelCustom: "", trim: "", trimCustom: "" };
      if (field === "model") return { ...car, model: value, modelCustom: "", trim: "", trimCustom: "" };
      if (field === "trim") return { ...car, trim: value, trimCustom: "" };
      return { ...car, [field]: value };
    }));
  }

  function addBattleCar() {
    setBattleCars((prev) => (prev.length >= MAX_BATTLE_CARS ? prev : [...prev, makeBattleCarRow()]));
  }

  function removeBattleCar(index) {
    setBattleCars((prev) => (prev.length <= MIN_BATTLE_CARS ? prev : prev.filter((_, i) => i !== index)));
  }

  const battleCarsValid = battleCars.every((car) => {
    const resolved = resolveBattleCar(car);
    return resolved.make && resolved.model && resolved.year;
  });

  const baseCar = resolveBattleCar(battleCars[0]);
  const baseCarValid = Boolean(baseCar.make && baseCar.model && baseCar.year);

  async function handleSuggestRivals() {
    if (!baseCarValid) return;
    setRivalSuggestError(null);
    setRivalSuggestLoading(true);
    setRivalSuggestions(null);
    setAddedRivalIndexes(new Set());
    const count = Math.max(1, MAX_BATTLE_CARS - 1);

    if (settings.openaiKey) {
      try {
        const rivals = await fetchRivalsDirect({
          apiKey: settings.openaiKey,
          make: baseCar.make,
          model: baseCar.model,
          trim: baseCar.trim,
          year: baseCar.year,
          count,
        });
        setRivalSuggestions(rivals);
      } catch (err) {
        setRivalSuggestError(String(err.message || err));
      } finally {
        setRivalSuggestLoading(false);
      }
      return;
    }

    if (!repoOk) {
      setRivalSuggestError("Fill in your GitHub token + repo settings, or add an OpenAI API key for instant suggestions.");
      setRivalSuggestLoading(false);
      return;
    }
    const id = makeDraftId(`rivals-${baseCar.year}-${baseCar.make}-${baseCar.model}`);
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
          request: `Suggest rivals for ${baseCar.year} ${baseCar.make} ${baseCar.model} ${baseCar.trim}`.trim(),
          draft_id: id,
          mode: "suggest_rivals",
          base_make: baseCar.make,
          base_model: baseCar.model,
          base_trim: baseCar.trim,
          base_year: baseCar.year,
          rival_count: String(count),
        },
      });
      const workflowRun = beginRunTracking("cars-research.yml", startedAt, abortRef.current.signal);
      const suggestionFile = pollForFile({
        owner: settings.owner,
        repo: settings.repo,
        branch: OUTPUT_BRANCH,
        path: `cars/rival-suggestions/${id}/suggestions.json`,
        signal: abortRef.current.signal,
        timeoutMs: RIVAL_SUGGEST_TIMEOUT_MS,
      });
      const res = await Promise.race([suggestionFile, workflowRun.then(() => suggestionFile)]);
      const data = await res.json();
      setRivalSuggestions(data.rivals || []);
    } catch (err) {
      setRivalSuggestError(String(err.message || err));
    } finally {
      setRivalSuggestLoading(false);
    }
  }

  function addRivalAsBattleCar(rival, rivalIndex) {
    const filled = {
      make: OTHER_VALUE,
      makeCustom: rival.make || "",
      model: OTHER_VALUE,
      modelCustom: rival.model || "",
      trim: rival.trim ? OTHER_VALUE : "",
      trimCustom: rival.trim || "",
      year: rival.year || "",
    };
    setBattleCars((prev) => {
      const emptyIndex = prev.findIndex((car, i) => i > 0 && !resolveBattleCar(car).make);
      if (emptyIndex !== -1) {
        return prev.map((car, i) => (i === emptyIndex ? { ...car, ...filled } : car));
      }
      if (prev.length >= MAX_BATTLE_CARS) return prev;
      return [...prev, { ...makeBattleCarRow(), ...filled }];
    });
    setAddedRivalIndexes((prev) => new Set(prev).add(rivalIndex));
  }

  async function handleBattleResearch() {
    if (!repoOk || !battleCarsValid) return;
    setError(null);
    setBattle(null);
    setBattleVideoUrl(null);
    const carsList = battleCars.map(resolveBattleCar);
    const summary = carsList.map((car) => `${car.year} ${car.make} ${[car.model, car.trim].filter(Boolean).join(" ")}`).join(", ");
    const id = makeDraftId(`battle-${summary}`);
    setBattleId(id);
    setStage("researching");
    setStatusDetail(`Finding exterior cold-start clips for: ${summary}...`);
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
          request: `Startup sound battle: ${summary}`,
          draft_id: id,
          mode: "battle",
          cars: JSON.stringify(carsList),
        },
      });
      const workflowRun = beginRunTracking("cars-research.yml", startedAt, abortRef.current.signal);
      const battleFile = pollForFile({
        owner: settings.owner,
        repo: settings.repo,
        branch: OUTPUT_BRANCH,
        path: `cars/battles/${id}/battle.json`,
        signal: abortRef.current.signal,
        timeoutMs: BATTLE_RESEARCH_TIMEOUT_MS,
      });
      const res = await Promise.race([battleFile, workflowRun.then(() => battleFile)]);
      const data = await res.json();
      setBattle(data);
      setStage("researched");
      setStatusDetail(`Found clips for ${data.approved_count}/${data.total_count} cars`);
    } catch (err) {
      setError(String(err.message || err));
      setStage("error");
      setStatusDetail("Battle research failed - check the error below and try again");
    }
  }

  async function handleBattleRender() {
    if (!battleId) return;
    setError(null);
    setStage("generating");
    setStatusDetail("Dispatching the battle render workflow...");
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
          draft_id: battleId,
          mode: "battle",
          tts_provider: "openai",
          tts_voice: voice,
          render_quality: "full",
        },
      });
      const workflowRun = beginRunTracking("cars-generate-from-research.yml", startedAt, abortRef.current.signal);
      setStatusDetail("Rendering the battle video...");
      await workflowRun;
      await pollForFile({
        owner: settings.owner,
        repo: settings.repo,
        branch: OUTPUT_BRANCH,
        path: `cars/battles/${battleId}/battle_short.mp4`,
        signal: abortRef.current.signal,
        timeoutMs: BATTLE_RENDER_TIMEOUT_MS,
      });
      setBattleVideoUrl(
        `https://raw.githubusercontent.com/${settings.owner}/${settings.repo}/${OUTPUT_BRANCH}/cars/battles/${battleId}/battle_short.mp4?_=${Date.now()}`
      );
      setStage("done");
      setStatusDetail("Battle video complete");
    } catch (err) {
      setError(String(err.message || err));
      setStage("error");
      setStatusDetail("Battle render failed - check the error below and try again");
    }
  }

  function rawVideoTestUrl(relativePath) {
    return `https://raw.githubusercontent.com/${settings.owner}/${settings.repo}/${OUTPUT_BRANCH}/cars/video-tests/${videoTestId}/${relativePath}`;
  }

  function dashboardRawUrl(item, relativePath) {
    return `https://raw.githubusercontent.com/${settings.owner}/${settings.repo}/${OUTPUT_BRANCH}/cars/${dashboardFolderName(item.type)}/${item.id}/${relativePath}`;
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

  async function handleDashboardBattleRender(item) {
    if (!repoOk) return;
    const key = `${item.type}:${item.id}`;
    setDashboardRenderingId(key);
    setDashboardError(null);
    dashboardAbortRef.current = new AbortController();
    try {
      const startedAt = Date.now();
      await dispatchWorkflow({
        owner: settings.owner,
        repo: settings.repo,
        branch: settings.branch,
        token: settings.token,
        workflow: "cars-generate-from-research.yml",
        inputs: {
          draft_id: item.id,
          mode: "battle",
          tts_provider: "openai",
          tts_voice: "onyx",
          render_quality: "full",
        },
      });
      const workflowRun = trackWorkflowRun({
        owner: settings.owner,
        repo: settings.repo,
        branch: settings.branch,
        token: settings.token,
        workflow: "cars-generate-from-research.yml",
        startedAt,
        signal: dashboardAbortRef.current.signal,
        onUpdate: () => {},
      });
      const videoFile = pollForFile({
        owner: settings.owner,
        repo: settings.repo,
        branch: OUTPUT_BRANCH,
        path: `cars/battles/${item.id}/battle_short.mp4`,
        signal: dashboardAbortRef.current.signal,
        timeoutMs: BATTLE_RENDER_TIMEOUT_MS,
      });
      await Promise.race([videoFile, workflowRun.then(() => videoFile)]);
      setDashboardItems((prev) => prev.map((entry) => (
        entry.type === item.type && entry.id === item.id ? { ...entry, hasVideo: true } : entry
      )));
    } catch (err) {
      setDashboardError(String(err.message || err));
    } finally {
      setDashboardRenderingId(null);
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
              <h2>Generated Drafts, Video Tests &amp; Battles</h2>
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
                  item.type === "draft" ? item.preview?.title || item.id
                  : item.type === "battle" ? item.preview?.title || `Battle: ${item.id}`
                  : `Video test: ${item.preview?.query || item.id}`;
                const thumb =
                  item.type === "draft" ? item.preview?.entries?.find((entry) => (entry.images || []).length)?.images?.[0]
                  : item.type === "battle" ? item.preview?.cars?.find((car) => (car.photos || []).length)?.photos?.[0]
                  : item.preview?.clips?.find((clip) => clip.thumbnail_url)?.thumbnail_url;
                const thumbUrl = item.type === "video-test" ? thumb : (thumb ? dashboardRawUrl(item, thumb) : null);
                const typeLabel = item.type === "draft" ? "Draft" : item.type === "battle" ? "Battle" : "Video Test";
                return (
                  <article className="dashboard-card" key={key}>
                    {thumbUrl && <img src={thumbUrl} alt={title} />}
                    <div className="dashboard-card-body">
                      <span className={`dashboard-type ${item.type}`}>{typeLabel}</span>
                      <h3>{title}</h3>
                      <p className="hint">{item.timestamp ? item.timestamp.toLocaleString() : item.id}</p>
                      {item.type === "draft" && (
                        <p className="hint">
                          {item.hasFinal ? "Full render ready" : item.hasPreview ? "Preview render ready" : "No render yet"}
                        </p>
                      )}
                      {item.type === "battle" && (
                        <p className="hint">
                          {item.hasVideo ? "Battle video ready" : `${item.preview?.approved_count ?? "?"}/${item.preview?.total_count ?? "?"} clips found`}
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

                {item.type === "battle" && item.preview && (
                  <div className="research-panel">
                    <h2>{item.preview.title}</h2>
                    <p className="rationale">
                      Found usable exterior startup clips for {item.preview.approved_count} of {item.preview.total_count} cars.
                    </p>
                    {item.hasVideo ? (
                      <div className="video-player">
                        <video controls src={dashboardRawUrl(item, "battle_short.mp4")} width="360" />
                      </div>
                    ) : (
                      <div className="render-actions">
                        <button
                          type="button"
                          className="generate-btn"
                          onClick={() => handleDashboardBattleRender(item)}
                          disabled={dashboardRenderingId === key || item.preview.approved_count < 2}
                        >
                          {dashboardRenderingId === key ? "Rendering..." : "Generate Full Battle Video"}
                        </button>
                        {item.preview.approved_count < 2 && (
                          <p className="hint">Need at least 2 approved cars to render.</p>
                        )}
                      </div>
                    )}
                    <div className="entries">
                      {(item.preview.cars || []).map((car) => (
                        <div key={car.index} className="entry-card">
                          <div className="entry-rank">#{car.index}</div>
                          <h3>{car.label}</h3>
                          <p className="label">{car.generation_label || `Generation range ${car.generation_start}-${car.generation_end}`}</p>
                          <div className="thumbs">
                            {(car.photos || []).length === 0 && <span className="no-images">no exterior photos found</span>}
                            {(car.photos || []).map((img, j) => (
                              <a key={j} href={dashboardRawUrl(item, img)} target="_blank" rel="noreferrer">
                                <img src={dashboardRawUrl(item, img)} alt={`${car.label} exterior`} />
                              </a>
                            ))}
                          </div>
                          {car.fallback_applied && (
                            <p className="hint">
                              Requested trim &quot;{car.trim_requested}&quot; not found; used &quot;{car.trim_used || "base model"}&quot; instead.
                            </p>
                          )}
                          {car.approved && car.clip_path ? (
                            <article className="video-probe-card entry-engine-clip">
                              <video controls src={dashboardRawUrl(item, car.clip_path)} preload="metadata" />
                              <p className="hint">Clip length: {car.clip_duration}s</p>
                              <p className="hint">
                                {car.rev_detected
                                  ? `Revving detected: peak ~${car.rev_events[0].peak_hz}Hz over baseline ~${car.rev_events[0].baseline_hz}Hz`
                                  : "No revving pattern detected (best-effort pitch check; may miss broadband exhaust notes)"}
                              </p>
                            </article>
                          ) : (
                            <>
                              <p className="error">No usable exterior startup clip: {car.error || "rejected"}</p>
                              {(car.listings_considered || []).length > 0 && (
                                <p className="hint">
                                  Listings checked:{" "}
                                  {car.listings_considered.map((url, j) => (
                                    <span key={url}>
                                      {j > 0 && ", "}
                                      <a href={url} target="_blank" rel="noreferrer">#{j + 1}</a>
                                    </span>
                                  ))}
                                </p>
                              )}
                            </>
                          )}
                        </div>
                      ))}
                    </div>
                    <details className="settings">
                      <summary>Full battle.json</summary>
                      <pre className="raw-json">{JSON.stringify(item.preview, null, 2)}</pre>
                    </details>
                  </div>
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
          <label>
            OpenAI API Key (optional)
            <input
              type="password"
              placeholder="sk-..."
              value={settings.openaiKey}
              onChange={(e) => setSettings({ ...settings, openaiKey: e.target.value })}
            />
          </label>
        </div>
        <p className="hint">
          Token needs "repo" + "workflow" scope (fine-grained: Contents + Actions read/write on this repo only).
          Stored only in this browser&apos;s localStorage.
        </p>
        <p className="hint">
          The OpenAI key is only used for "Suggest rivals" in Startup Sound Battle, called directly from this
          browser so suggestions come back in seconds instead of waiting on a GitHub Actions run. It is stored in
          this browser&apos;s localStorage like the token above -- leave it blank to fall back to the (slower)
          workflow-based suggestion instead.
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

          {workflow === "battle" ? (
            <div className="battle-cars">
              {battleCars.map((car, index) => {
                const disabled = stage === "researching" || stage === "generating";
                const modelOptions = battleModelOptions(car);
                const trimOptions = battleTrimOptions(car);
                return (
                  <div className="battle-car-row" key={index}>
                    <label>
                      Make
                      <select value={car.make} onChange={(e) => updateBattleCar(index, "make", e.target.value)} disabled={disabled}>
                        <option value="">Choose make</option>
                        {Object.keys(ENTHUSIAST_CARS).map((name) => <option key={name} value={name}>{name}</option>)}
                        <option value={OTHER_VALUE}>Other (type manually)</option>
                      </select>
                      {car.make === OTHER_VALUE && (
                        <input
                          value={car.makeCustom}
                          onChange={(e) => updateBattleCar(index, "makeCustom", e.target.value)}
                          placeholder="Make"
                          disabled={disabled}
                        />
                      )}
                    </label>
                    <label>
                      Model
                      {car.make && car.make !== OTHER_VALUE ? (
                        <>
                          <select
                            value={car.model}
                            onChange={(e) => updateBattleCar(index, "model", e.target.value)}
                            disabled={disabled}
                          >
                            <option value="">Choose model</option>
                            {modelOptions.map((name) => <option key={name} value={name}>{name}</option>)}
                            <option value={OTHER_VALUE}>Other (type manually)</option>
                          </select>
                          {car.model === OTHER_VALUE && (
                            <input
                              value={car.modelCustom}
                              onChange={(e) => updateBattleCar(index, "modelCustom", e.target.value)}
                              placeholder="Model"
                              disabled={disabled}
                            />
                          )}
                        </>
                      ) : (
                        <input
                          value={car.modelCustom}
                          onChange={(e) => updateBattleCar(index, "modelCustom", e.target.value)}
                          placeholder="Model"
                          disabled={disabled || !car.make}
                        />
                      )}
                    </label>
                    <label>
                      Trim
                      {car.model && car.model !== OTHER_VALUE && trimOptions.length > 0 ? (
                        <>
                          <select
                            value={car.trim}
                            onChange={(e) => updateBattleCar(index, "trim", e.target.value)}
                            disabled={disabled}
                          >
                            <option value="">Any / base</option>
                            {trimOptions.map((name) => <option key={name} value={name}>{name}</option>)}
                            <option value={OTHER_VALUE}>Other (type manually)</option>
                          </select>
                          {car.trim === OTHER_VALUE && (
                            <input
                              value={car.trimCustom}
                              onChange={(e) => updateBattleCar(index, "trimCustom", e.target.value)}
                              placeholder="Trim"
                              disabled={disabled}
                            />
                          )}
                        </>
                      ) : (
                        <input
                          value={car.trimCustom}
                          onChange={(e) => updateBattleCar(index, "trimCustom", e.target.value)}
                          placeholder="GT350 (optional)"
                          disabled={disabled}
                        />
                      )}
                    </label>
                    <label>
                      Year
                      <select
                        value={car.year}
                        onChange={(e) => updateBattleCar(index, "year", e.target.value)}
                        disabled={disabled}
                      >
                        <option value="">Year</option>
                        {YEAR_OPTIONS.map((year) => <option key={year} value={year}>{year}</option>)}
                      </select>
                    </label>
                    <button
                      type="button"
                      className="secondary battle-car-remove"
                      onClick={() => removeBattleCar(index)}
                      disabled={battleCars.length <= MIN_BATTLE_CARS || disabled}
                    >
                      Remove
                    </button>
                  </div>
                );
              })}
              <div className="battle-cars-buttons">
                <button
                  type="button"
                  className="secondary"
                  onClick={addBattleCar}
                  disabled={battleCars.length >= MAX_BATTLE_CARS || stage === "researching" || stage === "generating"}
                >
                  + Add another car
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={handleSuggestRivals}
                  disabled={(!repoOk && !settings.openaiKey) || !baseCarValid || rivalSuggestLoading || stage === "researching" || stage === "generating"}
                >
                  {rivalSuggestLoading ? "Asking AI for rivals..." : "Suggest rivals for car #1"}
                </button>
              </div>
              {rivalSuggestError && <div className="error">{rivalSuggestError}</div>}
              {rivalSuggestions && (
                <div className="rival-suggestions">
                  <p className="hint">
                    AI-suggested rivals for {baseCar.year} {baseCar.make} {baseCar.model} {baseCar.trim}. Click one to
                    drop it into an open car slot below.
                  </p>
                  <div className="rival-grid">
                    {rivalSuggestions.map((rival, index) => (
                      <div className="rival-card" key={index}>
                        <strong>{rival.year} {rival.make} {rival.model} {rival.trim}</strong>
                        <p className="hint">{rival.reason}</p>
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => addRivalAsBattleCar(rival, index)}
                          disabled={addedRivalIndexes.has(index) || battleCars.length >= MAX_BATTLE_CARS}
                        >
                          {addedRivalIndexes.has(index) ? "Added" : "Add to battle"}
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <p className="hint">
                Make/Model/Trim are curated (mainstream muscle + sports cars plus common exotics). Pick "Other" at
                any level to type something not listed. If an exact trim like GT4 RS isn't found on Cars &amp; Bids,
                the search automatically broadens (GT4 RS &rarr; GT4 &rarr; base Cayman) until it finds a listing.
              </p>
            </div>
          ) : (
            <>
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
            </>
          )}
        </div>
        <div className="request-actions">
          {workflow === "battle" ? (
            <button
              onClick={handleBattleResearch}
              disabled={!repoOk || !battleCarsValid || stage === "researching" || stage === "generating" || stage === "video-testing"}
            >
              {stage === "researching" ? "Finding Clips..." : "Find Startup Clips"}
            </button>
          ) : (
            <>
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
            </>
          )}
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {stage === "researching" && workflow === "battle" && (
        <p className="status">Searching each car's generation for an exterior-filmed cold-start clip and exterior photos. This can take a few minutes...</p>
      )}
      {stage === "researching" && workflow !== "battle" && (
        <p className="status">AI is researching facts and gathering varied, verified model photos. This can take a few minutes...</p>
      )}

      {battle && (
        <div className="research-panel">
          <h2>{battle.title}</h2>
          <p className="rationale">
            Found usable exterior startup clips for {battle.approved_count} of {battle.total_count} cars.
          </p>
          <div className="entries">
            {battle.cars.map((car) => (
              <div key={car.index} className="entry-card">
                <div className="entry-rank">#{car.index}</div>
                <h3>{car.label}</h3>
                <p className="label">{car.generation_label || `Generation range ${car.generation_start}-${car.generation_end}`}</p>
                <div className="thumbs">
                  {(car.photos || []).length === 0 && <span className="no-images">no exterior photos found</span>}
                  {(car.photos || []).map((img, j) => (
                    <a key={j} href={battleRawUrl(img)} target="_blank" rel="noreferrer">
                      <img src={battleRawUrl(img)} alt={`${car.label} exterior`} />
                    </a>
                  ))}
                </div>
                {car.fallback_applied && (
                  <p className="hint">
                    Requested trim &quot;{car.trim_requested}&quot; not found; used &quot;{car.trim_used || "base model"}&quot; instead.
                  </p>
                )}
                {car.approved && car.clip_path ? (
                  <article className="video-probe-card entry-engine-clip">
                    <video controls src={battleRawUrl(car.clip_path)} preload="metadata" />
                    <p className="hint">Clip length: {car.clip_duration}s</p>
                    <p className="hint">
                      {car.rev_detected
                        ? `Revving detected: peak ~${car.rev_events[0].peak_hz}Hz over baseline ~${car.rev_events[0].baseline_hz}Hz`
                        : "No revving pattern detected (best-effort pitch check; may miss broadband exhaust notes)"}
                    </p>
                  </article>
                ) : (
                  <>
                    <p className="error">No usable exterior startup clip: {car.error || "rejected"}</p>
                    {(car.listings_considered || []).length > 0 && (
                      <p className="hint">
                        Listings checked:{" "}
                        {car.listings_considered.map((url, j) => (
                          <span key={url}>
                            {j > 0 && ", "}
                            <a href={url} target="_blank" rel="noreferrer">#{j + 1}</a>
                          </span>
                        ))}
                      </p>
                    )}
                  </>
                )}
              </div>
            ))}
          </div>
          <div className="render-actions">
            <button
              className="generate-btn"
              onClick={handleBattleRender}
              disabled={stage === "generating" || battle.approved_count < 2}
            >
              {stage === "generating" ? "Rendering Battle..." : "Render Battle Video"}
            </button>
          </div>
          {battle.approved_count < 2 && (
            <p className="hint">Need at least 2 approved cars to render. Try different years/models for the failed ones.</p>
          )}
        </div>
      )}

      {battleVideoUrl && (
        <div className="video-panel">
          <h2>Done</h2>
          <div className="video-player">
            <video controls src={battleVideoUrl} width="360" />
            <p><a href={battleVideoUrl} target="_blank" rel="noreferrer">Open video directly</a></p>
          </div>
        </div>
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
