import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";
import puppeteer from "puppeteer-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";

puppeteer.use(StealthPlugin());
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "../../..");
const LAUNCH_ARGS = [
  "--no-sandbox",
  "--disable-setuid-sandbox",
  "--disable-blink-features=AutomationControlled",
  "--disable-infobars",
  "--window-size=1366,768",
];

function chromeExecutableOverride() {
  return process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROME_PATH || undefined;
}

function hasFlag(name) {
  return process.argv.includes(`--${name}`);
}

function argValue(name, fallback = null) {
  const prefix = `--${name}=`;
  const match = process.argv.find((arg) => arg.startsWith(prefix));
  return match ? match.slice(prefix.length) : fallback;
}

function argValues(name) {
  const prefix = `--${name}=`;
  return process.argv.filter((arg) => arg.startsWith(prefix)).map((arg) => arg.slice(prefix.length));
}

function slugify(value) {
  return String(value || "car-source")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "car-source";
}

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

function extensionFromContentType(contentType, url = "") {
  const cleanUrl = url.split("?")[0].split("#")[0].toLowerCase();
  const urlExt = path.extname(cleanUrl);
  if ([".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov"].includes(urlExt)) {
    return urlExt;
  }
  if (!contentType) return ".jpg";
  if (contentType.includes("png")) return ".png";
  if (contentType.includes("webp")) return ".webp";
  if (contentType.includes("gif")) return ".gif";
  if (contentType.includes("mp4")) return ".mp4";
  if (contentType.includes("webm")) return ".webm";
  return ".jpg";
}

function normalizeMediaUrl(rawUrl, baseUrl) {
  if (!rawUrl) return null;
  let value = String(rawUrl).trim().replace(/&amp;/g, "&");
  if (!value || value.startsWith("data:")) return null;
  value = value.replace("{width}", "1480").replace("%7Bwidth%7D", "1480");
  try {
    return new URL(value, baseUrl).href;
  } catch {
    return null;
  }
}

function classifyMediaCandidate(candidate) {
  const haystack = [candidate.url, candidate.alt, candidate.context, candidate.source].filter(Boolean).join(" ").toLowerCase();
  const labels = new Set(candidate.labels || []);
  const addIf = (label, patterns) => {
    if (patterns.some((pattern) => haystack.includes(pattern))) labels.add(label);
  };
  addIf("interior", ["interior", "cabin", "seat", "leather", "nappa", "dashboard", "instrument", "gauge", "cockpit"]);
  addIf("exterior", ["exterior", "hero", "360", "soul-red", "artisan-red", "roadster", "front", "rear", "side"]);
  addIf("wheels", ["wheel", "rim", "alloy"]);
  addIf("convertible_roof", ["convertible", "soft-top", "hard-top", "rf", "roof", "top-"]);
  addIf("performance", ["engine", "tach", "gauge", "instrument", "driving", "horsepower", "performance"]);
  addIf("price", ["price", "msrp", "build", "trim"]);
  addIf("gallery", ["gallery", "vlp", "siteassets"]);
  if (!labels.size) labels.add(candidate.type === "video" ? "video_broll" : "general");
  return { ...candidate, labels: Array.from(labels) };
}

function scoreMediaCandidate(candidate) {
  const labels = new Set(candidate.labels || []);
  let score = 0;
  if (candidate.type === "image") score += 10;
  if (candidate.type === "video") score += 12;
  if (labels.has("exterior")) score += 10;
  if (labels.has("interior")) score += 9;
  if (labels.has("convertible_roof")) score += 8;
  if (labels.has("performance")) score += 5;
  if (labels.has("gallery")) score += 4;
  if (/siteassets|mazdausa|porsche|audi|toyota|ford|chevrolet|bmwusa|mercedes/i.test(candidate.url)) score += 8;
  if (/logo|icon|favicon|sprite|badge/i.test(candidate.url)) score -= 20;
  if (/main-nav|homepage|global-nav|shopping|community|owner|national-geographic|sensor-movie|recommended-search/i.test(candidate.url)) score -= 80;
  if (/siteassets\/vehicles/i.test(candidate.url)) score += 10;
  if (/\.svg(\?|#|$)/i.test(candidate.url)) score -= 20;
  return score;
}

function dedupeCandidates(candidates) {
  const seen = new Set();
  return candidates
    .map(classifyMediaCandidate)
    .map((candidate) => ({ ...candidate, score: scoreMediaCandidate(candidate) }))
    .filter((candidate) => {
      if (!candidate.url || seen.has(candidate.url)) return false;
      seen.add(candidate.url);
      return true;
    })
    .sort((a, b) => b.score - a.score);
}

async function downloadAssets(packet, outputRoot, { maxImages = 40, maxVideos = 4, downloadVideos = false } = {}) {
  const imagesDir = path.join(outputRoot, "images");
  const videosDir = path.join(outputRoot, "videos");
  await ensureDir(imagesDir);
  await ensureDir(videosDir);
  const downloadedImages = [];
  const downloadedVideos = [];
  const imageCandidates = packet.media_candidates.filter((item) => item.type === "image").slice(0, maxImages);
  const videoCandidates = packet.media_candidates.filter((item) => item.type === "video").slice(0, maxVideos);

  async function fetchAsset(candidate, index, kind) {
    const response = await fetch(candidate.url, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
        Accept: kind === "image" ? "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8" : "video/mp4,video/*,*/*;q=0.8",
      },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const contentType = response.headers.get("content-type") || "";
    if (kind === "image" && !contentType.startsWith("image/")) throw new Error(`Not an image: ${contentType}`);
    if (kind === "video" && !contentType.startsWith("video/")) throw new Error(`Not a video: ${contentType}`);
    const buffer = Buffer.from(await response.arrayBuffer());
    const minBytes = kind === "image" ? 20_000 : 200_000;
    if (buffer.length < minBytes) throw new Error(`Too small: ${buffer.length} bytes`);
    const primaryLabel = candidate.labels?.[0] || kind;
    const filename = `${slugify(primaryLabel)}-${kind}-${String(index + 1).padStart(2, "0")}${extensionFromContentType(contentType, candidate.url)}`;
    const relativePath = path.join(kind === "image" ? "images" : "videos", filename);
    await fs.writeFile(path.join(outputRoot, relativePath), buffer);
    return {
      source_url: candidate.url,
      path: relativePath,
      content_type: contentType,
      bytes: buffer.length,
      labels: candidate.labels || [],
      score: candidate.score || 0,
      alt: candidate.alt || null,
      context: candidate.context || null,
    };
  }

  for (const [index, candidate] of imageCandidates.entries()) {
    try {
      downloadedImages.push(await fetchAsset(candidate, index, "image"));
    } catch (error) {
      downloadedImages.push({ source_url: candidate.url, labels: candidate.labels || [], error: String(error?.message || error) });
    }
  }

  if (downloadVideos) {
    for (const [index, candidate] of videoCandidates.entries()) {
      try {
        downloadedVideos.push(await fetchAsset(candidate, index, "video"));
      } catch (error) {
        downloadedVideos.push({ source_url: candidate.url, labels: candidate.labels || [], error: String(error?.message || error) });
      }
    }
  }

  return { downloadedImages, downloadedVideos, videoCandidates };
}

async function extractPagePacket(page, source) {
  return page.evaluate((sourceInput) => {
    const text = document.body?.innerText || "";
    const meta = (selector) => document.querySelector(selector)?.content || null;
    const canonical = document.querySelector("link[rel='canonical']")?.href || location.href;
    const title =
      meta("meta[property='og:title']") ||
      document.querySelector("h1")?.innerText?.trim() ||
      document.title ||
      null;
    const description =
      meta("meta[property='og:description']") ||
      meta("meta[name='description']") ||
      null;
    const parseSrcset = (srcset) => String(srcset || "")
      .split(",")
      .map((part) => part.trim().split(/\s+/)[0])
      .filter(Boolean);
    const mediaCandidates = [];
    const push = (type, url, data = {}) => {
      if (!url || !/^https?:|^\//i.test(url)) return;
      mediaCandidates.push({ type, url, ...data });
    };

    const ogImage = meta("meta[property='og:image']") || meta("meta[name='og:image']") || null;
    push("image", ogImage, { source: "open_graph", labels: ["hero"] });

    for (const img of Array.from(document.images)) {
      const context = img.closest("section, article, div")?.innerText?.replace(/\s+/g, " ").trim().slice(0, 280) || null;
      const alt = img.alt || img.getAttribute("aria-label") || null;
      push("image", img.currentSrc || img.src, { source: "img", alt, context });
      for (const src of parseSrcset(img.srcset)) push("image", src, { source: "img_srcset", alt, context });
    }

    for (const sourceEl of Array.from(document.querySelectorAll("picture source, source[srcset]"))) {
      const context = sourceEl.closest("section, article, div")?.innerText?.replace(/\s+/g, " ").trim().slice(0, 280) || null;
      for (const src of parseSrcset(sourceEl.srcset)) push("image", src, { source: "picture_source", context });
    }

    for (const el of Array.from(document.querySelectorAll("[style]"))) {
      const style = el.getAttribute("style") || "";
      const matches = style.matchAll(/url\(["']?([^"')]+)["']?\)/gi);
      const context = el.innerText?.replace(/\s+/g, " ").trim().slice(0, 280) || null;
      for (const match of matches) push("image", match[1], { source: "css_background", context });
    }

    for (const video of Array.from(document.querySelectorAll("video"))) {
      const context = video.closest("section, article, div")?.innerText?.replace(/\s+/g, " ").trim().slice(0, 280) || null;
      push("video", video.currentSrc || video.src, { source: "video", context });
      for (const sourceEl of Array.from(video.querySelectorAll("source"))) {
        push("video", sourceEl.src, { source: "video_source", context });
      }
      push("image", video.poster, { source: "video_poster", context, labels: ["video_poster"] });
    }

    for (const anchor of Array.from(document.querySelectorAll("a[href]"))) {
      const href = anchor.href;
      if (/\.(jpg|jpeg|png|webp|mp4|webm|mov)(\?|#|$)/i.test(href)) {
        const type = /\.(mp4|webm|mov)(\?|#|$)/i.test(href) ? "video" : "image";
        push(type, href, { source: "asset_link", context: anchor.innerText?.trim().slice(0, 160) || null });
      }
    }

    return {
      source_name: sourceInput.sourceName,
      source_url: canonical,
      input_url: location.href,
      scraped_at: new Date().toISOString(),
      role: sourceInput.role,
      title,
      description,
      og_image: ogImage,
      media_candidates: mediaCandidates,
      text_sample: text.replace(/\s+/g, " ").trim().slice(0, 4000),
      extraction_notes: [
        "Browser packet extracted from page metadata, visible text, images, picture/srcset assets, CSS background URLs, and video sources.",
        "Verify licensing/usage rights before using images or videos in a published video.",
      ],
    };
  }, source);
}

function parseSeedMediaValue(value) {
  const [rawUrl, rawLabels] = String(value).split("::");
  return {
    url: normalizeMediaUrl(rawUrl, "https://www.mazdausa.com/"),
    labels: rawLabels ? rawLabels.split(",").map((label) => label.trim()).filter(Boolean) : [],
  };
}

function seedMediaCandidates(seedItems, type, source) {
  return seedItems
    .filter((item) => item.url)
    .map((item) => ({ type, url: item.url, source, labels: item.labels, context: "seeded official media URL" }));
}

async function scrapeOne(page, url, { sourceName, role }) {
  await page.goto(url, { waitUntil: "networkidle2", timeout: 90000 });
  const packet = await extractPagePacket(page, { sourceName, role });
  packet.media_candidates = packet.media_candidates
    .map((candidate) => ({ ...candidate, url: normalizeMediaUrl(candidate.url, url) }))
    .filter((candidate) => candidate.url);
  return packet;
}

async function main() {
  if (hasFlag("doctor")) {
    let browser;
    try {
      browser = await puppeteer.launch({
        headless: "new",
        executablePath: chromeExecutableOverride(),
        args: LAUNCH_ARGS,
      });
      const version = await browser.version();
      console.log(JSON.stringify({
        ok: true,
        node: process.version,
        browserVersion: version,
        puppeteerExecutablePath: puppeteer.executablePath?.() || null,
        executableOverride: chromeExecutableOverride() || null,
        notes: ["Chrome launched successfully. You can run: npm run scrape:miata-official"],
      }, null, 2));
    } catch (error) {
      console.error(JSON.stringify({
        ok: false,
        node: process.version,
        puppeteerExecutablePath: puppeteer.executablePath?.() || null,
        executableOverride: chromeExecutableOverride() || null,
        error: String(error?.message || error),
        next_steps: ["Run: npm run setup:linux", "Then rerun: npm run doctor", "Then rerun: npm run scrape:miata-official"],
      }, null, 2));
      throw error;
    } finally {
      if (browser) await browser.close();
    }
    return;
  }

  const url = argValue("url");
  if (!url) throw new Error("Missing --url=https://...");

  const extraUrls = argValues("extra-url");
  const seedImages = argValues("seed-image").map(parseSeedMediaValue);
  const seedVideos = argValues("seed-video").map(parseSeedMediaValue);
  const topic = slugify(argValue("topic", new URL(url).hostname));
  const sourceName = argValue("source-name", new URL(url).hostname);
  const role = argValue("role", "official_or_reputable_source");
  const outputRoot = path.resolve(REPO_ROOT, "cars/output/sources", topic);
  const screenshotDir = path.join(outputRoot, "screenshots");
  const maxImages = Number(argValue("max-images", "40"));
  const maxVideos = Number(argValue("max-videos", "4"));
  const downloadVideos = hasFlag("download-videos");
  await ensureDir(screenshotDir);

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
    await page.setViewport({ width: 1366, height: 768, deviceScaleFactor: 1 });

    const pages = [];
    const page_errors = [];
    for (const sourceUrl of [url, ...extraUrls]) {
      try {
        pages.push(await scrapeOne(page, sourceUrl, { sourceName, role }));
      } catch (error) {
        page_errors.push({ url: sourceUrl, error: String(error?.message || error) });
      }
    }

    const fallbackPage = pages[0] || {
      source_name: sourceName,
      source_url: url,
      input_url: url,
      scraped_at: new Date().toISOString(),
      role,
      title: `${sourceName} source packet`,
      description: null,
      og_image: null,
      text_sample: "",
      extraction_notes: ["Page navigation failed; packet created from seeded official media URLs."],
    };

    const packet = {
      ...fallbackPage,
      topic,
      pages: pages.map(({ media_candidates, ...rest }) => rest),
      page_errors,
      screenshots: {
        viewport: path.join("screenshots", "viewport.png"),
        full_page: path.join("screenshots", "full-page.png"),
      },
      supported_official_hosts: [
        "mazdausa.com",
        "audiusa.com",
        "porsche.com",
        "toyota.com",
        "ford.com",
        "chevrolet.com",
        "bmwusa.com",
        "mercedes-benzusa.com",
      ],
    };

    packet.media_candidates = dedupeCandidates([
      ...seedMediaCandidates(seedImages, "image", "seed_image"),
      ...seedMediaCandidates(seedVideos, "video", "seed_video"),
      ...pages.flatMap((item) => item.media_candidates || []),
    ]);
    packet.image_urls = packet.media_candidates.filter((item) => item.type === "image").map((item) => item.url).slice(0, 50);
    packet.video_urls = packet.media_candidates.filter((item) => item.type === "video").map((item) => item.url).slice(0, 20);
    packet.image_count = packet.image_urls.length;
    packet.video_count = packet.video_urls.length;

    try {
      await page.goto(url, { waitUntil: "networkidle2", timeout: 90000 });
      await page.screenshot({ path: path.join(screenshotDir, "viewport.png"), fullPage: false });
      await page.screenshot({ path: path.join(screenshotDir, "full-page.png"), fullPage: true });
    } catch (error) {
      packet.screenshot_error = String(error?.message || error);
    }

    const { downloadedImages, downloadedVideos, videoCandidates } = await downloadAssets(packet, outputRoot, { maxImages, maxVideos, downloadVideos });
    packet.downloaded_images = downloadedImages;
    packet.downloaded_videos = downloadedVideos;
    packet.video_candidates = videoCandidates;

    await fs.writeFile(path.join(outputRoot, "source-packet.json"), JSON.stringify(packet, null, 2));
    await fs.writeFile(path.join(outputRoot, "media-manifest.json"), JSON.stringify({
      topic,
      source_name: sourceName,
      created_at: new Date().toISOString(),
      downloaded_images: downloadedImages,
      downloaded_videos: downloadedVideos,
      video_candidates: videoCandidates,
      media_candidates: packet.media_candidates,
    }, null, 2));
    console.log(JSON.stringify(packet, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  if (String(error?.message || error).includes("Failed to launch the browser process")) {
    console.error(
      "\nChrome launched but the Linux image is missing runtime libraries. " +
        "In Codespaces, run: npm run setup:linux\n"
    );
  }
  process.exit(1);
});
