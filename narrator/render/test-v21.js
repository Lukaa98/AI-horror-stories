// Real-browser regression checks for the capture API and the live buttons.
import assert from "node:assert/strict";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import puppeteer from "puppeteer-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const url = pathToFileURL(path.join(here, "../narrator-rig-v21.html"));
const executablePath = process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROME_PATH ||
  createRequire(path.resolve(here, "../../scraper/car-source-scraper/package.json"))("puppeteer").executablePath();
const browser = await puppeteer.launch({ headless: true,
  executablePath,
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
});
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewport({ width: 1100, height: 1200 });
  await page.goto(url.href, { waitUntil: "load" });
  // Switching either control must preserve the currently painted state.
  const initial = await page.evaluate(() => {
    narratorRig.startTalk();
    const before = document.querySelector("#character-placement").getAttribute("transform");
    document.querySelector('[data-layout="bottom-left"]').click();
    const after = document.querySelector("#character-placement").getAttribute("transform");
    return { before, after, mouth: narratorRig.getState().mouth };
  });
  assert.equal(initial.before, initial.after, "live placement should not teleport on click");
  assert.notEqual(initial.mouth, "closed");
  await page.waitForFunction(() => Math.abs(narratorRig.getState().tilt - 7) < .01);
  await page.evaluate(() => narratorRig.stopTalk());

  const plan = { duration: 12, safe_top: .66,
    shots: [
      { start: 0, layout: "bottom-right", framing: "half" },
      { start: 2.5, layout: "close-right", framing: "bust" },
      { start: 5, layout: "bottom-center", framing: "half" },
      { start: 7, layout: "bottom-left", framing: "half" },
      { start: 9.5, layout: "close-left", framing: "bust" },
      { start: 9.8, layout: "bottom-right", framing: "half" },
    ],
    gestures: [
      { start: .5, pose: "leftTalk" }, { start: 1.8, pose: "rest" },
      { start: 2.8, pose: "rightTalk" }, { start: 4.5, pose: "rest" },
      { start: 6, pose: "chest" }, { start: 6.25, pose: "open" },
      { start: 7.5, pose: "rest" },
    ],
    mouth_timeline: [{ start: 0, end: 11.8, mouth: "wide" }],
    expressions: [{ start: .3, end: 1.5, look_at: [270, 245], brows: true }],
  };
  const run = async () => page.evaluate(plan => {
    narratorRig.beginRender(plan);
    const samples = [];
    for (let frame = 0; frame < 12 * 24; frame++) {
      const state = narratorRig.renderAt(frame / 24);
      const head = document.querySelector("#head").getBoundingClientRect();
      const svg = document.querySelector("#character-svg").getBoundingClientRect();
      const ratio = 960 / svg.height;
      const bounds = { x: (head.x - svg.x) * ratio, y: (head.y - svg.y) * ratio,
        right: (head.right - svg.x) * ratio, bottom: (head.bottom - svg.y) * ratio };
      samples.push({ ...state, bounds,
        blinkVisible: document.querySelector("#eyes-blink").style.visibility === "visible",
        mouthVisible: document.querySelector("#mouth-wide").style.visibility === "visible" });
    }
    return samples;
  }, plan);
  const samples = await run();
  assert.deepEqual(await run(), samples, "capture must be repeatable");
  let maxJointStep = 0, maxXStep = 0;
  for (const [index, sample] of samples.entries()) {
    assert(sample.bounds.x >= 0 && sample.bounds.right <= 540, "face stays inside both sides");
    assert(sample.bounds.y >= plan.safe_top * 960 - 8 && sample.bounds.bottom <= 960, "face clears text and bottom");
    assert.equal(sample.blinking, sample.blinkVisible, "blink strips actually render");
    if (sample.time < 11.8) assert(sample.mouthVisible, "speech continues through blinks, gestures, and camera moves");
    assert(Math.abs(sample.joints.lh) <= 12 && Math.abs(sample.joints.rh) <= 12, "wrists stay bounded");
    if (index) {
      for (const key of Object.keys(sample.joints)) maxJointStep = Math.max(maxJointStep, Math.abs(sample.joints[key] - samples[index - 1].joints[key]));
      maxXStep = Math.max(maxXStep, Math.abs(sample.shot[0] - samples[index - 1].shot[0]));
    }
  }
  assert(maxJointStep > 0 && maxJointStep < 18, "arms move without snapping, including interrupted poses");
  assert(maxXStep > 0 && maxXStep < 55, "side changes travel through intermediate positions");
  assert(samples.some(s => s.blinking));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ frames: samples.length, maxJointStep, maxXStep, result: "passed" }));
} finally {
  await browser.close();
}
