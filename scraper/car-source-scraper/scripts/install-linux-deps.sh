#!/usr/bin/env bash
set -euo pipefail

# Chrome's runtime libraries, installed onto whatever Linux image the
# runner happens to be today.
#
# That last part is the whole difficulty. The package names move: Ubuntu
# 24.04 renamed a batch of libraries for the 64-bit time_t transition
# (libasound2 -> libasound2t64 and friends), and 26.04 dropped
# libu2f-udev altogether. A single apt-get install with a fixed list fails
# whole on the first name it cannot find -- which is what happened when
# the runners moved to 26.04: "E: Unable to locate package libu2f-udev",
# exit 100, and every scrape in the repo stopped, for a package that
# exists to make U2F security keys work and has nothing to do with
# rendering a page.
#
# So each entry below is a list of acceptable names, newest first, and
# anything that matches none of them is skipped with a warning rather than
# taken as a reason to fail. A missing library that Chrome genuinely needs
# shows up as Chrome failing to start, which the scraper already reports;
# a missing library it does not need should not stop the build at all.

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

# Installable means apt has a candidate version for it. `apt-cache show`
# is not enough on its own: it succeeds for a purely virtual package that
# cannot actually be installed.
package_available() {
  local candidate
  candidate="$(apt-cache policy "$1" 2>/dev/null | awk '/Candidate:/ {print $2}')"
  [ -n "$candidate" ] && [ "$candidate" != "(none)" ]
}

# Each line: the names that would satisfy this dependency, best first.
WANTED=(
  "ca-certificates"
  "fonts-liberation"
  "libasound2t64 libasound2"
  "libatk-bridge2.0-0t64 libatk-bridge2.0-0"
  "libatk1.0-0t64 libatk1.0-0"
  "libcairo2"
  "libcups2t64 libcups2"
  "libdbus-1-3"
  "libdrm2"
  "libexpat1"
  "libfontconfig1"
  "libgbm1"
  "libglib2.0-0t64 libglib2.0-0"
  "libgtk-3-0t64 libgtk-3-0"
  "libnspr4"
  "libnss3"
  "libpango-1.0-0"
  "libpangocairo-1.0-0"
  "libu2f-udev"
  "libvulkan1"
  "libx11-6"
  "libx11-xcb1"
  "libxcb1"
  "libxcomposite1"
  "libxcursor1"
  "libxdamage1"
  "libxext6"
  "libxfixes3"
  "libxi6"
  "libxkbcommon0"
  "libxrandr2"
  "libxrender1"
  "libxshmfence1"
  "libxss1"
  "libxtst6"
  "wget"
  "xdg-utils"
)

PACKAGES=()
SKIPPED=()
for alternatives in "${WANTED[@]}"; do
  picked=""
  for name in $alternatives; do
    if package_available "$name"; then
      picked="$name"
      break
    fi
  done
  if [ -n "$picked" ]; then
    PACKAGES+=("$picked")
  else
    SKIPPED+=("$alternatives")
  fi
done

if [ "${#SKIPPED[@]}" -gt 0 ]; then
  echo "Not available on this image, skipping: ${SKIPPED[*]}" >&2
fi

# Nothing resolving at all is apt being broken, not Chrome needing
# nothing. Worth failing on, where one absent library is not.
if [ "${#PACKAGES[@]}" -eq 0 ]; then
  echo "No Chrome dependency resolved to an installable package. apt is not usable here." >&2
  exit 1
fi

$SUDO apt-get install -y "${PACKAGES[@]}"

echo "Puppeteer Linux dependencies installed (${#PACKAGES[@]} packages). Try: npm run scrape:miata"
