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
// Four poses now instead of two: steady/jolt are the fast, small "redraw
// flicker" (line weight + a tiny arm/brow twitch, no lean), while
// lean_left/lean_right are a bigger, deliberate weight-shift -- one arm
// swaps to the bent-elbow variant (hand resting near the belly instead of
// hanging straight), that side's leg rotates outward at the hip, and the
// whole body tilts a few degrees toward that side. narrator_video.py
// cycles through all four per sentence (not on a fixed clock) with a
// crossfade between them, so the switch reads as a transition into the
// new stance rather than a jump cut.
const POSES = {
  steady: {
    strokeWidth: 4.6,
    armLeft: "rotate(-1.5deg)", armRight: "rotate(1.5deg)",
    armLeftBent: false, armRightBent: false,
    legLeft: "none", legRight: "none",
    bodyTilt: "none",
    brows: "neutral",
  },
  jolt: {
    strokeWidth: 3.9,
    armLeft: "rotate(1.5deg)", armRight: "rotate(-1.5deg)",
    armLeftBent: false, armRightBent: false,
    legLeft: "none", legRight: "none",
    bodyTilt: "none",
    brows: "talk",
  },
  lean_left: {
    strokeWidth: 4.4,
    armLeft: "none", armRight: "rotate(-2deg)",
    armLeftBent: true, armRightBent: false,
    legLeft: "rotate(-4deg)", legRight: "rotate(2deg)",
    bodyTilt: "rotate(-3deg)",
    brows: "neutral",
  },
  lean_right: {
    strokeWidth: 4.4,
    armLeft: "rotate(2deg)", armRight: "none",
    armLeftBent: false, armRightBent: true,
    legLeft: "rotate(-2deg)", legRight: "rotate(4deg)",
    bodyTilt: "rotate(3deg)",
    brows: "talk",
  },
};

// Two fixed turbulence seeds for the #wobble filter's hand-drawn outline
// jitter -- the live rig retriggers this on a random ~280-500ms timer
// (wobbleTick), which a static sprite export can't reproduce continuously,
// but cycling between a couple of fixed-seed captures in narrator_video.py
// approximates the same "alive, slightly redrawn" skin/edge flicker
// instead of a perfectly static outline.
const WOBBLE_SEEDS = { a: 3, b: 47 };

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
      // stopWobble() (above) stops the rig's own randomly-retriggering
      // timer but also strips the filter="url(#wobble)" attribute
      // entirely as part of turning the effect off for the live preview --
      // re-apply it here so the filter itself stays active, and
      // WOBBLE_SEEDS below captures it at a couple of fixed seeds instead
      // of the rig's own random timer. Without this the hand-drawn
      // "redrawn outline" jitter never reached the exported sprites (and
      // so never reached the shipped video) even though it was applied
      // per pose below -- setting a seed on an element with no filter
      // attribute silently does nothing.
      const charEl = document.getElementById("character");
      if (charEl) charEl.setAttribute("filter", "url(#wobble)");
    });

    const svgHandle = await page.$("#charSvg");

    const manifest = { sprites: {} };
    for (const [poseName, pose] of Object.entries(POSES)) {
      await page.evaluate((pose) => {
        const charEl = document.getElementById("character");
        charEl.style.strokeWidth = String(pose.strokeWidth);
        charEl.style.transform = pose.bodyTilt === "none" ? "" : pose.bodyTilt;

        const armLeft = document.querySelector(".arm-left");
        const armLeftBent = document.querySelector(".arm-left-bent");
        armLeft.style.display = pose.armLeftBent ? "none" : "";
        armLeftBent.style.display = pose.armLeftBent ? "" : "none";
        armLeft.style.transform = pose.armLeft === "none" ? "" : pose.armLeft;

        const armRight = document.querySelector(".arm-right");
        const armRightBent = document.querySelector(".arm-right-bent");
        armRight.style.display = pose.armRightBent ? "none" : "";
        armRightBent.style.display = pose.armRightBent ? "" : "none";
        armRight.style.transform = pose.armRight === "none" ? "" : pose.armRight;

        document.querySelector(".leg-left").style.transform = pose.legLeft === "none" ? "" : pose.legLeft;
        document.querySelector(".leg-right").style.transform = pose.legRight === "none" ? "" : pose.legRight;

        const neutral = document.querySelector(".brows-neutral");
        const raised = document.querySelector(".brows-raised");
        const talk = document.querySelector(".brows-talk");
        neutral.style.display = pose.brows === "neutral" ? "" : "none";
        raised.style.display = pose.brows === "raised" ? "" : "none";
        talk.style.display = pose.brows === "talk" ? "" : "none";
      }, pose);

      for (const [wobbleName, wobbleSeed] of Object.entries(WOBBLE_SEEDS)) {
        await page.evaluate((seed) => {
          document.getElementById("turb").setAttribute("seed", String(seed));
        }, wobbleSeed);

        for (const mouth of MOUTHS) {
          for (const eyes of EYES) {
            await page.evaluate((mouthName, eyesName) => {
              setMouth(mouthName);
              const eyeOpen = document.querySelector(".eyes-open");
              const eyeBlink = document.querySelector(".eyes-blink");
              eyeOpen.style.display = eyesName === "open" ? "" : "none";
              eyeBlink.style.display = eyesName === "blink" ? "" : "none";
            }, mouth, eyes);

            const fileName = `mouth-${mouth}_eyes-${eyes}_pose-${poseName}_wobble-${wobbleName}.png`;
            const filePath = path.join(outDir, fileName);
            await svgHandle.screenshot({
              path: filePath,
              omitBackground: true,
            });
            manifest.sprites[`${mouth}_${eyes}_${poseName}_${wobbleName}`] = fileName;
            process.stderr.write(`wrote ${fileName}\n`);
          }
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
