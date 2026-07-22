// Best-effort discovery of "generation" subcategories for an arbitrary car on
// Wikimedia Commons. Heuristic only -- no guarantee the car has a clean
// generation category tree (Mustang uses roman numerals, Miata uses NA/NB/NC/ND
// codes and isn't auto-discoverable by this heuristic at all). Prints JSON:
//   {"car": "...", "candidates": [{category, file_count}], "usable": bool}
import { fileURLToPath } from "url";

const API_ROOT = "https://commons.wikimedia.org/w/api.php";
const USER_AGENT = "ai-horror-stories-cars-research/0.1 (local research tool; non-commercial dry run)";

function argValue(name, fallback = null) {
  const prefix = `--${name}=`;
  const match = process.argv.find((arg) => arg.startsWith(prefix));
  return match ? match.slice(prefix.length) : fallback;
}

async function apiGet(params, retries = 5) {
  const url = new URL(API_ROOT);
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  for (let attempt = 0; attempt <= retries; attempt++) {
    const response = await fetch(url, { headers: { "User-Agent": USER_AGENT } });
    if (response.ok) return response.json();
    if (response.status === 429 && attempt < retries) {
      await new Promise((resolve) => setTimeout(resolve, 3000 * (attempt + 1)));
      continue;
    }
    throw new Error(`API HTTP ${response.status} for ${url}`);
  }
}

async function listSubcategories(category) {
  const results = [];
  let cmcontinue = null;
  while (true) {
    const params = {
      action: "query",
      list: "categorymembers",
      cmtitle: `Category:${category}`,
      cmtype: "subcat",
      cmlimit: "100",
      format: "json",
    };
    if (cmcontinue) params.cmcontinue = cmcontinue;
    const data = await apiGet(params);
    const members = data.query?.categorymembers || [];
    results.push(...members.map((m) => m.title.replace(/^Category:/, "")));
    cmcontinue = data.continue?.cmcontinue;
    if (!cmcontinue || members.length === 0) break;
  }
  return results;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function categoryFileCount(category) {
  const data = await apiGet({
    action: "query",
    prop: "categoryinfo",
    titles: `Category:${category}`,
    format: "json",
  });
  const pages = data.query?.pages || {};
  const page = Object.values(pages)[0];
  await sleep(900);
  return page?.categoryinfo?.files || 0;
}

// Patterns that suggest a subcategory represents a chronological generation,
// not a trim/variant/competition/logo/museum subcategory. Different makers use
// wildly different conventions (Mustang: roman numerals, Corvette: C1-C8,
// Miata: NA/NB/NC/ND, BMW: E30/E46-style, Porsche: 991/992-style), so this
// checks several patterns -- it's a heuristic, not a guarantee.
const GENERATION_PATTERNS = [
  /\b(I{1,3}|IV|VI{0,3}|IX|X)\b$/, // roman numerals at end, e.g. "Ford Mustang III"
  /\(\d{4}\s*[-–]\s*(\d{4}|present)\)/i, // "(2015-2023)" or "(2015-present)"
  /\bfirst generation\b|\bsecond generation\b|\bthird generation\b|\bfourth generation\b|\bfifth generation\b|\bsixth generation\b|\bseventh generation\b/i,
];

const EXCLUDE_PATTERNS = [
  /competition|racing|rally|nascar|concept|logo|police|pace car|museum|model cars?\b|toy|diecast|advertis|brochure|manual|engine\b|interior/i,
];

function looksLikeGeneration(subcatTitle, parentName) {
  const remainder = subcatTitle.replace(parentName, "").trim();
  if (EXCLUDE_PATTERNS.some((p) => p.test(subcatTitle))) return false;
  if (GENERATION_PATTERNS.some((p) => p.test(subcatTitle) || p.test(remainder))) return true;
  // Short chassis/generation code as the entire remainder, e.g. "C1", "NA", "E30", "991".
  if (remainder && remainder.length <= 4 && /^[A-Z]{1,3}\d{0,3}$/.test(remainder)) return true;
  if (remainder && /^\d{2,3}$/.test(remainder)) return true;
  return false;
}

async function main() {
  const car = argValue("car");
  if (!car) throw new Error('Missing --car="Toyota Supra"');
  const minFiles = Number(argValue("min-files", "15"));
  const maxResults = Number(argValue("max-results", "4"));

  const subcats = await listSubcategories(car);
  const candidates = subcats.filter((title) => looksLikeGeneration(title, car));

  const withCounts = [];
  for (const category of candidates) {
    const fileCount = await categoryFileCount(category);
    withCounts.push({ category, file_count: fileCount });
  }
  withCounts.sort((a, b) => b.file_count - a.file_count);
  const usable = withCounts.filter((c) => c.file_count >= minFiles).slice(0, maxResults);

  console.log(JSON.stringify({
    car,
    all_subcategories_checked: subcats.length,
    candidates: withCounts,
    usable_count: usable.length,
    usable: usable.length >= 2, // need at least a couple entries to make any ranking
    selected: usable,
  }, null, 2));
}

main().catch((error) => {
  console.error(JSON.stringify({ error: String(error?.message || error) }));
  process.exit(1);
});
