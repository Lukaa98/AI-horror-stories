// Renders narrator/narrator-rig-v4.html to the same flipbook of transparent
// PNG sprites that export-sprites.js produces for the original hand-drawn
// rig, so cars/automation/narrator_video.py can composite this character
// without knowing anything changed.
//
// The v4 rig is articulated rather than pose-swapped: each arm is a
// shoulder -> forearm -> hand chain driven by rotation transforms and a
// spring solver, instead of two pre-drawn "straight" / "bent" variants. To
// capture a still, setPose() sets the joint targets and finishMotion()
// snaps the solver straight to them, which is why no settling delay is
// needed between captures.
//
// Two things the old rig had that this one doesn't, and how they're handled:
//   * No wobble filter. The old exporter baked two turbulence seeds into the
//     sprite key; here the key simply stops at the pose level, and
//     narrator_video.py's existing lookup falls back from
//     `${mouth}_${eyes}_${brows}_${pose}_${wobble}` to
//     `${mouth}_${eyes}_${brows}_${pose}` on its own.
//   * No separate look_left / look_right eye groups. Gaze is a translate on
//     the pupils, so those two states are captured by shifting the pupils
//     rather than swapping in a different drawing.
import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";
import puppeteer from "puppeteer-core";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const RIG_PATH = path.join(__dirname, "..", "narrator-rig-v4.html");

const MOUTHS = ["closed", "small", "wide", "smile"];
const EYES = ["open", "blink", "look_left", "look_right"];
const BROWS = ["neutral", "raised"];

// Matches the rig's own idleGaze offset, so an exported glance lands where
// the live preview puts it.
const GAZE_OFFSET_PX = 3.5;
// Matches raiseBrows() in the rig.
const BROW_RAISE_PX = 10;

// narrator_video.py's POSE_CYCLE is a fixed vocabulary (steady, jolt,
// lean_left, lean_right) that it cycles once per sentence, and
// POSE_LEAN_DIRECTION expects lean_left/lean_right to actually lean that
// way. Those names are kept as the sprite keys and mapped onto this rig's
// conversational poses, so the compositor needs no changes: the two "lean"
// beats become a gesture on the matching side, and the two flat beats
// become the resting stance and a smaller open-handed accent.
const POSES = {
  steady: "rest",
  jolt: "open",
  lean_left: "leftTalk",
  lean_right: "rightTalk",
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
  const outDir = path.resolve(argValue("out-dir", path.join(__dirname, "..", "sprites-v4")));
  await fs.mkdir(outDir, { recursive: true });

  const browser = await puppeteer.launch({
    headless: "new",
    executablePath: chromeExecutableOverride(),
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });

  try {
    const page = await browser.newPage();
    // Wide enough that the rig's two-column layout never wraps -- a wrapped
    // sidebar overlaps the stage and bleeds into the cropped screenshot.
    await page.setViewport({ width: 1100, height: 1300 });
    await page.goto(`file://${RIG_PATH}`, { waitUntil: "networkidle0" });

    await page.evaluate((browRaisePx) => {
      const style = document.createElement("style");
      // The stage paints a checkerboard behind the SVG for the in-browser
      // transparency preview; an element screenshot still composites through
      // ancestor backgrounds, so it has to be cleared here rather than
      // relying on omitBackground (which only drops the browser's own white
      // canvas). The label chip overlaps the same region and is positioned
      // absolutely, so it gets captured unless hidden outright.
      style.textContent = [
        "*, *::before, *::after { animation: none !important; transition: none !important; }",
        "html, body, .stage { background: transparent !important; }",
        ".stage-label { display: none !important; }",
        ".stage { border-radius: 0 !important; overflow: visible !important; border: none !important; }",
      ].join("\n");
      document.head.appendChild(style);

      // Stop every self-retriggering timer in the rig, otherwise a blink,
      // idle glance or idle smile can land between setting a state and
      // taking its screenshot and silently corrupt that sprite.
      autoBlink = false;
      tracking = false;
      autoGesture = false;
      clearTimeout(blinkTimer);
      clearTimeout(expressionTimer);
      clearTimeout(gazeTimer);
      clearTimeout(gestureTimer);
      if (typeof stopTalk === "function") stopTalk(false);

      // Expose the small state setters the capture loop needs, so each
      // page.evaluate below stays a one-liner against the rig's own API.
      window.__exportSetBrows = (raised) => {
        const brows = document.getElementById("eyebrows");
        if (raised) brows.setAttribute("transform", `translate(0 -${browRaisePx})`);
        else brows.removeAttribute("transform");
      };
      window.__exportSetEyes = (name, gaze) => {
        const open = document.getElementById("eyes-open");
        const blink = document.getElementById("eyes-blink");
        const show = (el, on) => {
          el.removeAttribute("hidden");
          el.style.visibility = on ? "visible" : "hidden";
          el.style.opacity = on ? "1" : "0";
        };
        show(open, name !== "blink");
        show(blink, name === "blink");
        const dx = name === "look_left" ? -gaze : name === "look_right" ? gaze : 0;
        document.querySelectorAll(".pupil").forEach((p) => {
          p.style.transform = `translate(${dx}px,0)`;
        });
      };
    }, BROW_RAISE_PX);

    const svgHandle = await page.$("#character-svg");

    const manifest = { sprites: {} };
    for (const [poseName, rigPose] of Object.entries(POSES)) {
      // finishMotion() cancels the pending animation frame and snaps every
      // joint to its target, so the capture is the settled pose rather than
      // an arbitrary frame of the spring easing into it.
      await page.evaluate((rigPoseName) => {
        setPose(rigPoseName);
        finishMotion();
      }, rigPose);

      for (const brows of BROWS) {
        await page.evaluate((raised) => window.__exportSetBrows(raised), brows === "raised");

        for (const mouth of MOUTHS) {
          await page.evaluate((mouthName) => setMouth(mouthName), mouth);

          for (const eyes of EYES) {
            await page.evaluate(
              (args) => window.__exportSetEyes(args.name, args.gaze),
              { name: eyes, gaze: GAZE_OFFSET_PX },
            );

            const fileName = `mouth-${mouth}_eyes-${eyes}_brows-${brows}_pose-${poseName}.png`;
            await svgHandle.screenshot({
              path: path.join(outDir, fileName),
              omitBackground: true,
            });
            manifest.sprites[`${mouth}_${eyes}_${brows}_${poseName}`] = fileName;
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
