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
