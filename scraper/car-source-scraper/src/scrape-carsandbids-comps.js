// What this model actually sells for, from the site's own results page.
//
// A single auction is one data point about one car: a live bid is an
// unfinished event, and an ended one is whatever that particular example
// was worth. A video outlives both. The results page carries dated sale
// prices for the same model, which is the number a viewer actually wants.
//
// The model page is not guessed from make and model -- "911 Turbo" lives
// under /search/porsche/993-911, which nothing in the manifest knows. The
// listing links to it ("see more Porsche 993 911 auctions here"), so this
// follows that link from the auction we were already given.
//
//   node src/scrape-carsandbids-comps.js --auction-url=... --out-json=...
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
  "--window-size=1440,1600",
];

function argValue(name, fallback = null) {
  const prefix = `--${name}=`;
  const match = process.argv.find((arg) => arg.startsWith(prefix));
  return match ? match.slice(prefix.length) : fallback;
}

function chromeExecutableOverride() {
  return process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROME_PATH || undefined;
}

async function findModelPage(page, auctionUrl, timeoutMs) {
  await page.goto(auctionUrl, { waitUntil: "domcontentloaded", timeout: timeoutMs });
  return page.evaluate(() => {
    const links = [...document.querySelectorAll('a[href*="/search/"]')];
    // The model link has two path segments after /search/ (make and model);
    // a bare /search/porsche is the make page and too broad to be comps.
    const scored = links
      .map((a) => a.getAttribute("href") || "")
      .filter((href) => /\/search\/[^/?#]+\/[^/?#]+/.test(href));
    return scored.length ? new URL(scored[0], location.origin).toString() : "";
  });
}

async function readResults(page, modelUrl, timeoutMs) {
  await page.goto(modelUrl, { waitUntil: "domcontentloaded", timeout: timeoutMs });
  await page.waitForSelector('a[href*="/auctions/"]', { timeout: Math.min(20000, timeoutMs) })
    .catch(() => {});
  // Give the client-rendered result list a moment to fill in.
  await new Promise((resolve) => setTimeout(resolve, 2500));

  return page.evaluate(() => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    // "Sold for" and "Sold After for" are sales. "Bid to" is an auction that
    // did NOT meet its reserve -- nothing changed hands, so it is not
    // evidence of what the car sells for, only of what it failed to sell
    // for. The two must stay distinguishable.
    const PRICE = /(Sold After for|Sold for|Bid to|Winning bid)\s*\$([\d,]+)/i;
    const YEAR_TITLE = /\b(19|20)\d{2}\b/;

    const seen = new Set();
    const out = [];
    for (const anchor of document.querySelectorAll('a[href*="/auctions/"]')) {
      const href = anchor.getAttribute("href") || "";
      const slug = href.split("?")[0];
      if (!slug || seen.has(slug)) continue;

      // Walk up until an ancestor carries both a title and a price. Capped,
      // so a page-wide container cannot swallow every card into one.
      let node = anchor;
      let card = null;
      for (let depth = 0; depth < 5 && node; depth += 1) {
        const text = clean(node.innerText);
        if (PRICE.test(text) && YEAR_TITLE.test(text) && text.length < 400) {
          card = text;
          break;
        }
        node = node.parentElement;
      }
      if (!card) continue;
      seen.add(slug);

      const price = card.match(PRICE);
      const title = (card.match(/\b((19|20)\d{2}[^,\n]{0,70})/) || [])[1] || "";
      const ended = (card.match(/Ended\s+([\d/]+)/i) || [])[1] || "";
      out.push({
        title: clean(title),
        label: clean(price[1]),
        price: Number(price[2].replace(/,/g, "")),
        sold: /sold/i.test(price[1]),
        ended,
        url: slug,
      });
    }
    return out;
  });
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
  let result = { auction_url: auctionUrl, model_url: "", comps: [] };
  try {
    const page = await browser.newPage();
    await page.setUserAgent(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    );
    await page.setExtraHTTPHeaders({ "Accept-Language": "en-US,en;q=0.9" });
    await page.setViewport({ width: 1440, height: 1600, deviceScaleFactor: 1 });

    const modelUrl = await findModelPage(page, auctionUrl, timeoutMs);
    if (!modelUrl) throw new Error("No model results link on the listing page.");
    result.model_url = modelUrl;
    result.comps = await readResults(page, modelUrl, timeoutMs);
  } catch (err) {
    // Fails open: without comps the script writes the value beat the way it
    // did before this existed.
    result.error = String(err && err.message ? err.message : err);
  } finally {
    await browser.close().catch(() => {});
  }
  await fs.mkdir(path.dirname(path.resolve(outJson)), { recursive: true });
  await fs.writeFile(outJson, JSON.stringify(result, null, 2), "utf8");
  process.stdout.write(`${JSON.stringify({
    ok: !result.error,
    comps: result.comps.length,
    sold: result.comps.filter((c) => c.sold).length,
  })}\n`);
}

main().catch(async (err) => {
  process.stderr.write(`${String(err && err.message ? err.message : err)}\n`);
  process.exit(1);
});
