import assert from "node:assert/strict";
import test from "node:test";
import { serializePhotos } from "./photoSections.js";

test("group metadata survives workflow serialization, blanks do not", () => {
  const input = [{ id: "a", section: "interior", role: "hero", label: " Cabin ", url: " https://example.com/a.jpg ", note: " Look at the wheel " },
    { id: "b", section: "exterior", role: "detail", label: "Blank", url: " " }];
  assert.deepEqual(serializePhotos(input), [{ id: "a", section: "interior", role: "hero", label: "Cabin", url: "https://example.com/a.jpg", note: "Look at the wheel" }]);
  assert.equal(input[0].label, " Cabin ", "serialization must not mutate the editor");
});

test("legacy extras keep their ungrouped contract", () => {
  assert.deepEqual(serializePhotos([{ id: "old", label: "Gauges", url: "https://example.com/g.jpg" }]),
    [{ id: "old", label: "Gauges", url: "https://example.com/g.jpg" }]);
});
