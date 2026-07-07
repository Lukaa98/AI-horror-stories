# Car source scraper

Staged Puppeteer package for the future cars channel. This mirrors the approach in `carsandbids-labs/cab-daily-scraper`: launch Puppeteer with stealth, set a normal browser user agent/language, load a page, extract structured metadata from the page/DOM, and save JSON output plus screenshots.

This package is **not wired into production uploads yet**. Use it locally to build source packets for car Shorts.

## What it should scrape

Use this for official/reputable source pages only:

- official manufacturer press releases
- official model/configurator/build-and-price pages
- reputable automotive publications for verification/context
- marketplace/listing pages only for dated deal videos

Do **not** use it to scrape creator video content. Creator feeds are topic signals only.

## Run

```bash
cd tools/car-source-scraper
npm install
npm run scrape:miata
```

Output goes to:

```text
src/output/car_sources/<topic>/
  source-packet.json
  screenshots/*.png
```

The Python sample renderer can then use the packet idea/facts to produce a local test Short.
