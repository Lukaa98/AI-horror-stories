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
/* The crop box as fractions of the source frame, or null for the whole
 * frame. Typed as percentages because that is what somebody reading a
 * poster frame can estimate; stored as fractions because the build has no
 * idea what size the phone recorded at.
 *
 * Anything missing takes its edge: a top and a height with no left and
 * width is a full-width band, which is the common case -- the car is at
 * the top and the bottom two thirds are driveway.
 */
export function cropFractions(box) {
  const pct = (value, fallback) => {
    const text = String(value ?? "").trim().replace("%", "");
    if (text === "") return fallback;
    const n = Number(text);
    return Number.isFinite(n) ? Math.min(100, Math.max(0, n)) / 100 : fallback;
  };
  const x = pct(box?.left, 0);
  const y = pct(box?.top, 0);
  const w = Math.min(pct(box?.width, 1), 1 - x);
  const h = Math.min(pct(box?.height, 1), 1 - y);
  if (w <= 0 || h <= 0) return null;
  if (x === 0 && y === 0 && w === 1 && h === 1) return null;
  return { x, y, w, h };
}


/* A still from a Cloudflare Stream video, at a given second, or null.
 *
 * The listing's videos are HLS manifests. Chrome and Firefox cannot play
 * HLS natively -- only Safari can -- so a <video> pointed at one renders
 * controls, no duration and a black rectangle, which is what the crop box
 * was being drawn on.
 *
 * Stream serves a thumbnail endpoint beside the manifest, which is better
 * than a player for this anyway: asked for the clip's own start second it
 * shows the exact frame the film will open on, so the crop box is drawn
 * over the thing being cropped rather than over whatever frame a player
 * happened to stop at.
 *
 * Null for anything that is not a Stream manifest, so a pasted mp4 keeps
 * its player.
 */
export function streamThumbnail(url, seconds) {
  const match = String(url || "").trim()
    .match(/^(https:\/\/[^/]*cloudflarestream\.com\/[A-Za-z0-9]+)\/manifest\/[^/?#]+/);
  if (!match) return null;
  const at = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  return `${match[1]}/thumbnails/thumbnail.jpg?time=${at}s&height=480`;
}


/* Why a typed clip link cannot be used, or "" if it looks usable.
 *
 * ffmpeg reads media, not web pages. A YouTube watch url fetches HTML and
 * fails, and the build drops the clip without the video being any the
 * wiser -- which is what happened on the Murcielago run, where the link
 * was a youtube.com/watch page and the preview was correspondingly blank.
 *
 * Deliberately only certain about the cases that are certainly wrong. A
 * link this cannot classify is left alone rather than refused.
 */
export function clipLinkProblem(url) {
  const text = String(url || "").trim();
  if (!text) return "";
  if (!/^https?:\/\//i.test(text)) {
    return "That is not a link. Paste the address of the video file itself.";
  }
  if (/(^|\.)(youtube\.com|youtu\.be|vimeo\.com|dailymotion\.com)(\/|$)/i.test(
        new URL(text).hostname)) {
    return "A YouTube or Vimeo page is not a video file, and the build cannot read one. "
      + "Use the listing's own video instead \u2014 leave this box empty and press "
      + "\u201cFind videos\u201d.";
  }
  if (/\/auctions?\//i.test(text) || /carsandbids\.com\/auctions/i.test(text)) {
    return "That is the listing page, not a video on it. Leave this box empty and press "
      + "\u201cFind videos\u201d.";
  }
  return "";
}


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


/* The crop box, drawn with the mouse instead of typed.
 *
 * Four percentages are an awkward way to say "that bit there": you
 * estimate, type, look, and estimate again. Dragging a rectangle over the
 * frame says it once. Everything below works in the preview element's own
 * pixels and converts at the end, which is sound because the displayed
 * still keeps the source frame's aspect ratio -- a shape that looks right
 * on the preview is the same shape in the video.
 */

/* Percentages rounded to a tenth, which is finer than anybody can drag
 * but keeps a locked shape honest: rounding a 9:16 box to whole percent
 * can put it a percent off, and the point of the lock is that it is not. */
function cropPercents(x, y, w, h) {
  const pct = (v) => String(Math.round(Math.min(100, Math.max(0, v * 100)) * 10) / 10);
  return { left: pct(x), top: pct(y), width: pct(w), height: pct(h) };
}

/* The box a drag from one point to another describes, or null if the drag
 * was too small to mean anything -- a click rather than a drag, which the
 * caller is free to read as "clear the box".
 *
 * `ratio` is width over height, or 0 for no lock. A locked drag keeps the
 * corner the pointer started at, follows whichever side the pointer moved
 * furthest, and shrinks both rather than letting the box leave the frame.
 */
export function dragCropBox(from, to, rect, ratio = 0) {
  const W = Number(rect?.width), H = Number(rect?.height);
  if (!(W > 0 && H > 0)) return null;
  const x0 = Math.min(W, Math.max(0, from.x));
  const y0 = Math.min(H, Math.max(0, from.y));
  const x1 = Math.min(W, Math.max(0, to.x));
  const y1 = Math.min(H, Math.max(0, to.y));
  let w = Math.abs(x1 - x0);
  let h = Math.abs(y1 - y0);
  if (w < 6 || h < 6) return null;
  if (ratio > 0) {
    if (w / ratio > h) h = w / ratio; else w = h * ratio;
    const roomX = x1 >= x0 ? W - x0 : x0;
    const roomY = y1 >= y0 ? H - y0 : y0;
    if (w > roomX) { w = roomX; h = w / ratio; }
    if (h > roomY) { h = roomY; w = h * ratio; }
    if (w < 6 || h < 6) return null;
  }
  return cropPercents((x1 >= x0 ? x0 : x0 - w) / W,
                      (y1 >= y0 ? y0 : y0 - h) / H, w / W, h / H);
}

/* The same box shifted by a drag, kept whole and kept inside the frame.
 * Moving is the half of this that four numbers are worst at: nudging a
 * box left means editing one number and watching the far edge move too. */
export function moveCropBox(box, dx, dy, rect) {
  const W = Number(rect?.width), H = Number(rect?.height);
  if (!box || !(W > 0 && H > 0)) return null;
  const x = Math.min(1 - box.w, Math.max(0, box.x + dx / W));
  const y = Math.min(1 - box.h, Math.max(0, box.y + dy / H));
  return cropPercents(x, y, box.w, box.h);
}

/* Which part of an existing box a point is on: a corner by name, "inside"
 * for the body of it, or null for bare frame. Hit-tested here rather than
 * with four more elements so the drawn box can stay inert and let every
 * pointer event through to the one element that handles them. */
export function cropHandleAt(point, box, rect, slop = 14) {
  const W = Number(rect?.width), H = Number(rect?.height);
  if (!box || !(W > 0 && H > 0)) return null;
  const left = box.x * W, top = box.y * H;
  const right = left + box.w * W, bottom = top + box.h * H;
  const nearX = Math.abs(point.x - left) <= slop ? "w"
    : Math.abs(point.x - right) <= slop ? "e" : "";
  const nearY = Math.abs(point.y - top) <= slop ? "n"
    : Math.abs(point.y - bottom) <= slop ? "s" : "";
  if (nearX && nearY) return nearY + nearX;
  const inside = point.x >= left && point.x <= right
    && point.y >= top && point.y <= bottom;
  return inside ? "inside" : null;
}

/* The corner a resize pivots on: grab the bottom right and the top left
 * stays put. Returned in element pixels so a resize is just a fresh drag
 * from there, which is why there is no separate resize routine. */
export function cropAnchor(box, grip, rect) {
  const W = Number(rect?.width), H = Number(rect?.height);
  if (!box || !grip || !(W > 0 && H > 0)) return null;
  return {
    x: (grip.includes("w") ? box.x + box.w : box.x) * W,
    y: (grip.includes("n") ? box.y + box.h : box.y) * H,
  };
}

/* An existing box forced to a shape, or the largest box of that shape in
 * the middle of the frame if there is no box yet. Picking 9:16 with
 * nothing drawn should give the right answer on its own: that is the one
 * shape that fills a Shorts frame edge to edge, so it is the most common
 * thing to want and the least rewarding thing to drag by hand. */
export function fitCropRatio(box, ratio, rect) {
  const W = Number(rect?.width), H = Number(rect?.height);
  if (!(ratio > 0) || !(W > 0 && H > 0)) return null;
  const have = box
    ? { x: box.x * W, y: box.y * H, w: box.w * W, h: box.h * H }
    : { x: 0, y: 0, w: W, h: H };
  const cx = have.x + have.w / 2, cy = have.y + have.h / 2;
  let w = have.w, h = have.h;
  if (w / ratio > h) w = h * ratio; else h = w / ratio;
  if (w > W) { w = W; h = w / ratio; }
  if (h > H) { h = H; w = h * ratio; }
  const x = Math.min(W - w, Math.max(0, cx - w / 2));
  const y = Math.min(H - h, Math.max(0, cy - h / 2));
  return cropPercents(x / W, y / H, w / W, h / H);
}
