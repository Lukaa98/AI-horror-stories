/* How the typed notes become ranked bullets.
 *
 * This mirrors _split_angles in cars/automation/single_car_short.py, which
 * is the copy that matters -- the build reads the raw text and splits it
 * itself. This one exists so the dashboard can show the split before a
 * build is spent on it, because the ranking is otherwise invisible: the
 * numbering happens inside the prompt where nobody can see it.
 *
 * Two copies of one rule drift, so the shared cases are pinned in both
 * test suites. If you change the rule, change it there first.
 */
const BULLET_MARKER = /^\s*(?:[-*•·–—]+|\d+[.)])\s+/;
const HAS_WORD = /[0-9A-Za-z]/;

export function splitAngles(angles) {
  const bullets = [];
  let broken = true;
  for (const raw of String(angles || "").split("\n")) {
    const line = raw.trim();
    if (!line) {
      // A blank line between two notes separates them whatever the next
      // one starts with. Wrapping never produces one.
      broken = true;
      continue;
    }
    const marked = BULLET_MARKER.test(line);
    const text = line.replace(BULLET_MARKER, "").trim();
    // A marker with nothing after it is not a note.
    if (!HAS_WORD.test(text)) continue;
    const lower = text[0] === text[0].toLowerCase() && text[0] !== text[0].toUpperCase();
    if (bullets.length && !broken && !marked && lower) {
      bullets[bullets.length - 1] += ` ${text}`;
    } else {
      bullets.push(text);
    }
    broken = false;
  }
  return bullets;
}
