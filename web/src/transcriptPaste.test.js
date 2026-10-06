import assert from "node:assert/strict";
import test from "node:test";
import { parseTranscript, beats, summarise, fromPaste } from "./transcriptPaste.js";

/* The panel copies in two shapes depending on where you drag the cursor, and
 * it never gives end times. Both shapes have to land on the same cues. */

test("a stamp on its own line pairs with the line under it", () => {
  const cues = parseTranscript("0:00\nfour hundred horsepower\n0:03\nand nobody knows");
  assert.deepEqual(cues.map((c) => c.text), ["four hundred horsepower", "and nobody knows"]);
  assert.equal(cues[0].start, 0);
  assert.equal(cues[1].start, 3);
  assert.equal(cues[0].duration, 3, "a cue runs until the next one starts");
});

test("a stamp leading its own line parses the same way", () => {
  const cues = parseTranscript("0:00 four hundred horsepower\n0:03 and nobody knows");
  assert.deepEqual(cues.map((c) => c.text), ["four hundred horsepower", "and nobody knows"]);
  assert.equal(cues[1].start, 3);
});

test("hours parse, and a wrapped line joins the cue above it", () => {
  const cues = parseTranscript("1:02:05\nstill going\nand wrapped onto a second line");
  assert.equal(cues.length, 1);
  assert.equal(cues[0].start, 3725);
  assert.equal(cues[0].text, "still going and wrapped onto a second line");
});

test("the last cue gets the median gap, not zero", () => {
  const cues = parseTranscript("0:00\na\n0:02\nb\n0:04\nc");
  assert.equal(cues[2].duration, 2, "guessed from the others rather than left at nothing");
});

test("beats group at the pauses", () => {
  const grouped = beats([
    { text: "three hundred horsepower", start: 0, duration: 1 },
    { text: "and nobody noticed", start: 1.1, duration: 0.9 },  // 0.10 gap: same beat
    { text: "it was built in Japan", start: 3.0, duration: 1.2 }, // 1.00 gap: new beat
  ]);
  assert.equal(grouped.length, 2);
  assert.equal(grouped[0].words, 6);
  assert.equal(grouped[1].words, 5);
});

test("the shape measures the opening on its own", () => {
  const shape = summarise("abcdefghijk", [
    { text: "three hundred horsepower", start: 0.5, duration: 1.0 },
    { text: "and nobody noticed", start: 1.6, duration: 0.9 },
    { text: "would you buy one?", start: 4.0, duration: 1.5 },
  ]);
  assert.equal(shape.words, 10);
  assert.equal(shape.beats, 2);
  assert.equal(shape.opening_words, 6);
  assert.equal(shape.closes_on_question, true);
  // Spelled-out numbers count: a spoken hook says "three hundred", not "300".
  assert.equal(shape.opening_has_number, true);
});

test("an id is lifted out of whatever labels the paste", () => {
  const text = "0:00\nfour hundred horsepower\n0:03\nand nobody knows";
  assert.equal(fromPaste(text, "https://www.youtube.com/shorts/2dxa9oz1AZw").video_id, "2dxa9oz1AZw");
  assert.equal(fromPaste(text, "2dxa9oz1AZw").video_id, "2dxa9oz1AZw");
  assert.equal(fromPaste(text, "").video_id, "pasted");
});

test("a paste with nothing in it says so rather than pretending", () => {
  assert.equal(summarise("x", []).skipped, "nothing parsed out of that paste");
  assert.deepEqual(parseTranscript(""), []);
  assert.deepEqual(parseTranscript(null), []);
  assert.deepEqual(parseTranscript("no timestamps here at all"), []);
});
