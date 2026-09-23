// Read one Cars & Bids listing's *text* -- no photos, no gallery walk.
//
// The listing states this exact car's engine, output, drivetrain and what it
// actually sold for, on a dated page. Web search gives the model's numbers;
// this gives this car's, which is what makes "it costs about..." a fact
// rather than an estimate. It is deliberately a separate script from
// scrape-carsandbids-gallery.js: one page load, a short timeout, and it
// fails open, because the script must still be written when this comes back
// empty.
//
//   node src/scrape-carsandbids-facts.js --auction-url=... --out-json=...
import fs from "fs/promises";
import path from "path";
import puppeteer from "puppeteer-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";

puppeteer.use(StealthPlugin());

const LAUNCH_ARGS = [
  "--no-sandbox",
  "--disable-setuid-sandbox",
  "--disable-blink-features=AutomationControlled",
  "--disable-infobars",
  "--window-size=1440,1200",
];

// Headings worth keeping. Ownership history, service records and known
// flaws are about one used car's paperwork, not about the model, and the
// narration is not meant to read them out.
const WANTED_SECTIONS = "^(highlights|equipment|dougs? take|seller notes)";

function argValue(name, fallback = null) {
  const prefix = `--${name}=`;
  const match = process.argv.find((arg) => arg.startsWith(prefix));
  return match ? match.slice(prefix.length) : fallback;
}

function chromeExecutableOverride() {
  return process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROME_PATH || undefined;
}

async function extractFacts(page, auctionUrl, timeoutMs) {
  await page.goto(auctionUrl, { waitUntil: "domcontentloaded", timeout: timeoutMs });
  // The quick-facts list is rendered client-side; wait for it, but treat a
  // miss as "no facts" rather than as a failure -- the page may still have
  // usable prose.
  await page.waitForSelector("dl dt, .quick-facts", { timeout: Math.min(20000, timeoutMs) }).catch(() => {});
  return page.evaluate((wantedSource) => {
    const wanted = new RegExp(wantedSource, "i");
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();

    // Key/value pairs, however they are marked up: the spec table is a
    // definition list today, and reading every dt/dd on the page survives
    // it moving or being renamed.
    const facts = {};
    for (const dt of document.querySelectorAll("dt")) {
      const key = clean(dt.innerText);
      // Trailing UI words ride along in innerText -- the Model row came back
      // as "993 911 Save" because a Save button sits inside the cell.
      const value = clean(dt.nextElementSibling?.innerText)
        .replace(/\s+(Save|Follow|Watch|Share)$/i, "");
      if (key && value && key.length < 40 && value.length < 200) facts[key] = value;
    }

    const bodyText = clean(document.body?.innerText).slice(0, 20000);
    // Both states, because they are different facts. An ended auction has a
    // sale price; a live one has a bid that has not bought anything yet.
    // Matching only the ended wording left a live listing with no price at
    // all, and the script then took a number from search -- which for a
    // 993 Turbo returned a base Carrera's $70K against a car bid to $267K.
    const soldMatch = bodyText.match(/(Sold for|Winning bid)\s*\$[\d,]+/i);
    const liveMatch = bodyText.match(/(High Bid|Current Bid|Bid to)\s*\$[\d,]+/i);
    // Whichever appears first, not whichever kind we prefer. This car's
    // price is in the header; a page also lists other auctions further
    // down, so a live listing that shows a sold comparable underneath it
    // would otherwise report that car's price as this one's.
    const found = [soldMatch, liveMatch].filter(Boolean).sort((a, b) => a.index - b.index);
    const priceMatch = found[0] || null;
    const auctionState = !priceMatch ? "unknown"
      : priceMatch === soldMatch ? "sold" : "bidding";

    const sections = {};
    for (const heading of document.querySelectorAll("h2, h3, h4")) {
      const title = clean(heading.innerText);
      if (!title || !wanted.test(title)) continue;
      // Everything up to the next heading of the same kind is that
      // section's body.
      const parts = [];
      let node = heading.nextElementSibling;
      while (node && !/^H[234]$/.test(node.tagName)) {
        const text = clean(node.innerText);
        if (text) parts.push(text);
        node = node.nextElementSibling;
      }
      const body = clean(parts.join(" "));
      if (body) sections[title] = body.slice(0, 4000);
    }

    return {
      title: clean(document.querySelector("h1")?.innerText) || clean(document.title),
      price_text: priceMatch ? clean(priceMatch[0]) : "",
      auction_state: auctionState,
      facts,
      sections,
    };
  }, WANTED_SECTIONS);
}

async function main() {
  const auctionUrl = argValue("auction-url");
  const outJson = argValue("out-json");
  const timeoutMs = Number(argValue("timeout-ms", "60000")) || 60000;
  if (!auctionUrl || !/^https:\/\/carsandbids\.com\/auctions\//i.test(auctionUrl)) {
    throw new Error("--auction-url must be a carsandbids.com/auctions/... listing URL.");
  }
  if (!outJson) throw new Error("--out-json is required.");

  const browser = await puppeteer.launch({
    headless: "new",
    executablePath: chromeExecutableOverride(),
    args: LAUNCH_ARGS,
  });
  let result = { auction_url: auctionUrl, title: "", price_text: "", facts: {}, sections: {} };
  try {
    const page = await browser.newPage();
    await page.setUserAgent(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    );
    await page.setExtraHTTPHeaders({ "Accept-Language": "en-US,en;q=0.9" });
    await page.setViewport({ width: 1440, height: 1200, deviceScaleFactor: 1 });
    result = { auction_url: auctionUrl, ...(await extractFacts(page, auctionUrl, timeoutMs)) };
  } catch (err) {
    // Fails open on purpose: an empty read means the script is written from
    // web search alone, exactly as it was before this existed.
    result.error = String(err && err.message ? err.message : err);
  } finally {
    await browser.close().catch(() => {});
  }
  await fs.mkdir(path.dirname(path.resolve(outJson)), { recursive: true });
  await fs.writeFile(outJson, JSON.stringify(result, null, 2), "utf8");
  process.stdout.write(`${JSON.stringify({ ok: !result.error, facts: Object.keys(result.facts).length, sections: Object.keys(result.sections).length })}\n`);
}

main().catch(async (err) => {
  process.stderr.write(`${String(err && err.message ? err.message : err)}\n`);
  process.exit(1);
});
