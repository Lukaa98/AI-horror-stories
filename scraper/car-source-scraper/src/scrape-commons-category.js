import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "../../..");
const API_ROOT = "https://commons.wikimedia.org/w/api.php";
const USER_AGENT = "ai-horror-stories-cars-research/0.1 (local research tool; non-commercial dry run)";

function hasFlag(name) {
  return process.argv.includes(`--${name}`);
}

function argValue(name, fallback = null) {
  const prefix = `--${name}=`;
  const match = process.argv.find((arg) => arg.startsWith(prefix));
  return match ? match.slice(prefix.length) : fallback;
}

function slugify(value) {
  return String(value || "commons-source")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "commons-source";
}

async function apiGet(params) {
  const url = new URL(API_ROOT);
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  const response = await fetch(url, { headers: { "User-Agent": USER_AGENT } });
  if (!response.ok) throw new Error(`API HTTP ${response.status} for ${url}`);
  return response.json();
}

async function listCategoryFiles(category, limit) {
  const files = [];
  let cmcontinue = null;
  while (files.length < limit) {
    const params = {
      action: "query",
      list: "categorymembers",
      cmtitle: `Category:${category}`,
      cmtype: "file",
      cmlimit: String(Math.min(50, limit - files.length)),
      format: "json",
    };
    if (cmcontinue) params.cmcontinue = cmcontinue;
    const data = await apiGet(params);
    const members = data.query?.categorymembers || [];
    files.push(...members.map((m) => m.title));
    cmcontinue = data.continue?.cmcontinue;
    if (!cmcontinue || members.length === 0) break;
  }
  return files.slice(0, limit);
}

async function fetchImageInfo(titles, thumbWidth) {
  const results = [];
  for (let i = 0; i < titles.length; i += 50) {
    const batch = titles.slice(i, i + 50);
    const data = await apiGet({
      action: "query",
      titles: batch.join("|"),
      prop: "imageinfo",
      iiprop: "url|size|mime|extmetadata",
      iiurlwidth: String(thumbWidth),
      format: "json",
    });
    const pages = data.query?.pages || {};
    for (const page of Object.values(pages)) {
      const info = page.imageinfo?.[0];
      if (!info) continue;
      const meta = info.extmetadata || {};
      results.push({
        title: page.title,
        url: info.thumburl || info.url,
        original_url: info.url,
        width: info.thumbwidth || info.width,
        height: info.thumbheight || info.height,
        original_width: info.width,
        original_height: info.height,
        mime: info.mime,
        license_short: meta.LicenseShortName?.value || null,
        license_url: meta.LicenseUrl?.value || null,
        artist: meta.Artist?.value?.replace(/<[^>]+>/g, "").trim() || null,
        credit: meta.Credit?.value?.replace(/<[^>]+>/g, "").trim() || null,
        description: meta.ImageDescription?.value?.replace(/<[^>]+>/g, "").trim() || null,
        restrictions: meta.Restrictions?.value || null,
      });
    }
  }
  return results;
}

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

function classifyShot(title, description) {
  const t = `${title} ${description || ""}`.toLowerCase();
  if (/steering wheel|dashboard|dash board|\binterior\b|cockpit|\bcabin\b|driver seat|centre console|center console/.test(t)) return "interior";
  if (/engine bay|\bengine\b|under.?the.?hood|bonnet open|hood open|turbo|supercharge/.test(t)) return "engine";
  if (/\brear\b|taillight|tail light|\bback\b|\bboot\b|\btrunk\b/.test(t)) return "rear";
  if (/\bfront\b|headlight/.test(t)) return "front";
  if (/\bside\b|profile/.test(t)) return "side";
  if (/\bwheel\b|\brim\b|\balloy\b/.test(t)) return "wheel";
  return "other";
}

function pickBalanced(candidates, targets, total) {
  const byBucket = new Map();
  for (const item of candidates) {
    const bucket = item.shot_type;
    if (!byBucket.has(bucket)) byBucket.set(bucket, []);
    byBucket.get(bucket).push(item);
  }
  for (const list of byBucket.values()) list.sort((a, b) => (b.original_width || 0) - (a.original_width || 0));

  const picked = [];
  const pickedTitles = new Set();
  for (const [bucket, quota] of Object.entries(targets)) {
    const pool = byBucket.get(bucket) || [];
    for (const item of pool.slice(0, quota)) {
      if (!pickedTitles.has(item.title)) {
        picked.push(item);
        pickedTitles.add(item.title);
      }
    }
  }
  if (picked.length < total) {
    const remaining = candidates
      .filter((item) => !pickedTitles.has(item.title))
      .sort((a, b) => (b.original_width || 0) - (a.original_width || 0));
    for (const item of remaining) {
      if (picked.length >= total) break;
      picked.push(item);
      pickedTitles.add(item.title);
    }
  }
  return picked.slice(0, total);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function downloadImage(asset, outputDir, index, { retries = 3 } = {}) {
  for (let attempt = 0; attempt <= retries; attempt++) {
    const response = await fetch(asset.url, { headers: { "User-Agent": USER_AGENT } });
    if (response.status === 429 || response.status === 503) {
      const wait = 2000 * (attempt + 1);
      console.error(`Rate limited, waiting ${wait}ms before retry ${attempt + 1}/${retries} ...`);
      await sleep(wait);
      continue;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const buffer = Buffer.from(await response.arrayBuffer());
    const ext = asset.mime === "image/png" ? ".png" : asset.mime === "image/webp" ? ".webp" : ".jpg";
    const filename = `commons-${String(index + 1).padStart(3, "0")}${ext}`;
    await fs.writeFile(path.join(outputDir, filename), buffer);
    return { ...asset, path: path.join("images", filename), bytes: buffer.length };
  }
  throw new Error("HTTP 429 (rate limited after retries)");
}

async function main() {
  const category = argValue("category");
  if (!category) throw new Error("Missing --category=\"Mazda MX-5 (NA)\"");
  const topic = slugify(argValue("topic", category));
  const limit = Number(argValue("limit", "24"));
  const minWidth = Number(argValue("min-width", "1200"));
  const thumbWidth = Number(argValue("thumb-width", "1600"));
  const poolSize = Number(argValue("pool-size", "200"));
  const download = hasFlag("download");
  const targets = {
    front: Number(argValue("target-front", "3")),
    rear: Number(argValue("target-rear", "3")),
    side: Number(argValue("target-side", "2")),
    interior: Number(argValue("target-interior", "3")),
    engine: Number(argValue("target-engine", "2")),
    wheel: Number(argValue("target-wheel", "1")),
  };

  const outputRoot = path.resolve(REPO_ROOT, "cars/output/sources", topic);
  const imagesDir = path.join(outputRoot, "images");
  await ensureDir(imagesDir);

  console.error(`Listing files in Category:${category} (pool up to ${poolSize}) ...`);
  const titles = await listCategoryFiles(category, poolSize);
  console.error(`Found ${titles.length} candidate files; fetching imageinfo ...`);
  const infos = await fetchImageInfo(titles, thumbWidth);

  const usable = infos
    .filter((info) => info.mime?.startsWith("image/"))
    .filter((info) => !info.restrictions)
    .filter((info) => (info.original_width || 0) >= minWidth)
    .map((info) => ({ ...info, shot_type: classifyShot(info.title, info.description) }));

  const shotCounts = usable.reduce((acc, item) => {
    acc[item.shot_type] = (acc[item.shot_type] || 0) + 1;
    return acc;
  }, {});
  console.error(`Usable pool: ${usable.length} images. Shot-type breakdown: ${JSON.stringify(shotCounts)}`);

  const filtered = pickBalanced(usable, targets, limit);
  console.error(`${filtered.length} images selected after balancing shot types: ${JSON.stringify(
    filtered.reduce((acc, item) => { acc[item.shot_type] = (acc[item.shot_type] || 0) + 1; return acc; }, {})
  )}`);

  let downloaded = [];
  if (download) {
    for (const [index, asset] of filtered.entries()) {
      try {
        downloaded.push(await downloadImage(asset, imagesDir, index));
        console.error(`Downloaded: ${asset.title}`);
      } catch (error) {
        console.error(`Failed: ${asset.title} - ${error.message}`);
      }
      await sleep(3000);
    }
  }

  const manifest = {
    topic,
    source: "wikimedia_commons",
    category,
    created_at: new Date().toISOString(),
    candidate_count: infos.length,
    usable_count: filtered.length,
    images: download ? downloaded : filtered,
  };
  await fs.writeFile(path.join(outputRoot, "commons-manifest.json"), JSON.stringify(manifest, null, 2));
  console.log(JSON.stringify(manifest, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
