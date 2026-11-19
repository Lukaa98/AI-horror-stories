import assert from "node:assert/strict";
import test from "node:test";
import { serializePhotos, parseExtraPhotos, closeupsFor, SLOTS, MAX_CLOSEUPS,
         blurFractions } from "./photoSections.js";

test("nested close-ups carry their slot through serialization, blanks do not", () => {
  const input = [
    { id: "a", slot: "front", label: " Exhaust tip ", url: " https://example.com/a.jpg ", note: " Carbon " },
    { id: "b", slot: "rear", label: "Blank", url: " " },
  ];
  assert.deepEqual(serializePhotos(input), [
    { id: "a", slot: "front", label: "Exhaust tip", url: "https://example.com/a.jpg",
      note: "Carbon", describe: false },
  ]);
  assert.equal(input[0].label, " Exhaust tip ", "serialization must not mutate the editor");
});

test("a ticked close-up says so, an unticked one says so too", () => {
  // Shown either way. The flag only decides whether the script is told
  // what is in it, and every photo the script talks about is a beat it
  // does not spend on a note.
  const [ticked] = serializePhotos([
    { id: "a", slot: "front", label: "Exhaust", url: "https://x/a.jpg", describe: true }]);
  assert.equal(ticked.describe, true);
  const [plain] = serializePhotos([
    { id: "b", slot: "rear", label: "Wheel", url: "https://x/b.jpg" }]);
  assert.equal(plain.describe, false, "absent means not ticked");
});

test("the flag survives a round trip through a saved build", () => {
  const json = JSON.stringify(serializePhotos([
    { id: "a", slot: "front", label: "Exhaust", url: "https://x/a.jpg", describe: true },
    { id: "b", slot: "rear", label: "Wheel", url: "https://x/b.jpg", describe: false }]));
  assert.deepEqual(parseExtraPhotos(json).map(p => p.describe), [true, false]);
  // A build made before the box existed restores as unticked rather than
  // silently narrating every close-up it has.
  const older = JSON.stringify([{ slot: "front", label: "Exhaust", url: "https://x/a.jpg" }]);
  assert.deepEqual(parseExtraPhotos(older).map(p => p.describe), [false]);
});

test("legacy ungrouped extras keep their original contract", () => {
  assert.deepEqual(serializePhotos([{ id: "old", label: "Gauges", url: "https://example.com/g.jpg" }]),
    [{ id: "old", label: "Gauges", url: "https://example.com/g.jpg" }]);
});

test("close-ups group by slot and the editor offers the five pipeline slots", () => {
  const photos = [{ id: "1", slot: "front" }, { id: "2", slot: "rear" }, { id: "3", slot: "front" }];
  assert.deepEqual(closeupsFor(photos, "front").map(p => p.id), ["1", "3"]);
  assert.deepEqual(SLOTS.map(([id]) => id), ["front", "side", "rear", "engine", "interior"]);
  assert.equal(MAX_CLOSEUPS, 4);
});

test("a past build's close-ups round-trip back into the editor", () => {
  const editor = [
    { id: "a", slot: "front", label: "Exhaust tip", url: "https://example.com/a.jpg", note: "Carbon" },
    { id: "b", slot: "interior", label: "", url: "https://example.com/b.jpg", note: "" },
  ];
  const restored = parseExtraPhotos(JSON.stringify(serializePhotos(editor)));
  assert.deepEqual(restored.map(p => [p.slot, p.label, p.url]),
    [["front", "Exhaust tip", "https://example.com/a.jpg"],
     ["interior", "", "https://example.com/b.jpg"]]);
  // Fresh ids, so restored rows cannot collide with rows already in the form.
  assert.equal(restored.some(p => p.id === "a"), false);
});

test("nothing usable in a stored value yields no close-up rows", () => {
  assert.deepEqual(parseExtraPhotos(""), []);
  assert.deepEqual(parseExtraPhotos("not json"), []);
  // A legacy ungrouped extra has no slot, so it has nowhere to go in the
  // slot editor -- better absent than rendered under the wrong heading.
  assert.deepEqual(parseExtraPhotos('[{"id":"old","label":"Gauges","url":"https://x/g.jpg"}]'), []);
  assert.deepEqual(parseExtraPhotos('[{"slot":"front","url":"  "}]'), []);
});

test("a plate box rides with the close-up it was drawn on", () => {
  // Only when there is one: an empty key on twenty rows is twenty keys of
  // nothing, in an input that already carries twenty long urls.
  const [withBox, without] = serializePhotos([
    { id: "a", label: "Rear", url: "https://example.com/a.jpg", slot: "rear",
      blur: { left: "35", top: "55", width: "25", height: "15" } },
    { id: "b", label: "Tips", url: "https://example.com/b.jpg", slot: "rear" },
  ]);
  assert.deepEqual(withBox.blur, { x: 0.35, y: 0.55, w: 0.25, h: 0.15 });
  assert.ok(!("blur" in without));
});

test("a box that cannot be read is no box, not a wrong one", () => {
  for (const bad of [undefined, null, {}, { left: "", top: "0", width: "10", height: "10" },
                     { left: "x", top: "0", width: "10", height: "10" },
                     { left: "0", top: "0", width: "0", height: "10" }]) {
    assert.equal(blurFractions(bad), null, JSON.stringify(bad));
  }
  // Numbers already in fractions pass straight through, so a box restored
  // from a previous build is not re-read as percentages.
  const already = { x: 0.1, y: 0.2, w: 0.3, h: 0.4 };
  assert.deepEqual(blurFractions(already), already);
  // And a box dragged past the edge means "to the edge".
  assert.deepEqual(blurFractions({ left: "80", top: "90", width: "50", height: "50" }),
                   { x: 0.8, y: 0.9, w: 0.2, h: 0.1 });
});
