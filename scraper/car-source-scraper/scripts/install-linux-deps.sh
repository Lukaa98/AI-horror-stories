#!/usr/bin/env bash
set -euo pipefail

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This helper only supports apt-based Linux images. Install Chrome/Puppeteer runtime libs manually." >&2
  exit 1
fi

if [ "${EUID}" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

$SUDO apt-get update
$SUDO apt-get install -y \
  ca-certificates \
  fonts-liberation \
  libasound2 \
  libatk-bridge2.0-0 \
  libatk1.0-0 \
  libcairo2 \
  libcups2 \
  libdbus-1-3 \
  libdrm2 \
  libexpat1 \
  libfontconfig1 \
  libgbm1 \
  libglib2.0-0 \
  libgtk-3-0 \
  libnspr4 \
  libnss3 \
  libpango-1.0-0 \
  libpangocairo-1.0-0 \
  libu2f-udev \
  libvulkan1 \
  libx11-6 \
  libx11-xcb1 \
  libxcb1 \
  libxcomposite1 \
  libxcursor1 \
  libxdamage1 \
  libxext6 \
  libxfixes3 \
  libxi6 \
  libxkbcommon0 \
  libxrandr2 \
  libxrender1 \
  libxshmfence1 \
  libxss1 \
  libxtst6 \
  wget \
  xdg-utils

echo "Puppeteer Linux dependencies installed. Try: npm run scrape:miata"
