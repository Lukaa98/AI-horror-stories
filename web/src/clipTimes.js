/* Times typed off a listing's own video player.
 *
 * Cars & Bids shows m:ss on its player, so that is what gets read and
 * typed; plain seconds is the other natural thing to type. Both are
 * accepted, anything else is rejected rather than coerced -- a time the
 * form quietly reads as 0 would cut three seconds of silence from the
 * start of a clip and nobody would know why until they watched it.
 */
export function clipSeconds(value) {
  const text = String(value ?? "").trim();
  if (!text) return null;
  const parts = text.split(":");
  if (parts.length > 2 || parts.some((part) => part !== "" && !/^\d*\.?\d*$/.test(part))) {
    return null;
  }
  const seconds = parts.length === 2
    ? Number(parts[0]) * 60 + Number(parts[1] || 0)
    : Number(parts[0]);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

/* Seconds of clip between two typed times, or null if the pair is not a
 * usable range. Kept beside the parser because every caller wants the two
 * questions answered together: is this valid, and how long is it. */
export function clipLength(start, end) {
  const from = clipSeconds(start);
  const to = clipSeconds(end);
  if (from === null || to === null || to <= from) return null;
  return Number((to - from).toFixed(3));
}
