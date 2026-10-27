// Stills of the v21 narrator for the thumbnail, with the chest box the
// print goes in.
//
// The thumbnail used narrator/sprites, which is the legacy set the video
// stopped using when it moved to the v21 rig -- so every thumbnail carried
// a character the channel no longer uses. These come off the same rig the
// video does.
//
// The chest rect is read from the rig rather than measured off a render.
// Eyeballing it put the print on his collar once already, and #chest-car
// knows exactly where it is.
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import puppeteer from "puppeteer-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const OUT = path.join(root, "narrator/thumbnail-poses");
const WIDTH = 620, HEIGHT = 1040;
const BLANK = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";

// Standing and closer, which is the axis that matters in a grid: some
// full-length, some head and chest. "rest" is the only pose that leaves
// the chest clear -- the others put a hand across it.
const SHOTS = [
  { name: "stand-rest",     framing: "full", pose: "rest",  mouth: "smile" },
  { name: "stand-open",     framing: "full", pose: "open",  mouth: "smile" },
  { name: "stand-closed",   framing: "full", pose: "rest",  mouth: "closed" },
  { name: "stand-wave",     framing: "full", pose: "wave",  mouth: "smile" },
  { name: "close-rest",     framing: "half", pose: "rest",  mouth: "smile" },
  { name: "close-open",     framing: "half", pose: "open",  mouth: "small" },
  { name: "bust-rest",      framing: "bust", pose: "rest",  mouth: "smile" },
  { name: "bust-closed",    framing: "bust", pose: "rest",  mouth: "closed" },
];

function chromePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROME_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROME_PATH;
  }
  const require = createRequire(path.join(root, "scraper/car-source-scraper/package.json"));
  return require("puppeteer").executablePath();
}

async function main() {
  await fs.mkdir(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: chromePath(), headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
  });
  const index = {};
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.setViewport({ width: WIDTH, height: HEIGHT, deviceScaleFactor: 1 });
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
    // A blank hoodie: the thumbnail prints the build's own car on it.
    await page.evaluate((blank) => {
      const node = document.getElementById("chest-car");
      node.setAttribute("href", blank);
      node.removeAttribute("xlink:href");
    }, BLANK);

    for (const shot of SHOTS) {
      await page.evaluate((s, w, h) => window.narratorRig.beginRender({
        duration: 4, fps: 24, width: w, height: h,
        shots: [{ start: 0, layout: "center", framing: s.framing }],
        gestures: [{ start: 0, pose: s.pose }],
        mouth_timeline: [{ start: 0, end: 4, mouth: s.mouth }],
        expressions: [],
      }), shot, WIDTH, HEIGHT);
      // Half a second in: past the pose cue, before the first blink at 1.1s.
      await page.evaluate(() => window.narratorRig.renderAt(0.5));
      const png = await page.screenshot({ type: "png", omitBackground: true, captureBeyondViewport: false });
      await fs.writeFile(path.join(OUT, `${shot.name}.png`), png);
      const chest = await page.evaluate(() => {
        const r = document.getElementById("chest-car").getBoundingClientRect();
        return { x: r.x, y: r.y, width: r.width, height: r.height };
      });
      index[shot.name] = { ...shot, chest, canvas: { width: WIDTH, height: HEIGHT } };
      console.log(`${shot.name}: chest ${Math.round(chest.x)},${Math.round(chest.y)} ` +
                  `${Math.round(chest.width)}x${Math.round(chest.height)}`);
    }
    if (errors.length) throw new Error(errors.join("\n"));
    await fs.writeFile(path.join(OUT, "poses.json"), JSON.stringify(index, null, 2));
  } finally {
    await browser.close();
  }
  console.log(`Wrote ${SHOTS.length} poses to ${OUT}`);
}

main().catch(e => { console.error(e); process.exit(1); });
