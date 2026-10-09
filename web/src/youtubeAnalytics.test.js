import assert from "node:assert/strict";
import test from "node:test";
import { forVideos } from "./youtubeAnalytics.js";

function fakeFetch(responses) {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    const next = responses.shift();
    return {
      ok: next.status === undefined || next.status === 200,
      status: next.status || 200,
      json: async () => next.body,
      text: async () => JSON.stringify(next.body || {}),
    };
  };
  return calls;
}

const TOTALS = {
  columnHeaders: [
    { name: "video" }, { name: "views" }, { name: "estimatedMinutesWatched" },
    { name: "averageViewDuration" }, { name: "averageViewPercentage" },
    { name: "subscribersGained" },
  ],
  rows: [["abc12345678", 586, 36, 15, 23.4, 0], ["def12345678", 1066, 60, 16, 33.1, 1]],
};
const SOURCES = {
  columnHeaders: [{ name: "video" }, { name: "insightTrafficSourceType" }, { name: "views" }],
  rows: [["abc12345678", "SHORTS", 563], ["abc12345678", "YT_SEARCH", 8],
         ["abc12345678", "YT_OTHER_PAGE", 15]],
};

test("per-video analytics come back keyed by id, in one call per report", async () => {
  const calls = fakeFetch([{ body: TOTALS }, { body: SOURCES }]);
  const { byVideo } = await forVideos("tok", ["abc12345678", "def12345678"]);

  assert.equal(calls.length, 2, "the video dimension carries the batch; not one call each");
  assert.equal(byVideo["abc12345678"].views, 586);
  assert.equal(byVideo["abc12345678"].average_view_seconds, 15);
  assert.equal(byVideo["def12345678"].subscribers_gained, 1);
});

test("traffic is a share of that video's own views, biggest first", async () => {
  fakeFetch([{ body: TOTALS }, { body: SOURCES }]);
  const { byVideo } = await forVideos("tok", ["abc12345678"]);
  const traffic = byVideo["abc12345678"].traffic;

  assert.equal(traffic[0].source, "Shorts feed", "sorted by views");
  assert.equal(traffic[0].share, 96.1, "563 of 586");
  assert.equal(traffic.at(-1).source, "YouTube search");
  // Studio's own wording, not the API's enum.
  assert.ok(!traffic.some((item) => item.source === "SHORTS"));
});

test("a 403 says the scope is missing rather than returning nothing", async () => {
  fakeFetch([{ status: 403, body: {} }]);
  await assert.rejects(() => forVideos("tok", ["abc12345678"]),
    /analytics permission/, "an empty result is indistinguishable from a video nobody watched");
});

test("a failed traffic split does not lose the totals", async () => {
  fakeFetch([{ body: TOTALS }, { status: 500, body: {} }]);
  const { byVideo } = await forVideos("tok", ["abc12345678"]);
  assert.equal(byVideo["abc12345678"].views, 586);
  assert.deepEqual(byVideo["abc12345678"].traffic, []);
});

test("asking about nothing asks YouTube nothing", async () => {
  const calls = fakeFetch([]);
  assert.deepEqual((await forVideos("tok", [])).byVideo, {});
  assert.equal(calls.length, 0);
});
