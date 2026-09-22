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

// Pose-to-pose in-between frames. The compositor used to crossfade two
// settled poses, which blends pixels but never moves anything -- the arm
// appeared to blink from one place to another. These capture the rig's own
// spring solver mid-flight, so the compositor can play a short flipbook of
// the arm actually travelling instead. Stepped manually at a fixed dt
// rather than by waiting on requestAnimationFrame, so the sequence is
// deterministic and doesn't depend on how fast the export machine renders.
// Non-uniform steps: the spring covers most of its distance early, so the
// samples are dense at the start and stretch out as it settles. Seven
// frames spanning 0.56s of motion this way leaves only a couple of percent
// of the travel unresolved at the last frame, where seven evenly spaced
// ones spanning 0.35s still had a visible snap left over at the end.
const TWEEN_STEPS = [0.03, 0.04, 0.05, 0.07, 0.09, 0.12, 0.16];
// Pose changes land in the pause between sentences (see
// _pose_intervals/SENTENCE_PAUSE_THRESHOLD_SECONDS in narrator_video.py),
// so the face is idle there: one closed-mouth, eyes-open, neutral-brow
// capture per in-between frame covers every transition instead of
// multiplying the whole face cross-product by TWEEN_FRAMES. The compositor
// only reaches for these while the mouth timeline is actually closed and
// falls back to the old crossfade otherwise.
const TWEEN_FACE = { mouth: "closed", eyes: "open", brows: "neutral" };

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

      // Stops the rig's own animation loop without snapping the joints to
      // their targets, which is what finishMotion() would do -- the whole
      // point of a tween capture is to sit between the two poses.
      window.__exportCancelMotion = () => {
        if (motionFrame !== null) cancelAnimationFrame(motionFrame);
        motionFrame = null;
        lastMotionTime = 0;
      };
      // motionTick() without the wave term, advanced by an explicit dt
      // instead of a frame clock -- same advanceJoint() integration, same
      // wrist follow-through, so a stepped frame matches the frame the
      // live rig would paint at that point in the motion.
      window.__exportStepMotion = (dt) => {
        for (const key of ["lu", "lf", "ru", "rf"]) {
          advanceJoint(joints[key], joints[key].target, dt);
        }
        for (const [key, forearm] of [["lh", "lf"], ["rh", "rf"]]) {
          const j = joints[key];
          const follow = clamp(-joints[forearm].velocity * 0.014, -2.5, 2.5);
          advanceJoint(j, clamp(j.target + follow, -12, 12), dt);
          if (j.angle > 12 || j.angle < -12) { j.angle = clamp(j.angle, -12, 12); j.velocity = 0; }
        }
        paintRig();
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

    // In-between frames, one sequence per ordered pair of distinct poses.
    // setPose() on top of a settled from-pose sets the targets and the
    // per-joint frequency exactly as it would live; stepping from there
    // captures the same easing the interactive rig plays.
    // Per-frame hold lengths, so the compositor plays the flipbook with the
    // same easing it was sampled at instead of at a flat frame rate.
    manifest.tween_steps = TWEEN_STEPS;
    manifest.tweens = {};
    await page.evaluate((face) => {
      window.__exportSetBrows(face.brows === "raised");
      setMouth(face.mouth);
      window.__exportSetEyes(face.eyes, 0);
    }, TWEEN_FACE);

    for (const fromPose of Object.keys(POSES)) {
      for (const toPose of Object.keys(POSES)) {
        if (fromPose === toPose) continue;
        await page.evaluate((args) => {
          setPose(args.from);
          finishMotion();
          setPose(args.to);
          // setPose() queues a real animation frame; cancel it so the only
          // thing advancing the solver is __exportStepMotion below.
          window.__exportCancelMotion();
        }, { from: POSES[fromPose], to: POSES[toPose] });

        const files = [];
        for (let frame = 0; frame < TWEEN_STEPS.length; frame += 1) {
          await page.evaluate((dt) => window.__exportStepMotion(dt), TWEEN_STEPS[frame]);
          const fileName = `tween_${fromPose}_to_${toPose}_${frame}.png`;
          await svgHandle.screenshot({ path: path.join(outDir, fileName), omitBackground: true });
          files.push(fileName);
          process.stderr.write(`wrote ${fileName}\n`);
        }
        manifest.tweens[`${fromPose}>${toPose}`] = files;
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
