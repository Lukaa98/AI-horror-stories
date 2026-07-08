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

function slugify(value) {
  return String(value || "car-source")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "car-source";
}

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

function extensionFromContentType(contentType) {
  if (!contentType) return ".jpg";
  if (contentType.includes("png")) return ".png";
  if (contentType.includes("webp")) return ".webp";
  if (contentType.includes("gif")) return ".gif";
  return ".jpg";
}

async function downloadImageAssets(page, packet, outputRoot) {
  const imagesDir = path.join(outputRoot, "images");
  await ensureDir(imagesDir);
  const urls = Array.from(new Set([packet.og_image, ...packet.image_urls].filter(Boolean))).slice(0, 12);
  const downloaded = [];

  for (const [index, imageUrl] of urls.entries()) {
    try {
      const response = await page.goto(imageUrl, { waitUntil: "networkidle2", timeout: 45000 });
      if (!response || !response.ok()) {
        continue;
      }
      const contentType = response.headers()["content-type"] || "";
      if (!contentType.startsWith("image/")) {
        continue;
      }
      const buffer = await response.buffer();
      if (buffer.length < 10_000) {
        continue;
      }
      const relativePath = path.join("images", `source-image-${String(index + 1).padStart(2, "0")}${extensionFromContentType(contentType)}`);
      await fs.writeFile(path.join(outputRoot, relativePath), buffer);
      downloaded.push({
        source_url: imageUrl,
        path: relativePath,
        content_type: contentType,
        bytes: buffer.length,
      });
    } catch (error) {
      downloaded.push({
        source_url: imageUrl,
        error: String(error?.message || error),
      });
    }
  }

  return downloaded;
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
        notes: [
          "Chrome launched successfully. You can run: npm run scrape:miata",
        ],
      }, null, 2));
    } catch (error) {
      console.error(JSON.stringify({
        ok: false,
        node: process.version,
        puppeteerExecutablePath: puppeteer.executablePath?.() || null,
        executableOverride: chromeExecutableOverride() || null,
        error: String(error?.message || error),
        next_steps: [
          "Run: npm run setup:linux",
          "Then rerun: npm run doctor",
          "Then rerun: npm run scrape:miata",
        ],
      }, null, 2));
      throw error;
    } finally {
      if (browser) {
        await browser.close();
      }
    }
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
    await page.goto(url, { waitUntil: "networkidle2", timeout: 90000 });

    const packet = await extractPagePacket(page, { sourceName, role });
    packet.topic = topic;
    packet.screenshots = {
      viewport: path.join("screenshots", "viewport.png"),
      full_page: path.join("screenshots", "full-page.png"),
    };

    await page.screenshot({ path: path.join(screenshotDir, "viewport.png"), fullPage: false });
    await page.screenshot({ path: path.join(screenshotDir, "full-page.png"), fullPage: true });

    const assetPage = await browser.newPage();
    packet.downloaded_images = await downloadImageAssets(assetPage, packet, outputRoot);
    await assetPage.close();

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
