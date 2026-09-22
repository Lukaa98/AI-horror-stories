import assert from "node:assert/strict";
import test from "node:test";
import { serializePhotos, parseExtraPhotos, closeupsFor, SLOTS, MAX_CLOSEUPS } from "./photoSections.js";

test("nested close-ups carry their slot through serialization, blanks do not", () => {
  const input = [
    { id: "a", slot: "front", label: " Exhaust tip ", url: " https://example.com/a.jpg ", note: " Carbon " },
    { id: "b", slot: "rear", label: "Blank", url: " " },
  ];
  assert.deepEqual(serializePhotos(input), [
    { id: "a", slot: "front", label: "Exhaust tip", url: "https://example.com/a.jpg", note: "Carbon" },
  ]);
  assert.equal(input[0].label, " Exhaust tip ", "serialization must not mutate the editor");
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
