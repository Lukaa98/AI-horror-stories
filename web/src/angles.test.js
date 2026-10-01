import assert from "node:assert/strict";
import test from "node:test";
import { splitAngles } from "./angles.js";

/* These cases are pinned identically in tests/test_single_car_short.py
 * against _split_angles. Two copies of one rule drift; this is the leash. */

test("wrapped notes stay one bullet, so the ranking is the one typed", () => {
  assert.deepEqual(
    splitAngles(
      "McLaren wanted a sports car, Mercedes wanted a GT - planned 3,500 cars,\n"
      + "sold ~1,400 by end of 2007.\n"
      + "617hp carbon supercar with a 5-speed torque converter auto - the complaint\n"
      + "that sticks, and why it sat still while the Enzo and Carrera GT climbed.\n"
      + "722 Edition: 150 built, 641 bhp, 44kg lighter.",
    ),
    [
      "McLaren wanted a sports car, Mercedes wanted a GT - planned 3,500 cars, "
      + "sold ~1,400 by end of 2007.",
      "617hp carbon supercar with a 5-speed torque converter auto - the complaint "
      + "that sticks, and why it sat still while the Enzo and Carrera GT climbed.",
      "722 Edition: 150 built, 641 bhp, 44kg lighter.",
    ],
  );
});

test("the join does not depend on punctuation, which notes rarely carry", () => {
  assert.deepEqual(
    splitAngles("722 badge = Moss's 7:22am start\nat the 1955 Mille Miglia\n150 built, 641 bhp"),
    ["722 badge = Moss's 7:22am start at the 1955 Mille Miglia", "150 built, 641 bhp"],
  );
});

test("markers and blank lines start a bullet whatever its case", () => {
  assert.deepEqual(splitAngles("- first\n- second\n- third"), ["first", "second", "third"]);
  assert.deepEqual(splitAngles("1. first\n2. second"), ["first", "second"]);
  assert.deepEqual(splitAngles("the gearbox\n\nonly the SLR didn't go up"),
    ["the gearbox", "only the SLR didn't go up"]);
});

test("nothing typed is no bullets, and a lone dash is not a note", () => {
  assert.deepEqual(splitAngles(""), []);
  assert.deepEqual(splitAngles(null), []);
  assert.deepEqual(splitAngles(undefined), []);
  assert.deepEqual(splitAngles("  \n\n - \n"), []);
});

test("a leading minus sign survives when nothing follows it", () => {
  assert.deepEqual(splitAngles("-40kg lighter\n- real bullet"), ["-40kg lighter", "real bullet"]);
});
