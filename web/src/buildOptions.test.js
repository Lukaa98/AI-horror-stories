import assert from "node:assert/strict";
import test from "node:test";
import { buildOptions, buildOptionsLine } from "./buildOptions.js";

const labels = (result) => buildOptions(result).map((badge) => badge.label);

test("the style badge is always there, so a plain build says so", () => {
  // The four-way comparison this exists for needs "this one was plain" to be
  // as visible as "this one was dense". An absent badge reads as missing data.
  assert.deepEqual(labels({ build_inputs: { script_style: "classic" } }), ["Classic script"]);
  assert.deepEqual(labels({ build_inputs: { script_style: "dense" } }), ["Dense script"]);
  assert.deepEqual(labels({ build_inputs: { script_style: "short" } }), ["35s short"]);
  // Old builds recorded no style at all; the pipeline's default was classic.
  assert.deepEqual(labels({ build_inputs: { make: "Audi" } }), ["Classic script"]);
});

test("a build with nothing recorded claims nothing", () => {
  // result.json predating build_inputs. A row of "off" badges would be a
  // guess dressed as a fact.
  assert.deepEqual(buildOptions({ word_count: 147 }), []);
  assert.deepEqual(buildOptions(null), []);
  assert.deepEqual(buildOptionsLine({}), "");
});

test("the four runs of one car come out distinguishable", () => {
  const dense = { build_inputs: { script_style: "dense", auction_url: "https://x/1", angles: "quad tips\nV8" } };
  const plain = { build_inputs: { script_style: "classic", auction_url: "https://x/1", angles: "quad tips\nV8" } };
  const urlOnly = { build_inputs: { script_style: "classic", auction_url: "https://x/1" } };
  const bare = { build_inputs: { script_style: "classic" } };
  assert.deepEqual(buildOptionsLine(dense), "Dense script · Listing URL · 2 notes");
  assert.deepEqual(buildOptionsLine(plain), "Classic script · Listing URL · 2 notes");
  assert.deepEqual(buildOptionsLine(urlOnly), "Classic script · Listing URL");
  assert.deepEqual(buildOptionsLine(bare), "Classic script");
});

test("notes are counted by the same split the prompt ranks them with", () => {
  // A wrapped line is one note, not two -- splitAngles decides that, and a
  // second counting rule here would drift from the one that matters.
  const wrapped = { build_inputs: { angles: "- the engine is a quad-turbo\n  which nobody mentions" } };
  assert.deepEqual(labels(wrapped), ["Classic script", "1 note"]);
  const badge = buildOptions(wrapped)[1];
  assert.equal(badge.title, "1. the engine is a quad-turbo which nobody mentions",
               "the hover shows the ranking, which the count cannot");
  assert.deepEqual(labels({ build_inputs: { angles: "   \n  " } }), ["Classic script"],
                   "whitespace is not a note");
});

test("manual photos count slots and pasted extras together", () => {
  assert.deepEqual(labels({ build_inputs: { photo_front: "https://x/f.jpg" } }),
                   ["Classic script", "1 manual photo"]);
  // extra_photos is the JSON the create form serialises, not a list of
  // lines -- splitting the real 1.7kB string on commas reported thirty-odd
  // photos for the seven that were actually pasted.
  const extras = JSON.stringify([
    { id: "a", slot: "rear", label: "back close up", url: "https://x/a.jpg", note: "" },
    { id: "b", slot: "front", label: "headlights", url: "https://x/b.jpg", note: "" },
  ]);
  assert.deepEqual(labels({
    build_inputs: { photo_front: "https://x/f.jpg", photo_rear: "https://x/r.jpg", extra_photos: extras },
  }), ["Classic script", "4 manual photos"]);
  // Empty strings are what the pipeline records for an unused slot.
  assert.deepEqual(labels({ build_inputs: { photo_front: "", photo_side: "", extra_photos: "" } }),
                   ["Classic script"]);
  // A build whose extras are unreadable counts none rather than crashing
  // the card it is drawn on.
  assert.deepEqual(labels({ build_inputs: { extra_photos: "not json" } }), ["Classic script"]);
});

test("the comparison and rival settings read as they were set", () => {
  assert.deepEqual(labels({ build_inputs: { disable_comparison: "true" } }),
                   ["Classic script", "No comparison"]);
  // The pipeline writes the string "false", which is truthy in JS -- the
  // reason this is pinned.
  assert.deepEqual(labels({ build_inputs: { disable_comparison: "false" } }), ["Classic script"]);
  assert.deepEqual(labels({ build_inputs: { rival_car: "BMW M3" } }), ["Classic script", "vs BMW M3"]);
  assert.deepEqual(labels({ build_inputs: { photo_rival: "https://x/rival.jpg" } }),
                   ["Classic script", "Rival photo"]);
});

test("a price told to the script is shown, with the figure on hover", () => {
  const badges = buildOptions({ build_inputs: { current_price: "$300k" } });
  assert.deepEqual(badges.map((b) => b.label), ["Classic script", "Price given"]);
  assert.match(badges[1].title, /\$300k/);
});

test("the listing badge carries the url it was given", () => {
  const badges = buildOptions({ build_inputs: { auction_url: "https://bringatrailer.com/listing/x" } });
  assert.equal(badges[1].title, "https://bringatrailer.com/listing/x");
});
