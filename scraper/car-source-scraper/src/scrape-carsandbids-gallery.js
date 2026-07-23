import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";
import puppeteer from "puppeteer-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";

puppeteer.use(StealthPlugin());

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const LAUNCH_ARGS = [
  "--no-sandbox",
  "--disable-setuid-sandbox",
  "--disable-blink-features=AutomationControlled",
  "--disable-infobars",
  "--window-size=1440,1200",
];

function argValue(name, fallback = null) {
  const prefix = `--${name}=`;
  const match = process.argv.find((arg) => arg.startsWith(prefix));
  return match ? match.slice(prefix.length) : fallback;
}

function chromeExecutableOverride() {
  return process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROME_PATH || undefined;
}

function slugify(value) {
  return String(value || "asset")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "asset";
}

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

async function autoScroll(page, steps = 10, delayMs = 500) {
  for (let index = 0; index < steps; index += 1) {
    await page.evaluate(() => {
      window.scrollBy(0, Math.max(window.innerHeight, 900));
    });
    await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
  await page.evaluate(() => window.scrollTo(0, 0));
}

function extensionFromContentType(contentType, url = "") {
  const cleanUrl = url.split("?")[0].split("#")[0].toLowerCase();
  const urlExt = path.extname(cleanUrl);
  if ([".jpg", ".jpeg", ".png", ".webp"].includes(urlExt)) {
    return urlExt;
  }
  if (!contentType) return ".jpg";
  if (contentType.includes("png")) return ".png";
  if (contentType.includes("webp")) return ".webp";
  return ".jpg";
}

function normalizeMediaUrl(rawUrl, baseUrl) {
  if (!rawUrl) return null;
  const value = String(rawUrl).trim().replace(/&amp;/g, "&");
  if (!value || value.startsWith("data:")) return null;
  try {
    return new URL(value, baseUrl).href;
  } catch {
    return null;
  }
}

function normalizeAuctionUrl(rawUrl) {
  try {
    const url = new URL(String(rawUrl));
    return `${url.origin}${url.pathname}`;
  } catch {
    return String(rawUrl || "");
  }
}

function extractAuctionId(url) {
  const match = normalizeAuctionUrl(url).match(/\/auctions\/([^/?#]+)/i);
  return match ? match[1] : null;
}

function extractSearchTokens(value) {
  return String(value || "")
    .toLowerCase()
    .split(/[^a-z0-9+.-]+/i)
    .filter(Boolean);
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function humanizeAuctionUrl(url) {
  const normalized = normalizeAuctionUrl(url);
  const slug = normalized.split("/").filter(Boolean).pop() || "";
  return slug
    .split("-")
    .map((part) => part ? part.charAt(0).toUpperCase() + part.slice(1) : part)
    .join(" ");
}

function buildSearchUrl({ make, model, startYear, endYear, sort = "10" }) {
  const url = new URL(`https://carsandbids.com/search/${slugify(make)}/${slugify(model)}`);
  if (startYear) url.searchParams.set("start_year", String(startYear));
  if (endYear) url.searchParams.set("end_year", String(endYear));
  if (sort) url.searchParams.set("csort", String(sort));
  return url.href;
}

function classifyCandidate(candidate, visualHighlight = "") {
  const haystack = [
    candidate.url,
    candidate.alt,
    candidate.anchorText,
    candidate.context,
    candidate.section,
    candidate.title,
    visualHighlight,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  const labels = new Set(candidate.labels || []);
  const addIf = (label, patterns) => {
    if (patterns.some((pattern) => haystack.includes(pattern))) labels.add(label);
  };

  addIf("interior", ["interior", "cabin", "cockpit", "seat", "dashboard", "gauge"]);
  addIf("engine", ["engine", "frunk", "trunk", "bay"]);
  addIf("rear", [" rear", "taillight", "taillamp", "back ", "spoiler"]);
  addIf("front", [" front", "nose", "grille", "headlight", "bumper"]);
  addIf("wheel", ["wheel", "rim", "brake"]);
  addIf("detail", ["detail", "badge", "blade", "trim", "carbon", "light"]);
  addIf("highlight", visualHighlight ? [String(visualHighlight).toLowerCase()] : []);

  if (!labels.size) labels.add("exterior");
  if (!labels.has("interior") && !labels.has("engine")) labels.add("exterior");
  return { ...candidate, labels: Array.from(labels) };
}

function scoreCandidate(candidate, desiredLabels, queryTokens) {
  const haystack = [candidate.url, candidate.alt, candidate.anchorText, candidate.context, candidate.section]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  const labels = new Set(candidate.labels || []);
  let score = 0;
  for (const label of desiredLabels) {
    if (labels.has(label)) score += 50;
  }
  if (labels.has("exterior")) score += 12;
  if (labels.has("interior")) score += 18;
  if (labels.has("engine")) score += 16;
  if (labels.has("rear")) score += 10;
  if (labels.has("front")) score += 10;
  if (labels.has("highlight")) score += 8;
  const matchedTokens = queryTokens.filter((token) => haystack.includes(token));
  score += matchedTokens.length * 14;
  if (queryTokens.length && matchedTokens.length === 0) score -= 35;
  score += Math.min(Number(candidate.width || 0), 2200) / 100;
  score += Math.min(Number(candidate.height || 0), 2200) / 120;
  if (/doug's take|related|recommended|shipping|carfax|seller q&a|comments/i.test(haystack)) score -= 80;
  if (/logo|avatar|icon|profile|thumbnail|dougscore|shipping|carfax|banner/i.test(haystack)) score -= 120;
  if (/\.svg(\?|#|$)/i.test(candidate.url || "")) score -= 120;
  if ((candidate.width || 0) < 320 || (candidate.height || 0) < 240) score -= 80;
  return score;
}

async function downloadImage(candidate, outDir, filenamePrefix) {
  const response = await fetch(candidate.url, {
    headers: {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
      Accept: "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
      Referer: candidate.auctionUrl,
    },
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.startsWith("image/")) throw new Error(`Not an image: ${contentType}`);
  const buffer = Buffer.from(await response.arrayBuffer());
  if (buffer.length < 20_000) throw new Error(`Too small: ${buffer.length} bytes`);
  const filename = `${filenamePrefix}${extensionFromContentType(contentType, candidate.url)}`;
  await fs.writeFile(path.join(outDir, filename), buffer);
  return {
    path: filename,
    url: candidate.url,
    labels: candidate.labels,
    section: candidate.section || null,
    width: candidate.width || null,
    height: candidate.height || null,
    bytes: buffer.length,
  };
}

async function collectAuctionEntries(page, searchUrl, queryTokens, startYear, endYear) {
  await page.goto(searchUrl, { waitUntil: "networkidle2", timeout: 90000 });
  await page.waitForSelector("a[href*='/auctions/']", { timeout: 30000 });
  await autoScroll(page, 6, 400);

  const entries = await page.evaluate(() => {
    const results = [];
    const seen = new Set();
    for (const anchor of Array.from(document.querySelectorAll("a[href*='/auctions/']"))) {
      const rawHref = anchor.href.startsWith("http")
        ? anchor.href
        : `https://carsandbids.com${anchor.getAttribute("href")}`;
      const href = rawHref ? rawHref.split("?")[0] : rawHref;
      if (seen.has(href)) continue;
      seen.add(href);
      const card = anchor.closest("article, li, div");
      const text = (card?.innerText || anchor.innerText || "").replace(/\s+/g, " ").trim();
      const title = anchor.innerText.replace(/\s+/g, " ").trim() || text.split("Watch")[0]?.trim() || href;
      results.push({ url: href, title, text });
    }
    return results;
  });

  const tokens = queryTokens.map((token) => token.toLowerCase()).filter(Boolean);
  return entries
    .map((entry) => {
      const haystack = `${entry.title} ${entry.text} ${entry.url}`.toLowerCase();
      const titleYear = Number((entry.title.match(/\b(19|20)\d{2}\b/) || [])[0] || 0);
      const cleanedTitle = /sold for|bid to|featured/i.test(entry.title) ? humanizeAuctionUrl(entry.url) : entry.title;
      let score = 0;
      for (const token of tokens) {
        if (haystack.includes(token)) score += token.length > 2 ? 18 : 8;
      }
      if (tokens.length && haystack.includes(tokens.join(" "))) score += 45;
      if (titleYear && (!startYear || titleYear >= startYear) && (!endYear || titleYear <= endYear)) score += 28;
      if (/sold for|bid to|sold after|ended/i.test(entry.text)) score += 6;
      if (/spyder|convertible/i.test(haystack) && tokens.includes("coupe")) score -= 25;
      return { ...entry, title: cleanedTitle, titleYear: titleYear || null, score };
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, 6);
}

async function extractAuctionGallery(page, auctionUrl, visualHighlight) {
  await page.goto(auctionUrl, { waitUntil: "networkidle2", timeout: 90000 });
  await page.waitForSelector("img", { timeout: 30000 });
  await autoScroll(page, 12, 450);

  const raw = await page.evaluate(() => {
    const headingText = (node) => {
      let current = node;
      while (current) {
        const heading = current.querySelector?.("h1,h2,h3,h4,strong");
        const text = heading?.innerText?.replace(/\s+/g, " ").trim();
        if (text) return text;
        current = current.parentElement;
      }
      return "";
    };

    const nextData = (() => {
      const node = document.querySelector("#__NEXT_DATA__");
      if (!node) return null;
      try {
        return JSON.parse(node.textContent || "{}");
      } catch {
        return null;
      }
    })();
    const auction =
      nextData?.props?.pageProps?.auction ||
      nextData?.props?.pageProps?.listing ||
      null;
    const bodyText = document.body?.innerText || "";
    let salePrice = auction?.salePrice || auction?.finalSalePrice || null;
    if (!salePrice) {
      const match = bodyText.match(/(?:Sold for|Bid to)\s+\$([\d,]+)/i);
      if (match) salePrice = Number(match[1].replace(/,/g, ""));
    }

    const candidates = [];
    const pushCandidate = (candidate) => {
      if (!candidate?.url || !/media\.carsandbids\.com|carsandbids\.com/i.test(candidate.url)) return;
      candidates.push(candidate);
    };

    for (const anchor of Array.from(document.querySelectorAll("a[href]"))) {
      const img = anchor.querySelector("img");
      if (!img) continue;
      const url = img.currentSrc || img.src || anchor.href || null;
      const container = anchor.closest("section, article, li, div");
      const context = container?.innerText?.replace(/\s+/g, " ").trim().slice(0, 320) || "";
      pushCandidate({
        url,
        alt: img.alt || "",
        anchorText: anchor.getAttribute("aria-label") || anchor.getAttribute("title") || anchor.innerText || "",
        anchorHref: anchor.href || anchor.getAttribute("href") || "",
        context,
        section: headingText(container),
        width: img.naturalWidth || img.width || 0,
        height: img.naturalHeight || img.height || 0,
        auctionUrl: location.href,
        auctionTitle: document.querySelector("h1")?.innerText?.trim() || document.title,
        title: document.querySelector("h1")?.innerText?.trim() || document.title,
      });
    }

    const seen = new Set();
    for (const img of Array.from(document.querySelectorAll("img"))) {
      const url = img.currentSrc || img.src || null;
      if (!url || seen.has(url)) continue;
      seen.add(url);
      const container = img.closest("section, article, li, div");
      const context = container?.innerText?.replace(/\s+/g, " ").trim().slice(0, 320) || "";
      pushCandidate({
        url,
        alt: img.alt || img.getAttribute("aria-label") || "",
        anchorText: img.closest("a")?.getAttribute("aria-label") || img.closest("a")?.getAttribute("title") || "",
        anchorHref: img.closest("a")?.href || img.closest("a")?.getAttribute("href") || "",
        context,
        section: headingText(container),
        width: img.naturalWidth || img.width || 0,
        height: img.naturalHeight || img.height || 0,
        auctionUrl: location.href,
        auctionTitle: document.querySelector("h1")?.innerText?.trim() || document.title,
        title: document.querySelector("h1")?.innerText?.trim() || document.title,
      });
    }
    return {
      pageMeta: {
        title: auction?.title || document.querySelector("h1")?.innerText?.trim() || document.title,
        year: auction?.year || null,
        make: auction?.make || null,
        model: auction?.model || null,
        salePrice: salePrice || null,
        saleType: salePrice ? (/(sold for)/i.test(bodyText) ? "sold" : "bid_to") : null,
        location: auction?.location || null,
      },
      candidates,
    };
  });

  return {
    pageMeta: raw.pageMeta,
    candidates: raw.candidates
      .map((candidate) => ({
        ...candidate,
        url: normalizeMediaUrl(candidate.url, auctionUrl),
        anchorHref: normalizeAuctionUrl(candidate.anchorHref || ""),
      }))
      .filter((candidate) => candidate.url)
      .filter((candidate) => /media\.carsandbids\.com/i.test(candidate.url))
      .map((candidate) => classifyCandidate(candidate, visualHighlight)),
  };
}

function candidateLooksRelevant(candidate, { auctionId, makeToken, modelToken, queryTokens }) {
  const haystack = [
    candidate.url,
    candidate.alt,
    candidate.anchorText,
    candidate.anchorHref,
    candidate.context,
    candidate.section,
    candidate.auctionTitle,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  const linkedAuctionId = extractAuctionId(candidate.anchorHref);
  if (linkedAuctionId && auctionId && linkedAuctionId !== auctionId) return false;
  if (/related listings|you may also like|recommended|more auctions/i.test(haystack)) return false;

  const foreignMakes = ["acura", "alfa", "aston", "bentley", "bmw", "bugatti", "cadillac", "chevrolet", "ferrari", "ford", "honda", "hyundai", "jaguar", "lamborghini", "lexus", "lotus", "maserati", "mazda", "mclaren", "mercedes", "mini", "nissan", "porsche", "subaru", "tesla", "toyota", "volkswagen"];
  for (const foreignMake of foreignMakes) {
    if (foreignMake === makeToken) continue;
    if (new RegExp(`\\b${escapeRegExp(foreignMake)}\\b`, "i").test(haystack)) return false;
  }

  if (makeToken && !new RegExp(`\\b${escapeRegExp(makeToken)}\\b`, "i").test(haystack)) return false;

  const strongModelMatch = modelToken && new RegExp(`\\b${escapeRegExp(modelToken)}\\b`, "i").test(haystack);
  const queryMatches = queryTokens.filter((token) => token.length >= 2 && haystack.includes(token)).length;
  return Boolean(strongModelMatch || queryMatches >= 2);
}

function chooseImages(candidates, desiredLabels, queryTokens) {
  const ranked = candidates
    .map((candidate) => ({ ...candidate, score: scoreCandidate(candidate, desiredLabels, queryTokens) }))
    .sort((a, b) => b.score - a.score);

  const chosen = [];
  const used = new Set();
  const targetOrder = ["front", "rear", "interior", "engine", "highlight", "detail", "exterior"];

  for (const target of targetOrder) {
    const match = ranked.find((candidate) => !used.has(candidate.url) && candidate.labels.includes(target));
    if (match) {
      used.add(match.url);
      chosen.push({ ...match, primaryLabel: target });
    }
  }

  for (const candidate of ranked) {
    if (chosen.length >= 6) break;
    if (used.has(candidate.url)) continue;
    used.add(candidate.url);
    chosen.push({ ...candidate, primaryLabel: candidate.labels[0] || "exterior" });
  }

  return chosen.slice(0, 6);
}

async function main() {
  const outDir = path.resolve(argValue("out-dir", "."));
  const outJson = argValue("out-json", path.join(outDir, "carsandbids-manifest.json"));
  const query = argValue("query", "");
  const visualHighlight = argValue("visual-highlight", "");
  const searchUrl = argValue("search-url") || buildSearchUrl({
    make: argValue("make"),
    model: argValue("model"),
    startYear: argValue("start-year"),
    endYear: argValue("end-year"),
    sort: argValue("sort", "10"),
  });

  if (!searchUrl || !/^https:\/\/carsandbids\.com\/search\//i.test(searchUrl)) {
    throw new Error("Provide --search-url or --make/--model for a Cars & Bids search page.");
  }

  const startYear = Number(argValue("start-year", "0")) || null;
  const endYear = Number(argValue("end-year", "0")) || null;
  const queryTokens = query
    .toLowerCase()
    .split(/[^a-z0-9+.-]+/i)
    .filter(Boolean);
  const desiredLabels = queryTokens.filter((token) => ["coupe", "spyder", "convertible", "manual", "v8", "v10", "gt"].includes(token));
  const makeToken = String(argValue("make", "")).toLowerCase();
  const modelToken = String(argValue("model", "")).toLowerCase();

  await ensureDir(outDir);
  const browser = await puppeteer.launch({
    headless: "new",
    executablePath: chromeExecutableOverride(),
    args: LAUNCH_ARGS,
  });

  try {
    const page = await browser.newPage();
    await page.setUserAgent(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    );
    await page.setExtraHTTPHeaders({ "Accept-Language": "en-US,en;q=0.9" });
    await page.setViewport({ width: 1440, height: 1200, deviceScaleFactor: 1 });

    const auctions = await collectAuctionEntries(page, searchUrl, queryTokens, startYear, endYear);
    if (!auctions.length) {
      throw new Error(`No auction links found at ${searchUrl}`);
    }

    let selectedAuction = auctions[0];
    let selectedCandidates = [];
    const auctionsUsed = [];
    let bestGalleryCount = 0;
    for (const auction of auctions.slice(0, 4)) {
      const gallery = await extractAuctionGallery(page, auction.url, visualHighlight);
      const usable = gallery.candidates.filter((candidate) => {
        const haystack = `${candidate.url} ${candidate.alt} ${candidate.anchorText} ${candidate.context} ${candidate.section}`.toLowerCase();
        if (/logo|icon|avatar|dougscore|shipping|carfax/i.test(haystack)) return false;
        return candidateLooksRelevant(candidate, {
          auctionId: extractAuctionId(auction.url),
          makeToken,
          modelToken,
          queryTokens,
        });
      });
      if (usable.length) {
        auctionsUsed.push({
          ...auction,
          usable_image_count: usable.length,
          sale_price: gallery.pageMeta?.salePrice || null,
          sale_type: gallery.pageMeta?.saleType || null,
          page_title: gallery.pageMeta?.title || null,
        });
        selectedCandidates.push(...usable);
      }
      if (usable.length > bestGalleryCount) {
        bestGalleryCount = usable.length;
        selectedAuction = {
          ...auction,
          sale_price: gallery.pageMeta?.salePrice || null,
          sale_type: gallery.pageMeta?.saleType || null,
          page_title: gallery.pageMeta?.title || null,
          year: gallery.pageMeta?.year || auction.titleYear || null,
          make: gallery.pageMeta?.make || null,
          model: gallery.pageMeta?.model || null,
          location: gallery.pageMeta?.location || null,
        };
      }
    }

    if (!selectedAuction || !selectedCandidates.length) {
      throw new Error("No usable Cars & Bids gallery images found.");
    }

    const chosen = chooseImages(selectedCandidates, desiredLabels, queryTokens);
    const downloaded = [];
    for (const [index, candidate] of chosen.entries()) {
      try {
        downloaded.push(await downloadImage(candidate, outDir, `${slugify(candidate.primaryLabel)}-${String(index + 1).padStart(2, "0")}`));
      } catch (error) {
        downloaded.push({
          path: null,
          url: candidate.url,
          labels: candidate.labels,
          error: String(error?.message || error),
        });
      }
    }

    const manifest = {
      provider: "cars_and_bids",
      created_at: new Date().toISOString(),
      query,
      visual_highlight: visualHighlight || null,
      search_url: searchUrl,
      search_result_count: auctions.length,
      auctions_considered: auctions,
      auctions_used: auctionsUsed,
      selected_auction: selectedAuction,
      downloaded_images: downloaded,
    };
    await fs.writeFile(outJson, JSON.stringify(manifest, null, 2));
    console.log(JSON.stringify(manifest, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
