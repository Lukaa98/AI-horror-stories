import { test } from "node:test";
import assert from "node:assert/strict";
import { clipSeconds, clipLength, clipStamp, sameListingVideo } from "./clipTimes.js";

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
