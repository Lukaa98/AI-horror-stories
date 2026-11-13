import { test } from "node:test";
import assert from "node:assert/strict";
import { clipSeconds, clipLength, clipStamp, cropFractions, sameListingVideo,
         streamThumbnail, clipLinkProblem, dragCropBox, moveCropBox,
         cropHandleAt, cropAnchor, fitCropRatio, engineClipPayload,
         engineClipProblem } from "./clipTimes.js";

test("reads the m:ss a listing's player shows, and plain seconds", () => {
  assert.equal(clipSeconds("1:04"), 64);
  assert.equal(clipSeconds("0:07"), 7);
  assert.equal(clipSeconds("64"), 64);
  assert.equal(clipSeconds("1:04.5"), 64.5);
  assert.equal(clipSeconds(":30"), 30);
});

test("refuses what it cannot read rather than calling it zero", () => {
  // A time silently read as 0 cuts from the start of the clip instead of
  // where it was asked for, and nothing says so until it is watched back.
  for (const bad of ["", "   ", "abc", "1:2:3", "-3", null, undefined, "4m"]) {
    assert.equal(clipSeconds(bad), null, JSON.stringify(bad));
  }
});

test("a range is only a range when it runs forwards", () => {
  assert.equal(clipLength("0:04", "0:07"), 3);
  assert.equal(clipLength("1:00", "1:02.5"), 2.5);
  assert.equal(clipLength("0:07", "0:04"), null, "backwards");
  assert.equal(clipLength("0:04", "0:04"), null, "zero length");
  assert.equal(clipLength("0:04", "nonsense"), null);
});

test("a clip carried over from a previous build reads back off the player", () => {
  // Only the seconds are stored, so refilling the form has to turn them
  // back into something that can be checked against the listing's player.
  assert.equal(clipStamp(7), "0:07");
  assert.equal(clipStamp(64), "1:04");
  assert.equal(clipStamp(0), "0:00");
  assert.equal(clipStamp(600), "10:00");
  // A fraction is kept rather than rounded away: on a start-up, half a
  // second is the difference between the crank and the catch.
  assert.equal(clipStamp(12.5), "0:12.5");
  assert.equal(clipStamp(125.25), "2:05.25");
  // And it round-trips through the parser the field uses.
  for (const seconds of [0, 7, 12.5, 64, 125.25, 600]) {
    assert.equal(clipSeconds(clipStamp(seconds)), seconds, String(seconds));
  }
  // Nothing to refill is an empty field, not "0:00".
  for (const bad of [null, undefined, "", -1, NaN, "abc"]) {
    assert.equal(clipStamp(bad), "", JSON.stringify(bad));
  }
});

test("the same listing video survives being re-signed", () => {
  const a = "https://videodelivery.net/abc123/manifest/video.m3u8?token=one";
  const b = "https://videodelivery.net/abc123/manifest/video.m3u8?token=two";
  // A carried-over clip must not be dropped because the listing handed out
  // a fresh token between probes.
  assert.equal(sameListingVideo(a, b), true);
  assert.equal(sameListingVideo(a, a), true);
  assert.equal(sameListingVideo("https://videodelivery.net/abc123/manifest/video.m3u8", a), true);
  // But a different video is a different video -- building this car with
  // another listing's engine is the failure worth guarding against.
  assert.equal(sameListingVideo(a, "https://videodelivery.net/zzz999/manifest/video.m3u8"), false);
  for (const bad of [null, undefined, ""]) {
    assert.equal(sameListingVideo(bad, bad), false, JSON.stringify(bad));
    assert.equal(sameListingVideo(a, bad), false);
  }
});


test("a blank crop box means the whole frame, not a zero-sized one", () => {
  assert.equal(cropFractions(null), null);
  assert.equal(cropFractions({}), null);
  assert.equal(cropFractions({ left: "", top: "", width: "", height: "" }), null);
  // Explicitly the whole frame is still the whole frame.
  assert.equal(cropFractions({ left: "0", top: "0", width: "100", height: "100" }), null);
});

test("a missing edge takes its default, so a band needs two numbers not four", () => {
  // The common case: the car is at the top and the rest is driveway.
  assert.deepEqual(cropFractions({ top: "0", height: "33" }),
                   { x: 0, y: 0, w: 1, h: 0.33 });
  // Cropping the sides in as well is what actually magnifies it.
  assert.deepEqual(cropFractions({ left: "20", top: "0", width: "60", height: "33" }),
                   { x: 0.2, y: 0, w: 0.6, h: 0.33 });
});

test("nonsense is clamped rather than passed on to ffmpeg", () => {
  // Past the edge means to the edge.
  assert.deepEqual(cropFractions({ left: "50", width: "90", height: "100" }),
                   { x: 0.5, y: 0, w: 0.5, h: 1 });
  // Out of range is clamped into range, and -10 to 150 clamps to the whole
  // frame -- which is null, the same as having typed nothing.
  assert.equal(cropFractions({ top: "-10", height: "150" }), null);
  // Clamped but still a crop.
  assert.deepEqual(cropFractions({ top: "-10", height: "40" }),
                   { x: 0, y: 0, w: 1, h: 0.4 });
  assert.equal(cropFractions({ height: "tall" }), null);
  // A trailing % is what somebody types.
  assert.deepEqual(cropFractions({ top: "0%", height: "33%" }),
                   { x: 0, y: 0, w: 1, h: 0.33 });
});

test("a listing video gets a still, because browsers cannot play its HLS", () => {
  const manifest = "https://customer-abc123.cloudflarestream.com/02543da7bf33e49e82021a467a401735"
    + "/manifest/video.m3u8";
  assert.equal(
    streamThumbnail(manifest, 33),
    "https://customer-abc123.cloudflarestream.com/02543da7bf33e49e82021a467a401735"
      + "/thumbnails/thumbnail.jpg?time=33s&height=480");
  // The clip's own start second, so the still is the frame it opens on.
  assert.match(streamThumbnail(manifest, 7), /time=7s/);
  // No start time yet is the first frame, not NaN.
  assert.match(streamThumbnail(manifest, null), /time=0s/);
  assert.match(streamThumbnail(manifest, undefined), /time=0s/);
});

test("anything that is not a Stream manifest keeps its own player", () => {
  assert.equal(streamThumbnail("https://example.com/startup.mp4", 5), null);
  assert.equal(streamThumbnail("", 5), null);
  assert.equal(streamThumbnail(null, 5), null);
  // The page a video sits on is not the video, and must not be rewritten
  // into a thumbnail url that would 404.
  assert.equal(streamThumbnail("https://carsandbids.com/auctions/abc/2008-lamborghini", 5), null);
});

test("a page is not a video, and the build cannot read one", () => {
  // The Murcielago run went out with this link and would have dropped the
  // clip silently: ffmpeg fetches HTML and fails.
  assert.match(clipLinkProblem("https://www.youtube.com/watch?v=ddgJK8dN7cM"),
               /not a video file/);
  assert.match(clipLinkProblem("https://youtu.be/ddgJK8dN7cM"), /not a video file/);
  assert.match(clipLinkProblem("https://carsandbids.com/auctions/KmOkpwNY/2008-lambo"),
               /the listing page/);
  assert.match(clipLinkProblem("ddgJK8dN7cM"), /not a link/);
});

test("anything that could be a media file is left alone", () => {
  // Only the certainly-wrong cases are refused; an unrecognised host is
  // not the dashboard's business to veto.
  assert.equal(clipLinkProblem(""), "");
  assert.equal(clipLinkProblem(null), "");
  assert.equal(clipLinkProblem("https://example.com/startup.mp4"), "");
  assert.equal(clipLinkProblem(
    "https://customer-abc.cloudflarestream.com/xyz/manifest/video.m3u8"), "");
});


/* Drawing the box with the pointer. The preview below is 320x180, which
   is 16:9 -- the shape a phone clip held sideways comes back as. */
const PREVIEW = { width: 320, height: 180 };

test("a drag becomes the four percentages, free of any shape", () => {
  assert.deepEqual(dragCropBox({ x: 32, y: 18 }, { x: 160, y: 90 }, PREVIEW, 0),
                   { left: "10", top: "10", width: "40", height: "40" });
});

test("a drag reads the same whichever corner it started from", () => {
  const forwards = dragCropBox({ x: 32, y: 18 }, { x: 160, y: 90 }, PREVIEW, 0);
  const backwards = dragCropBox({ x: 160, y: 90 }, { x: 32, y: 18 }, PREVIEW, 0);
  assert.deepEqual(backwards, forwards);
});

test("a press that goes nowhere is a click, not a box of nothing", () => {
  // The caller reads null as "clear the crop", so a twitch on the frame
  // must not land as a two-pixel box that crops the film to a speck.
  assert.equal(dragCropBox({ x: 40, y: 40 }, { x: 42, y: 41 }, PREVIEW, 0), null);
  assert.equal(dragCropBox({ x: 40, y: 40 }, { x: 140, y: 43 }, PREVIEW, 0), null);
});

test("a drag cannot leave the frame", () => {
  const box = dragCropBox({ x: 160, y: 90 }, { x: 900, y: 900 }, PREVIEW, 0);
  assert.deepEqual(box, { left: "50", top: "50", width: "50", height: "50" });
});

test("a locked drag keeps its shape in the pixels of the source", () => {
  // 9:16 of a 16:9 frame is a tall sliver: the full height is 180 of the
  // preview's pixels, so the width has to come out at 180 * 9/16 = 101.25,
  // which is 31.6% of 320. The percentages are not equal and should not
  // be -- it is the pixels behind them that have to make the shape.
  const box = dragCropBox({ x: 0, y: 0 }, { x: 320, y: 180 }, PREVIEW, 9 / 16);
  assert.equal(box.height, "100");
  const ratio = (Number(box.width) / 100 * 320) / (Number(box.height) / 100 * 180);
  assert.ok(Math.abs(ratio - 9 / 16) < 0.005, box.width);
});

test("a locked drag shrinks rather than running off the frame", () => {
  // Started near the bottom and dragged hard down: there is no room to
  // make the height the shape asks for, so the width gives way instead.
  const box = dragCropBox({ x: 10, y: 120 }, { x: 300, y: 900 }, PREVIEW, 9 / 16);
  assert.ok(Number(box.top) + Number(box.height) <= 100.01, JSON.stringify(box));
  assert.ok(Number(box.left) + Number(box.width) <= 100.01, JSON.stringify(box));
  const ratio = (Number(box.width) / 100 * 320) / (Number(box.height) / 100 * 180);
  assert.ok(Math.abs(ratio - 9 / 16) < 0.005, JSON.stringify(box));
});

test("moving a box keeps its size and stops at the edge", () => {
  const box = cropFractions({ left: 10, top: 10, width: 40, height: 40 });
  assert.deepEqual(moveCropBox(box, 32, 0, PREVIEW),
                   { left: "20", top: "10", width: "40", height: "40" });
  // Shoved past the right edge: it stops flush rather than being clipped,
  // which is the one thing editing the left number by hand cannot do.
  assert.deepEqual(moveCropBox(box, 9000, 9000, PREVIEW),
                   { left: "60", top: "60", width: "40", height: "40" });
});

test("a press is hit-tested against the box it lands on", () => {
  const box = cropFractions({ left: 25, top: 25, width: 50, height: 50 });
  assert.equal(cropHandleAt({ x: 80, y: 45 }, box, PREVIEW), "nw");
  assert.equal(cropHandleAt({ x: 240, y: 135 }, box, PREVIEW), "se");
  assert.equal(cropHandleAt({ x: 160, y: 90 }, box, PREVIEW), "inside");
  assert.equal(cropHandleAt({ x: 5, y: 5 }, box, PREVIEW), null);
  assert.equal(cropHandleAt({ x: 160, y: 90 }, null, PREVIEW), null);
});

test("a resize is a fresh drag from the corner that stays put", () => {
  const box = cropFractions({ left: 25, top: 25, width: 50, height: 50 });
  assert.deepEqual(cropAnchor(box, "se", PREVIEW), { x: 80, y: 45 });
  assert.deepEqual(cropAnchor(box, "nw", PREVIEW), { x: 240, y: 135 });
});

test("picking a shape with nothing drawn gives the biggest box of it", () => {
  // The case worth getting right: 9:16 is the only crop that fills a
  // Shorts frame, so asking for it should not also mean dragging it.
  const box = fitCropRatio(null, 9 / 16, PREVIEW);
  assert.equal(box.height, "100");
  assert.equal(box.top, "0");
  // Centred, so the margins either side match.
  assert.ok(Math.abs(Number(box.left) * 2 + Number(box.width) - 100) < 0.2,
            JSON.stringify(box));
});

test("picking a shape over a box keeps the middle of what was drawn", () => {
  const box = cropFractions({ left: 20, top: 20, width: 60, height: 60 });
  const square = fitCropRatio(box, 1, PREVIEW);
  const centre = (box.x + box.w / 2) * 320;
  const after = (Number(square.left) / 100 + Number(square.width) / 200) * 320;
  assert.ok(Math.abs(after - centre) < 1, JSON.stringify(square));
  const ratio = (Number(square.width) / 100 * 320) / (Number(square.height) / 100 * 180);
  assert.ok(Math.abs(ratio - 1) < 0.01, JSON.stringify(square));
});

test("nothing is drawn against a preview that has no size yet", () => {
  // The first render has a ref but no laid-out element behind it.
  const empty = { width: 0, height: 0 };
  assert.equal(dragCropBox({ x: 0, y: 0 }, { x: 10, y: 10 }, empty, 0), null);
  assert.equal(moveCropBox({ x: 0, y: 0, w: 1, h: 1 }, 5, 5, empty), null);
  assert.equal(cropHandleAt({ x: 1, y: 1 }, { x: 0, y: 0, w: 1, h: 1 }, empty), null);
  assert.equal(fitCropRatio(null, 9 / 16, empty), null);
  assert.equal(fitCropRatio(null, 0, PREVIEW), null);
});


/* Whether the ticked box actually produces a clip. */
const CLIP = "https://customer-abc.cloudflarestream.com/d1f2e3/manifest/video.m3u8";
const form = (over) => ({ enabled: true, source: CLIP, url: "", start: "0:04",
                          end: "0:09", crop: {}, ...over });

test("a complete form becomes the clip the build is sent", () => {
  assert.deepEqual(engineClipPayload(form()),
                   { url: CLIP, start: 4, end: 9, crop: null });
  assert.equal(engineClipProblem(form()), "");
});

test("the crop rides with the clip when there is one", () => {
  const box = engineClipPayload(form({ crop: { left: 0, top: 0, width: 100, height: 65 } }));
  assert.deepEqual(box.crop, { x: 0, y: 0, w: 1, h: 0.65 });
});

test("missing times are a refusal, not a clip quietly dropped", () => {
  // This is the GT3 run. Ticked, a clip chosen, a crop set, and no times:
  // the build went out, took a quarter of an hour, and came back silent.
  for (const times of [{ start: "", end: "" }, { start: "0:04", end: "" },
                       { start: "", end: "0:09" }, { start: "x", end: "0:09" }]) {
    const f = form(times);
    assert.equal(engineClipPayload(f), null, JSON.stringify(times));
    assert.match(engineClipProblem(f), /start and end times are not both/);
  }
});

test("a backwards or empty range says which of the two is wrong", () => {
  const f = form({ start: "0:09", end: "0:04" });
  assert.equal(engineClipPayload(f), null);
  assert.match(engineClipProblem(f), /end time is not after the start/);
  assert.match(engineClipProblem(form({ start: "0:04", end: "0:04" })),
               /end time is not after the start/);
});

test("ticked with nothing chosen says so rather than building in silence", () => {
  const f = form({ source: "" });
  assert.equal(engineClipPayload(f), null);
  assert.match(engineClipProblem(f), /no clip is chosen/);
});

test("an unusable link keeps its own message", () => {
  const f = form({ source: "https://www.youtube.com/watch?v=abc",
                   url: "https://www.youtube.com/watch?v=abc" });
  assert.equal(engineClipPayload(f), null);
  assert.equal(engineClipProblem(f), clipLinkProblem(f.url));
  assert.ok(engineClipProblem(f));
});

test("an unticked box is not a problem to report", () => {
  // Nothing is wrong with not wanting an engine clip.
  assert.equal(engineClipProblem(form({ enabled: false, start: "", end: "" })), "");
  assert.equal(engineClipPayload(form({ enabled: false })), null);
  assert.equal(engineClipProblem(undefined), "");
});
