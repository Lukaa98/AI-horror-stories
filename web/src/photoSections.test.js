import assert from "node:assert/strict";
import test from "node:test";
import { serializePhotos, closeupsFor, SLOTS, MAX_CLOSEUPS } from "./photoSections.js";

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
