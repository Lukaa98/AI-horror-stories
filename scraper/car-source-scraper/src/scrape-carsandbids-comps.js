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
  // The quick-facts table holds the model link, and it is rendered client
  // side. The first version looked for it the instant the document was
  // ready and always found nothing: run #218 reported "No model results
  // link on the listing page" on a page that has one.
  await page.waitForSelector('dl dt, a[href*="/search/"]', { timeout: Math.min(20000, timeoutMs) })
    .catch(() => {});
  await new Promise((resolve) => setTimeout(resolve, 2000));

  return page.evaluate(() => {
    const full = (href) => new URL(href, location.origin).toString().split("?")[0];
    // The model link has two path segments after /search/ (make and model);
    // a bare /search/porsche is the make page and too broad to be comps.
    for (const anchor of document.querySelectorAll('a[href*="/search/"]')) {
      const href = anchor.getAttribute("href") || "";
      if (/\/search\/[^/?#]+\/[^/?#]+/.test(href)) return full(href);
    }
    // Fallback: build it from the Make and Model rows. A live auction has
    // no "this auction has ended, see more X here" banner, so on those the
    // quick-facts rows may be the only route to the model page.
    const rows = {};
    for (const dt of document.querySelectorAll("dt")) {
      const key = String(dt.innerText || "").replace(/\s+/g, " ").trim().toLowerCase();
      const value = String(dt.nextElementSibling?.innerText || "")
        .replace(/\s+(Save|Follow|Watch|Share)$/i, "").replace(/\s+/g, " ").trim();
      if (key && value) rows[key] = value;
    }
    const slug = (value) => value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    if (rows.make && rows.model) {
      return `${location.origin}/search/${slug(rows.make)}/${slug(rows.model)}`;
    }
    return "";
  });
}

// The results page shows one screenful at a time. Run #219 asked for 993
// Turbo comps, got the first twenty 993 results, and not one of them was a
// Turbo -- the page is dominated by base Carreras, and the rarer variant we
// actually wanted was further down. So keep asking for more before reading
// anything.
//
// Written blind to which mechanism the site uses: it clicks anything that
// says "load more" and also scrolls to the bottom, then stops as soon as a
// round adds no new cards. Under either pattern -- button or infinite
// scroll -- that terminates, and on a page with neither it costs one round.
const MAX_LOAD_ROUNDS = 8;
const MAX_CARDS = 160;

async function loadMoreResults(page) {
  let previous = 0;
  for (let round = 0; round < MAX_LOAD_ROUNDS; round += 1) {
    const count = await page.evaluate(() => document.querySelectorAll('a[href*="/auctions/"]').length);
    if (count >= MAX_CARDS || (round > 0 && count <= previous)) return count;
    previous = count;
    await page.evaluate(() => {
      const wanted = /^(load|show|view)\s+more|^more\s+results?$/i;
      for (const el of document.querySelectorAll("button, a")) {
        const text = String(el.innerText || "").replace(/\s+/g, " ").trim();
        if (wanted.test(text) && !el.disabled) {
          el.click();
          return;
        }
      }
      window.scrollTo(0, document.body.scrollHeight);
    });
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  return page.evaluate(() => document.querySelectorAll('a[href*="/auctions/"]').length);
}

async function readResults(page, modelUrl, timeoutMs) {
  await page.goto(modelUrl, { waitUntil: "domcontentloaded", timeout: timeoutMs });
  await page.waitForSelector('a[href*="/auctions/"]', { timeout: Math.min(20000, timeoutMs) })
    .catch(() => {});
  // Give the client-rendered result list a moment to fill in.
  await new Promise((resolve) => setTimeout(resolve, 2500));
  await loadMoreResults(page);

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
      // The title comes off the title link itself, not off the card's text.
      // A card carries the listing's subtitle too ("6-Speed Manual",
      // "1 Owner Since 2002"), and sweeping 70 characters of card text
      // swallowed it -- which made every comp look like a different variant
      // from ours and left run #219 with no comparable sales at all.
      let title = "";
      for (const link of document.querySelectorAll(`a[href^="${slug}"]`)) {
        const text = clean(link.innerText);
        if (/^(19|20)\d{2}\s/.test(text) && text.length < 90) {
          title = text;
          break;
        }
      }
      if (!title) title = (card.match(/\b((19|20)\d{2}[^,\n]{0,70})/) || [])[1] || "";
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
