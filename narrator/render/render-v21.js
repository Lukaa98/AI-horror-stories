// Capture the actual V21 rig at every output frame, including independent
// lip sync, blinks, arm/wrist motion, and eased placement/zoom transitions.
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawn } from "node:child_process";
import puppeteer from "puppeteer-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const argument = (name) => {
  const index = process.argv.indexOf("--" + name);
  return index >= 0 ? process.argv[index + 1] : undefined;
};

function chromePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROME_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROME_PATH;
  }
  // Actions already installs Chromium with the source scraper. Reuse it.
  try {
    const require = createRequire(path.join(root, "scraper/car-source-scraper/package.json"));
    return require("puppeteer").executablePath();
  } catch {
    throw new Error("Set PUPPETEER_EXECUTABLE_PATH to Chrome, or install the scraper's Puppeteer browser.");
  }
}

async function main() {
  const planPath = argument("plan"), output = argument("output");
  if (!planPath || !output) throw new Error("Usage: node render-v21.js --plan motion.json --output narrator.mov");
  const plan = JSON.parse(await fs.readFile(planPath, "utf8"));
  if (![plan.duration, plan.fps, plan.width, plan.height].every(n => Number.isFinite(n) && n > 0)) {
    throw new Error("Plan needs positive duration, fps, width, and height.");
  }
  await fs.mkdir(path.dirname(path.resolve(output)), { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: chromePath(), headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
  });
  let encoder, encoderError = "";
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.setViewport({ width: plan.width, height: plan.height, deviceScaleFactor: 1 });
    const url = pathToFileURL(path.join(root, "narrator/narrator-rig-v21.html"));
    url.searchParams.set("render", "1");
    await page.goto(url.href, { waitUntil: "load" });
    await page.addStyleTag({ content: [
      "html,body,.stage{background:transparent!important;margin:0!important;padding:0!important}",
      "header,.panel,.stage-label{display:none!important}",
      ".rig{display:block!important;width:100%!important;margin:0!important}",
      ".stage-column{position:static!important}",
      ".stage{position:absolute!important;inset:0!important;width:100vw!important;height:100vh!important;border:0!important;border-radius:0!important}",
      "*,*::before,*::after{animation:none!important;transition:none!important}",
    ].join("\n") });
    await page.evaluate(p => window.narratorRig.beginRender(p), plan);
    if (errors.length) throw new Error(errors.join("\n"));
    // QTRLE preserves the alpha channel for MoviePy's has_mask=True reader.
    // This is a temporary compositor input, not a published video artifact.
    encoder = spawn(process.env.FFMPEG_BINARY || "ffmpeg", [
      "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe",
      "-framerate", String(plan.fps), "-vcodec", "png", "-i", "pipe:0",
      "-an", "-c:v", "qtrle", "-pix_fmt", "argb", path.resolve(output),
    ], { stdio: ["pipe", "ignore", "pipe"] });
    encoder.stderr.on("data", chunk => { encoderError = (encoderError + chunk).slice(-6000); });
    const finished = new Promise(resolve => {
      encoder.on("error", error => { encoderError += error.message; resolve(-1); });
      encoder.on("close", code => resolve(code));
    });
    encoder.stdin.on("error", () => {}); // Each write callback handles errors.
    const frames = Math.ceil(plan.duration * plan.fps);
    for (let frame = 0; frame < frames; frame++) {
      await page.evaluate(t => window.narratorRig.renderAt(t), frame / plan.fps);
      if (errors.length) throw new Error(errors.join("\n"));
      const png = await page.screenshot({ type: "png", omitBackground: true, captureBeyondViewport: false });
      await new Promise((resolve, reject) => encoder.stdin.write(png, error => error ? reject(error) : resolve()));
      if (frame % (plan.fps * 5) === 0) process.stderr.write("V21 narrator: " + frame + "/" + frames + " frames\n");
    }
    encoder.stdin.end();
    const code = await finished;
    if (code !== 0) throw new Error("Narrator ffmpeg failed: " + encoderError);
    process.stderr.write("V21 narrator captured: " + frames + " frames\n");
  } catch (error) {
    if (encoder && encoder.exitCode === null) encoder.kill();
    await fs.rm(output, { force: true });
    throw error;
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
