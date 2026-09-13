// Keep the workflow input backward-compatible: an array, with optional metadata.
export function serializePhotos(photos) {
  return photos.filter(p => p.url.trim()).map(p => ({
    id: p.id, label: p.label.trim(), url: p.url.trim(),
    ...(p.section ? { section: p.section, role: p.role || "detail", note: (p.note || "").trim() } : {}),
  }));
}
