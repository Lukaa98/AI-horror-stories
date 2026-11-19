// The workflow input stays a plain JSON array so old requests keep running.
// A nested close-up adds `slot` (front/side/rear/engine/interior) naming the
// main photo it belongs under; a bare {id,label,url} is still a legacy
// ungrouped extra. Blank rows never reach the pipeline.
export const SLOTS = [
  ["front", "Front"],
  ["side", "Side"],
  ["rear", "Rear"],
  ["engine", "Engine bay"],
  ["interior", "Interior"],
];
export const MAX_CLOSEUPS = 4;

export function serializePhotos(photos) {
  return photos.filter(p => (p.url || "").trim()).map(p => ({
    id: p.id, label: (p.label || "").trim(), url: p.url.trim(),
    ...(p.slot ? { slot: p.slot, note: (p.note || "").trim(), describe: p.describe === true } : {}),
    // Only when there is one: an empty key on twenty rows is twenty keys
    // of nothing in an input that already carries twenty long urls.
    ...(blurFractions(p.blur) ? { blur: blurFractions(p.blur) } : {}),
  }));
}


/* A plate box as fractions, or null. The four percentages the editor
 * holds are strings while they are being typed, and the build wants
 * numbers; anything unreadable is no box rather than a wrong one. */
export function blurFractions(box) {
  if (!box) return null;
  if (["x", "y", "w", "h"].every((key) => typeof box[key] === "number")) return box;
  const pct = (value) => {
    const text = String(value ?? "").trim().replace("%", "");
    if (text === "") return null;
    const n = Number(text);
    return Number.isFinite(n) ? Math.min(100, Math.max(0, n)) / 100 : null;
  };
  const x = pct(box.left), y = pct(box.top), w = pct(box.width), h = pct(box.height);
  if ([x, y, w, h].some((value) => value === null)) return null;
  // Rounded, because clamping to the edge goes through 1 - 0.8 and lands
  // on 0.19999999999999996 -- which is the same box and an uglier payload.
  const round = (value) => Math.round(value * 1e6) / 1e6;
  const width = round(Math.min(w, 1 - x)), height = round(Math.min(h, 1 - y));
  return width > 0 && height > 0
    ? { x: round(x), y: round(y), w: width, h: height } : null;
}

export function closeupsFor(photos, slot) {
  return photos.filter(p => p.slot === slot);
}

// The inverse of serializePhotos, for filling the form from a build that
// already ran. The stored value is the workflow input verbatim -- a JSON
// string, possibly empty, possibly from an older run with no `slot` -- so
// anything that isn't a usable row is dropped rather than rendered as a
// blank close-up. Rows get fresh ids: the originals may collide with rows
// already in the form.
export function parseExtraPhotos(raw, slots = SLOTS.map(([slot]) => slot)) {
  let parsed;
  try {
    parsed = JSON.parse(raw || "[]");
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed
    .filter((p) => p && typeof p.url === "string" && p.url.trim() && slots.includes(p.slot))
    .map((p, index) => ({
      id: `closeup-restored-${Date.now()}-${index}`,
      slot: p.slot,
      label: typeof p.label === "string" ? p.label : "",
      url: p.url.trim(),
      note: typeof p.note === "string" ? p.note : "",
      // Opt-in: a close-up is shown either way, and only a ticked one is
      // described to the script. Absent means not ticked, which is what
      // builds made before the box existed should restore as.
      describe: p.describe === true,
    }));
}
