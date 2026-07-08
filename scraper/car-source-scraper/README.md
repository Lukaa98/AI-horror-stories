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

## Codespaces / Linux setup

If Chrome fails with an error like `libatk-1.0.so.0: cannot open shared object file`, the browser downloaded correctly but the Linux image is missing Chrome runtime libraries.

Run this once inside `scraper/car-source-scraper`:

```bash
npm install
npm run setup:linux
```

Run `doctor` after setup. It now actually launches Chrome, so `ok: true` means Chrome can start in this container:

```bash
npm run doctor
```

If `doctor` returns `ok: false`, run `npm run setup:linux` again and check the missing-library error in the JSON output. The setup helper handles Ubuntu Noble/Codespaces package renames such as `libasound2t64`.

If you want to use a system Chrome/Chromium instead of Puppeteer's downloaded browser, set one of these before scraping:

```bash
export PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
# or
export CHROME_PATH=/usr/bin/google-chrome
```

## Run

```bash
cd scraper/car-source-scraper
npm install
npm run setup:linux
npm run scrape:miata
```

Output goes to:

```text
cars/output/sources/<topic>/
  source-packet.json
  screenshots/*.png
```

The Python sample renderer can then use these screenshots:

```bash
cd ../..
FAST_MODE=1 python cars/automation/generate_sample.py --require-real-media
```
