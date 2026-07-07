import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";
import puppeteer from "puppeteer-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";

puppeteer.use(StealthPlugin());
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "../../..");

function hasFlag(name) {
  return process.argv.includes(`--${name}`);
}

function argValue(name, fallback = null) {
  const prefix = `--${name}=`;
  const match = process.argv.find((arg) => arg.startsWith(prefix));
  return match ? match.slice(prefix.length) : fallback;
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
    const imageUrls = Array.from(document.images)
      .map((img) => img.currentSrc || img.src || null)
      .filter(Boolean)
      .filter((src) => /^https?:\/\//i.test(src));
    const uniqueImages = Array.from(new Set(imageUrls));
    const ogImage = meta("meta[property='og:image']") || meta("meta[name='og:image']") || null;

    return {
      source_name: sourceInput.sourceName,
      source_url: canonical,
      scraped_at: new Date().toISOString(),
      role: sourceInput.role,
      title,
      description,
      og_image: ogImage,
      image_urls: uniqueImages.slice(0, 20),
      image_count: uniqueImages.length,
      text_sample: text.replace(/\s+/g, " ").trim().slice(0, 2500),
      extraction_notes: [
        "Browser packet extracted from page metadata, visible text, and image URLs.",
        "Verify licensing/usage rights before using images in a video.",
      ],
    };
  }, source);
}

async function main() {
  if (hasFlag("doctor")) {
    console.log(JSON.stringify({
      ok: true,
      node: process.version,
      puppeteerExecutablePath: puppeteer.executablePath?.() || null,
      executableOverride: process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROME_PATH || null,
      notes: [
        "If Chrome fails with missing .so libraries in Codespaces, run: npm run setup:linux",
        "Then rerun: npm run scrape:miata",
      ],
    }, null, 2));
    return;
  }

  const url = argValue("url");
  if (!url) {
    throw new Error("Missing --url=https://...");
  }

  const topic = slugify(argValue("topic", new URL(url).hostname));
  const sourceName = argValue("source-name", new URL(url).hostname);
  const role = argValue("role", "official_or_reputable_source");
  const outputRoot = path.resolve(REPO_ROOT, "cars/output/sources", topic);
  const screenshotDir = path.join(outputRoot, "screenshots");
  await ensureDir(screenshotDir);

  const executablePath =
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    undefined;

  const browser = await puppeteer.launch({
    headless: "new",
    executablePath,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-blink-features=AutomationControlled",
      "--disable-infobars",
      "--window-size=1366,768",
    ],
  });

  try {
    const page = await browser.newPage();
    await page.setUserAgent(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    );
    await page.setExtraHTTPHeaders({ "Accept-Language": "en-US,en;q=0.9" });
    await page.setViewport({ width: 1366, height: 768, deviceScaleFactor: 1 });
    await page.goto(url, { waitUntil: "networkidle2", timeout: 90000 });

    const packet = await extractPagePacket(page, { sourceName, role });
    packet.topic = topic;
    packet.screenshots = {
      viewport: path.join("screenshots", "viewport.png"),
      full_page: path.join("screenshots", "full-page.png"),
    };

    await page.screenshot({ path: path.join(screenshotDir, "viewport.png"), fullPage: false });
    await page.screenshot({ path: path.join(screenshotDir, "full-page.png"), fullPage: true });
    await fs.writeFile(path.join(outputRoot, "source-packet.json"), JSON.stringify(packet, null, 2));
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
