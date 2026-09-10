// Renders narrator/narrator-rig-v3.html (the AI-illustrated full-body
// character) to a fixed set of transparent PNG sprites, one per
// mouth x eyes x brows combination -- mirrors export-sprites.js's approach
// for the hand-drawn rig, but with no pose/wobble dimension: there's only
// one flat base image (no separately-rigged arms/legs to pose), so every
// sprite differs only in the mouth/eyes/brows overlay drawn on top of it.
//
// narrator_video.py's existing sprite-lookup fallback chain already tries
// "{mouth}_{eyes}_{brows}" once the more specific pose/wobble keys miss, so
// keying this manifest at exactly that level means no narrator_video.py
// change is needed to make these sprites resolve.
import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";
import puppeteer from "puppeteer-core";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const RIG_PATH = path.join(__dirname, "..", "narrator-rig-v3.html");

const MOUTHS = ["closed", "small", "wide", "smile"];
const BROWS = ["neutral", "raised"];

function chromeExecutableOverride() {
  return process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROME_PATH || undefined;
}

function argValue(name, fallback) {
  const prefix = `--${name}=`;
  const match = process.argv.find((arg) => arg.startsWith(prefix));
  return match ? match.slice(prefix.length) : fallback;
}

async function main() {
  const outDir = path.resolve(argValue("out-dir", path.join(__dirname, "sprites-v3")));
  await fs.mkdir(outDir, { recursive: true });

  const browser = await puppeteer.launch({
    headless: "new",
    executablePath: chromeExecutableOverride(),
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });

  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1200, height: 1600 });
    await page.goto(`file://${RIG_PATH}`, { waitUntil: "networkidle0" });

    await page.evaluate(() => {
      const style = document.createElement("style");
      style.textContent = [
        "*, *::before, *::after { animation: none !important; transition: none !important; }",
        "html, body, .stage { background: transparent !important; }",
        ".stage-label { display: none !important; }",
        ".stage { border-radius: 0 !important; overflow: visible !important; border: none !important; }",
      ].join("\n");
      document.head.appendChild(style);
      if (typeof stopBlink === "function") stopBlink();
      if (typeof stopWobble === "function") stopWobble();
      if (typeof stopTalk === "function") stopTalk();
      const charEl = document.getElementById("character");
      if (charEl) charEl.setAttribute("filter", "url(#wobble)");
    });

    const svgHandle = await page.$("#charSvg");

    const manifest = { sprites: {} };
    for (const brows of BROWS) {
      await page.evaluate((browsName) => {
        const neutral = document.querySelector(".brows-neutral");
        const raised = document.querySelector(".brows-raised");
        const talk = document.querySelector(".brows-talk");
        neutral.style.display = browsName === "neutral" ? "" : "none";
        raised.style.display = browsName === "raised" ? "" : "none";
        talk.style.display = "none";
      }, brows);

      for (const mouth of MOUTHS) {
        for (const eyes of ["open", "blink"]) {
          await page.evaluate((mouthName, eyesName) => {
            setMouth(mouthName);
            const eyeOpen = document.querySelector(".eyes-open");
            const eyeBlink = document.querySelector(".eyes-blink");
            eyeOpen.style.display = eyesName === "open" ? "" : "none";
            eyeBlink.style.display = eyesName === "blink" ? "" : "none";
          }, mouth, eyes);

          const fileName = `mouth-${mouth}_eyes-${eyes}_brows-${brows}.png`;
          const filePath = path.join(outDir, fileName);
          await svgHandle.screenshot({ path: filePath, omitBackground: true });
          manifest.sprites[`${mouth}_${eyes}_${brows}`] = fileName;
          // narrator_video.py's blink timeline can briefly override
          // whichever look-direction is showing (look_left/look_right),
          // which this rig has no separate visual for -- alias both to
          // the plain "open" sprite so that lookup always resolves
          // instead of leaving a silent gap in the rendered video.
          if (eyes === "open") {
            manifest.sprites[`${mouth}_look_left_${brows}`] = fileName;
            manifest.sprites[`${mouth}_look_right_${brows}`] = fileName;
          }
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
