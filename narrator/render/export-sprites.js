// Renders narrator/narrator-rig.html to a fixed set of transparent PNG
// sprites, one per mouth x eyes x flicker-pose combination the video
// compositor (cars/automation/narrator_video.py) can reference. This trades
// a full per-frame video render of the HTML/CSS rig for a cheap flipbook:
// the Python compositor swaps between these sprites according to the mouth
// timeline (loudness-driven) and a flicker cycle (time-driven), and applies
// the smooth body lean itself as a continuous per-frame rotation, instead
// of driving a browser for every output frame.
//
// Idle animations (arm sway, head bob, body sway, line flicker, the wobble
// filter) are disabled before capture so every sprite is a clean,
// reproducible snapshot rather than an arbitrary frame of a running CSS
// animation. The body-wide lean is smooth and continuous in CSS, but baking
// several lean angles into discrete sprites and cycling through them made it
// look like a jerky snap between 3 pictures instead of a smooth motion, so
// that lean is applied at the video-compositing level instead (see
// narrator_video.py's _apply_body_sway) -- the two POSES below only bake in
// the fast, small hand-drawn "redraw" flicker (line weight + a tiny arm/brow
// twitch), which is meant to read as a discrete jump-cut, not a smooth one.
import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";
import puppeteer from "puppeteer-core";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const RIG_PATH = path.join(__dirname, "..", "narrator-rig.html");

const MOUTHS = ["closed", "small", "wide", "smile"];
const EYES = ["open", "blink"];
const POSES = {
  steady: {
    strokeWidth: 4.6,
    armLeft: "rotate(-1.5deg)",
    armRight: "rotate(1.5deg)",
    brows: "neutral",
  },
  jolt: {
    strokeWidth: 3.9,
    armLeft: "rotate(1.5deg)",
    armRight: "rotate(-1.5deg)",
    brows: "talk",
  },
};

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
    for (const [poseName, pose] of Object.entries(POSES)) {
      await page.evaluate((pose) => {
        const charEl = document.getElementById("character");
        charEl.style.strokeWidth = String(pose.strokeWidth);
        document.querySelector(".arm-left").style.transform = pose.armLeft;
        document.querySelector(".arm-right").style.transform = pose.armRight;
        const neutral = document.querySelector(".brows-neutral");
        const raised = document.querySelector(".brows-raised");
        const talk = document.querySelector(".brows-talk");
        neutral.style.display = pose.brows === "neutral" ? "" : "none";
        raised.style.display = pose.brows === "raised" ? "" : "none";
        talk.style.display = pose.brows === "talk" ? "" : "none";
      }, pose);

      for (const mouth of MOUTHS) {
        for (const eyes of EYES) {
          await page.evaluate((mouthName, eyesName) => {
            setMouth(mouthName);
            const eyeOpen = document.querySelector(".eyes-open");
            const eyeBlink = document.querySelector(".eyes-blink");
            eyeOpen.style.display = eyesName === "open" ? "" : "none";
            eyeBlink.style.display = eyesName === "blink" ? "" : "none";
          }, mouth, eyes);

          const fileName = `mouth-${mouth}_eyes-${eyes}_pose-${poseName}.png`;
          const filePath = path.join(outDir, fileName);
          await svgHandle.screenshot({
            path: filePath,
            omitBackground: true,
          });
          manifest.sprites[`${mouth}_${eyes}_${poseName}`] = fileName;
          process.stderr.write(`wrote ${fileName}\n`);
        }
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
