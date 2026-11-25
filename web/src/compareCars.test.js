import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  COMPARE_SLOTS, MAX_COMPARE_PHOTOS, compareProblem, comparePayload,
  compareTabLabel, emptyCompareCar, emptyCompareCars, overPhotoLimit, photoCount,
} from "./compareCars.js";

const car = (over = {}) => ({
  ...emptyCompareCar(),
  url: "https://carsandbids.com/auctions/abc/2008-mercedes-benz-e55-amg",
  photos: { front: "https://example.com/a.jpg" },
  ...over,
});

test("a filled-in countdown has nothing wrong with it", () => {
  assert.equal(compareProblem([car(), car(), car()], 3), null);
  assert.equal(compareProblem([car(), car()], 2), null);
});

test("a countdown of one or four is refused", () => {
  assert.match(compareProblem([car()], 1), /2 or 3/);
  assert.match(compareProblem([car(), car(), car(), car()], 4), /2 or 3/);
});

test("a car with neither a link nor a name is named in the complaint", () => {
  const cars = [car(), car({ url: "", label: "" }), car()];
  assert.match(compareProblem(cars, 3), /Car 2/);
});

test("a link that is not a Cars & Bids auction is refused before the build starts", () => {
  const cars = [car(), car(), car({ url: "https://bringatrailer.com/listing/x" })];
  assert.match(compareProblem(cars, 3), /Car 3/);
  // A car named by hand with no link at all is fine -- not every entry in a
  // countdown is for sale somewhere right now.
  assert.equal(compareProblem([car(), car(), car({ url: "", label: "SL55 AMG" })], 3), null);
});

test("a car with no photo is caught in the form, not fifteen minutes into a run", () => {
  const cars = [car(), car({ photos: {} }), car()];
  assert.match(compareProblem(cars, 3), /Car 2 needs at least one photo/);
});

test("only the first N cars matter, so a half-typed third is ignored at two", () => {
  const cars = [car(), car(), { ...emptyCompareCar(), label: "half typed" }];
  assert.equal(compareProblem(cars, 2), null);
  assert.match(compareProblem(cars, 3), /Car 3 needs at least one photo/);
});

test("the payload carries the theme inside it", () => {
  const payload = comparePayload([car(), car(), car()], 3, "  the cheapest V8 AMGs  ");
  assert.equal(payload.theme, "the cheapest V8 AMGs");
  assert.equal(payload.cars.length, 3);
});

test("the payload is null while anything is still wrong", () => {
  assert.equal(comparePayload([car(), car({ photos: {} })], 2, "t"), null);
});

test("a car is trimmed to the photos a countdown can actually show", () => {
  const busy = car({
    photos: Object.fromEntries(COMPARE_SLOTS.map((slot) => [slot, `https://example.com/${slot}.jpg`])),
  });
  assert.equal(photoCount(busy), 5);
  assert.equal(overPhotoLimit(busy), true);
  const payload = comparePayload([busy, car()], 2, "");
  assert.equal(Object.keys(payload.cars[0].photos).length, MAX_COMPARE_PHOTOS);
  // In slot order, so which three are kept is predictable rather than
  // whatever the object happened to iterate.
  assert.deepEqual(Object.keys(payload.cars[0].photos), ["front", "side", "rear"]);
});

test("blur boxes travel beside the urls, never inside them", () => {
  // The exact trap the single-car build fell into twice: a box smuggled in
  // with the photo URLs is dropped when that map is rebuilt, and the plate
  // ships readable.
  const withBox = car({ blur: { front: { x: 0.1, y: 0.2, w: 0.1, h: 0.1 } } });
  const payload = comparePayload([withBox, car()], 2, "");
  assert.deepEqual(payload.cars[0].photos.blur, { front: { x: 0.1, y: 0.2, w: 0.1, h: 0.1 } });
  assert.equal(payload.cars[1].photos.blur, undefined);
});

test("an engine clip rides along only when there is one", () => {
  const clip = { url: "https://example.com/v.m3u8", start: 0, end: 5 };
  const payload = comparePayload([car({ engineClip: clip }), car()], 2, "");
  assert.deepEqual(payload.cars[0].engine_clip, clip);
  assert.equal("engine_clip" in payload.cars[1], false);
});

test("everything is trimmed on the way out", () => {
  const messy = car({ url: "  https://carsandbids.com/auctions/a/b  ", label: "  E55  ",
                      worth: "  8k  ", notes: "  pulley swap  ",
                      photos: { front: "  https://example.com/a.jpg  " } });
  const payload = comparePayload([messy, car()], 2, "");
  assert.deepEqual(payload.cars[0], {
    url: "https://carsandbids.com/auctions/a/b",
    label: "E55",
    worth: "8k",
    notes: "pulley swap",
    photos: { front: "https://example.com/a.jpg" },
  });
});

test("a tab names itself from whatever it has", () => {
  assert.equal(compareTabLabel({ label: "E55 AMG" }, 0), "E55 AMG");
  // No name typed, so the listing slug stands in -- better than "Car 2"
  // when three listings are open and they all look alike.
  assert.equal(
    compareTabLabel({ url: "https://carsandbids.com/auctions/abc/2008-mercedes-benz-e55" }, 1),
    "2008 Mercedes Benz");
  assert.equal(compareTabLabel({}, 2), "Car 3");
  assert.equal(compareTabLabel(null, 0), "Car 1");
});

test("a long name is cut rather than stretching the tab row", () => {
  const long = compareTabLabel({ label: "Mercedes Benz E55 AMG W211 Kompressor" }, 0);
  // The cut has to include the ellipsis. Slicing to the limit and then
  // appending three dots is three characters over it, every time.
  assert.equal(long.length <= 18, true, `${long} is ${long.length} long`);
  assert.match(long, /\.\.\.$/);
});

test("an empty set of cars is the right length and all blank", () => {
  const cars = emptyCompareCars(3);
  assert.equal(cars.length, 3);
  for (const blank of cars) {
    assert.equal(blank.url, "");
    assert.deepEqual(blank.photos, {});
  }
  assert.match(compareProblem(cars, 3), /Car 1/);
});

test("every tab rule is qualified enough to beat the page's own button style", () => {
  // Twice now this has shipped wrong. `.page button` in App.css is one
  // class and one element deep, so a bare `.compare-tab` loses to it and
  // the whole row comes out brand purple -- and because `.compare-tab.active`
  // is two classes it wins, so the inactive tabs look selected and the
  // selected one looks inactive. The crop shape pills went out invisible
  // for the same reason, accent on accent.
  const css = readFileSync(new URL("./CompareCars.css", import.meta.url), "utf8");
  const rules = css.match(/^\s*\.compare-tab[^{]*\{/gm) || [];
  assert.equal(rules.length > 0, true, "no .compare-tab rules found at all");
  for (const rule of rules) {
    const selector = rule.replace("{", "").trim();
    // Either the rule is scoped under .compare-tabs, or it carries a second
    // class of its own (.compare-tab-rank, .compare-tab-name) and is not a
    // button being painted.
    // The container itself is a div, not a button, and the rank/name are
    // spans inside one -- neither is something .page button can repaint.
    const isContainer = selector === ".compare-tabs";
    const isInnerSpan = /\.compare-tab-(rank|name)\b/.test(selector);
    const scoped = selector.includes(".compare-tabs ");
    assert.equal(scoped || isContainer || isInnerSpan, true,
      `"${selector}" is one class deep and will lose to .page button`);
  }
});

test("the count pills are qualified for the same reason", () => {
  const css = readFileSync(new URL("./CompareCars.css", import.meta.url), "utf8");
  for (const rule of css.match(/^\s*\.compare-count-pick[^{]*\{/gm) || []) {
    assert.fail(`"${rule.replace("{", "").trim()}" needs scoping under .compare-count`);
  }
  assert.match(css, /\.compare-count \.compare-count-pick/);
});
