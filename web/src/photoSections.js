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
    ...(p.slot ? { slot: p.slot, note: (p.note || "").trim() } : {}),
  }));
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
    }));
}
