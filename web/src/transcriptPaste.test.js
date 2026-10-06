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

test("beats are sentences, because pasted cues have no pauses to group on", () => {
  // Each pasted cue ends exactly where the next begins -- the panel gives
  // start times only -- so pause-grouping put a whole script in one beat.
  const grouped = beats([
    { text: "People hated this car's headlights.", start: 0, duration: 4 },
    { text: "This is the Impreza. The reaction was brutal.", start: 4, duration: 4 },
  ]);
  assert.deepEqual(grouped.map((b) => b.text), [
    "People hated this car's headlights.",
    "This is the Impreza.",
    "The reaction was brutal.",
  ]);
  assert.equal(grouped[0].words, 5);
  assert.ok(grouped[1].start >= 4, "a sentence is timed from where its words fall");
});

test("the shape measures the opening on its own", () => {
  const shape = summarise("abcdefghijk", [
    { text: "Three hundred horsepower, and nobody noticed.", start: 0.5, duration: 2.0 },
    { text: "Would you buy one?", start: 2.5, duration: 1.5 },
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
