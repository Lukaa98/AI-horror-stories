// Fallback image source: free-text Commons file search (not category-tree
// dependent). Used when an exact category guess comes up empty -- more
// forgiving of imprecise AI-guessed category names.
import fs from "fs/promises";
import path from "path";

const API_ROOT = "https://commons.wikimedia.org/w/api.php";
const USER_AGENT = "ai-horror-stories-cars-research/0.1 (local research tool; non-commercial dry run)";

function argValue(name, fallback = null) {
  const prefix = `--${name}=`;
  const match = process.argv.find((arg) => arg.startsWith(prefix));
  return match ? match.slice(prefix.length) : fallback;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function apiGet(params, retries = 4) {
  const url = new URL(API_ROOT);
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  for (let attempt = 0; attempt <= retries; attempt++) {
    const response = await fetch(url, { headers: { "User-Agent": USER_AGENT } });
    if (response.ok) return response.json();
    if (response.status === 429 && attempt < retries) {
      await sleep(2000 * (attempt + 1));
      continue;
    }
    throw new Error(`API HTTP ${response.status} for ${url}`);
  }
}

async function searchFiles(query, limit) {
  const data = await apiGet({
    action: "query",
    list: "search",
    srnamespace: "6", // File: namespace
    srsearch: `${query} filetype:bitmap`,
    srlimit: String(limit),
    format: "json",
  });
  return (data.query?.search || []).map((r) => r.title);
}

async function fetchImageInfo(titles, thumbWidth) {
  if (!titles.length) return [];
  const data = await apiGet({
    action: "query",
    titles: titles.join("|"),
    prop: "imageinfo",
    iiprop: "url|size|mime|extmetadata",
    iiurlwidth: String(thumbWidth),
    format: "json",
  });
  const pages = data.query?.pages || {};
  const results = [];
  for (const page of Object.values(pages)) {
    const info = page.imageinfo?.[0];
    if (!info) continue;
    const meta = info.extmetadata || {};
    results.push({
      title: page.title,
      url: info.thumburl || info.url,
      width: info.thumbwidth || info.width,
      height: info.thumbheight || info.height,
      original_width: info.width,
      mime: info.mime,
      restrictions: meta.Restrictions?.value || null,
    });
  }
  return results;
}

async function main() {
  const query = argValue("query");
  if (!query) throw new Error('Missing --query="Chevrolet Corvette Z06 C8"');
  const outDir = argValue("out-dir");
  if (!outDir) throw new Error("Missing --out-dir=path");
  const limit = Number(argValue("limit", "6"));
  const minWidth = Number(argValue("min-width", "1000"));

  const titles = await searchFiles(query, limit * 3);
  const infos = await fetchImageInfo(titles, 1600);
  const usable = infos
    .filter((i) => i.mime?.startsWith("image/"))
    .filter((i) => !i.restrictions)
    .filter((i) => (i.original_width || 0) >= minWidth)
    .slice(0, limit);

  await fs.mkdir(outDir, { recursive: true });
  const downloaded = [];
  for (const [index, asset] of usable.entries()) {
    try {
      const response = await fetch(asset.url, { headers: { "User-Agent": USER_AGENT } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const buffer = Buffer.from(await response.arrayBuffer());
      const ext = asset.mime === "image/png" ? ".png" : ".jpg";
      const filename = `search-${String(index + 1).padStart(2, "0")}${ext}`;
      await fs.writeFile(path.join(outDir, filename), buffer);
      downloaded.push(filename);
    } catch (error) {
      console.error(`Failed ${asset.title}: ${error.message}`);
    }
    await sleep(700);
  }
  console.log(JSON.stringify({ query, found: usable.length, downloaded }, null, 2));
}

main().catch((error) => {
  console.error(JSON.stringify({ error: String(error?.message || error) }));
  process.exit(1);
});
