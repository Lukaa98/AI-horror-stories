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

/* Seconds back into the m:ss a listing's player shows, for refilling the
 * form from a build that already used a clip.
 *
 * Not the inverse of a typed string -- "7" and "0:07" both come back as
 * "0:07" -- because what the field has to hold is a time the user can read
 * off the player again, not the keystrokes they happened to use. */
export function clipStamp(seconds) {
  // Checked before coercing, because Number(null) and Number("") are both
  // 0 -- a build that recorded no time would come back refilled as "0:00",
  // which is a real instruction to cut from the start of the video rather
  // than the empty field it should be.
  if (seconds === null || seconds === undefined || seconds === "") return "";
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "";
  const minutes = Math.floor(value / 60);
  const rest = value - minutes * 60;
  // A fraction is kept rather than rounded away: a clip asked for at 12.5s
  // and refilled as 0:12 would start half a second early, which on a
  // start-up is the difference between the crank and the catch.
  const text = Number.isInteger(rest) ? String(rest) : String(Number(rest.toFixed(3)));
  return `${minutes}:${rest < 10 ? "0" : ""}${text}`;
}

/* Two listing videos that are the same video.
 *
 * Compared without the query string, because a listing's playback urls can
 * come back re-signed from one probe to the next -- the same video with a
 * different token. Matching on the whole url would drop a clip carried over
 * from a previous build for no reason the user can see, and matching on
 * nothing at all would quietly build one listing's car with another
 * listing's engine. */
export function sameListingVideo(a, b) {
  const bare = (url) => String(url || "").split("?")[0];
  return !!bare(a) && bare(a) === bare(b);
}
