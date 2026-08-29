// Renders narrator/narrator-rig.html to a fixed set of transparent PNG
// sprites, one per mouth x eye combination the mouth_timeline (see
// cars/automation/narrator_script.py) can reference. This trades a full
// per-frame video render of the HTML/CSS rig for a cheap flipbook: the
// Python compositor swaps between these sprites according to the timeline
// instead of driving a browser for every output frame.
//
// Idle animations (arm sway, head bob, the wobble filter) are disabled
// before capture so every sprite is a clean, reproducible snapshot rather
// than an arbitrary frame of a running CSS animation -- the render step
// trades that idle motion away for determinism. Re-adding a few "phase
// variants" per state is a reasonable follow-up if the flipbook reads too
// stiff, but a static-per-state export is the simplest thing that works
// first.
import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";
import puppeteer from "puppeteer-core";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const RIG_PATH = path.join(__dirname, "..", "narrator-rig.html");

const MOUTHS = ["closed", "small", "wide", "smile"];
const EYES = ["open", "blink"];

function chromeExecutableOverride() {
  return process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROME_PATH || undefined;
}

function argValue(name, fallback) {
  const prefix = `--${name}=`;
  const match = process.argv.find((arg) => arg.startsWith(prefix));
  return match ? match.slice(prefix.length) : fallback;
}

async function main() {
  const outDir = path.resolve(argValue("out-dir", path.join(__dirname, "sprites")));
  await fs.mkdir(outDir, { recursive: true });

  const browser = await puppeteer.launch({
    headless: "new",
    executablePath: chromeExecutableOverride(),
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });

  try {
    const page = await browser.newPage();
    // Wide enough that the two-column layout never wraps -- at narrower
    // viewports the sidebar panel drops below the stage and bleeds into
    // the clipped screenshot.
    await page.setViewport({ width: 1100, height: 1300 });
    await page.goto(`file://${RIG_PATH}`, { waitUntil: "networkidle0" });

    // Freeze every idle animation and timer so each capture is a clean,
    // reproducible snapshot instead of an arbitrary animation frame.
    await page.evaluate(() => {
      const style = document.createElement("style");
      // The stage div paints a checkerboard behind the SVG for the
      // in-browser transparency preview -- cropping a page screenshot
      // still composites through ancestor backgrounds, so that checkerboard
      // has to be turned off here rather than relying on omitBackground
      // alone (which only strips the browser's own default white canvas).
      style.textContent = [
        "*, *::before, *::after { animation: none !important; transition: none !important; }",
        "html, body, .stage { background: transparent !important; }",
        // An element screenshot is really just a raster crop of the
        // rendered page at that element's bounding rect, so an absolutely
        // positioned sibling overlapping the same screen region (the
        // "transparent stage" label chip) gets captured too unless it's
        // hidden outright.
        ".stage-label { display: none !important; }",
        ".stage { border-radius: 0 !important; overflow: visible !important; border: none !important; }",
      ].join("\n");
      document.head.appendChild(style);
      if (typeof stopBlink === "function") stopBlink();
      if (typeof stopWobble === "function") stopWobble();
      if (typeof stopTalk === "function") stopTalk();
      const charEl = document.getElementById("character");
      if (charEl) charEl.removeAttribute("filter");
    });

    const svgHandle = await page.$("#charSvg");

    const manifest = { sprites: {} };
    for (const mouth of MOUTHS) {
      for (const eyes of EYES) {
        await page.evaluate((mouthName, eyesName) => {
          setMouth(mouthName);
          const eyeOpen = document.querySelector(".eyes-open");
          const eyeBlink = document.querySelector(".eyes-blink");
          eyeOpen.style.display = eyesName === "open" ? "" : "none";
          eyeBlink.style.display = eyesName === "blink" ? "" : "none";
        }, mouth, eyes);

        const fileName = `mouth-${mouth}_eyes-${eyes}.png`;
        const filePath = path.join(outDir, fileName);
        await svgHandle.screenshot({
          path: filePath,
          omitBackground: true,
        });
        manifest.sprites[`${mouth}_${eyes}`] = fileName;
        process.stderr.write(`wrote ${fileName}\n`);
      }
    }

    await fs.writeFile(
      path.join(outDir, "sprites.json"),
      JSON.stringify(manifest, null, 2),
      "utf-8",
    );
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
