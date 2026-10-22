import { test } from "node:test";
import assert from "node:assert/strict";
import { clipSeconds, clipLength } from "./clipTimes.js";

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
