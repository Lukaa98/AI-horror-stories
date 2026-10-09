/* The numbers Studio shows and the Data API does not.
 *
 * Views, likes and comments are public, so videos.list carries them. Average
 * view duration, watch time, subscribers gained and the feed-versus-search
 * split are owner-only and live in a different service entirely:
 * youtubeanalytics.googleapis.com, not googleapis.com/youtube/v3.
 *
 * That service needs its own scope, so a token granted before this existed
 * will come back 403. The caller is told that in words rather than being
 * handed an empty object, because an empty object is indistinguishable from
 * a video nobody watched -- which is exactly how a broken transcript reader
 * looked fine for three runs.
 *
 * Untested against a live channel: this session cannot reach YouTube at all.
 * Every metric name here is from Google's reference rather than from a
 * response I have seen, so the first real call is the test.
 */
const ENDPOINT = "https://youtubeanalytics.googleapis.com/v2/reports";

// Owner-only metrics that the Data API has no equivalent for.
const METRICS = [
  "views",
  "estimatedMinutesWatched",
  "averageViewDuration",
  "averageViewPercentage",
  "subscribersGained",
].join(",");

// Studio's labels for what the API calls these.
const SOURCE_NAMES = {
  SHORTS: "Shorts feed",
  YT_SEARCH: "YouTube search",
  RELATED_VIDEO: "Suggested videos",
  SUBSCRIBER: "Subscriptions",
  CHANNEL: "Channel pages",
  PLAYLIST: "Playlists",
  NOTIFICATION: "Notifications",
  EXT_URL: "External",
  NO_LINK_OTHER: "Direct or unknown",
  YT_CHANNEL: "Channel pages",
  YT_OTHER_PAGE: "Other YouTube features",
  PROMOTED: "Promoted",
  ADVERTISING: "Advertising",
  END_SCREEN: "End screens",
  HASHTAGS: "Hashtag pages",
  SOUND_PAGE: "Sound pages",
};

function day(date) {
  return date.toISOString().slice(0, 10);
}

async function report(token, params) {
  const query = new URLSearchParams({ ids: "channel==MINE", ...params }).toString();
  const res = await fetch(`${ENDPOINT}?${query}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 403) {
    throw new Error(
      "YouTube refused the analytics request (403). The sign-in most likely predates the "
      + "analytics permission -- sign out and in again to grant it."
    );
  }
  if (res.status === 401) {
    throw new Error("The YouTube sign-in expired; sign in again.");
  }
  if (!res.ok) {
    throw new Error(`YouTube Analytics said ${res.status}: ${(await res.text()).slice(0, 300)}`);
  }
  return res.json();
}

/** Index a report's rows by the column names it says it returned. */
function rowsByName(payload) {
  const names = (payload.columnHeaders || []).map((column) => column.name);
  return (payload.rows || []).map((row) => {
    const out = {};
    names.forEach((name, index) => { out[name] = row[index]; });
    return out;
  });
}

/**
 * Per-video analytics for the ids given.
 *
 * Returns {byVideo: {id: {...}}, since, until}. Two requests, not two per
 * video: the video dimension carries the whole batch in one call.
 */
export async function forVideos(token, ids, { since } = {}) {
  const wanted = (ids || []).filter(Boolean).slice(0, 200);
  if (!wanted.length) return { byVideo: {}, since: "", until: "" };

  const until = day(new Date());
  // The channel is weeks old, so a wide window costs nothing and a narrow
  // one would silently clip a video published before it.
  const start = since || day(new Date(Date.now() - 400 * 24 * 3600 * 1000));
  const filters = `video==${wanted.join(",")}`;

  const totals = rowsByName(await report(token, {
    startDate: start, endDate: until, metrics: METRICS,
    dimensions: "video", filters, maxResults: "200",
  }));

  // Traffic sources in the same shape -- one row per video per source.
  let sources = [];
  try {
    sources = rowsByName(await report(token, {
      startDate: start, endDate: until, metrics: "views",
      dimensions: "video,insightTrafficSourceType", filters,
      sort: "-views", maxResults: "500",
    }));
  } catch {
    // The totals are the point, so a split that fails is dropped rather
    // than failing the whole call. Any error that matters -- a missing
    // scope, an expired token -- already came out of the totals above.
    sources = [];
  }

  const byVideo = {};
  for (const row of totals) {
    byVideo[row.video] = {
      views: row.views ?? null,
      minutes_watched: row.estimatedMinutesWatched ?? null,
      average_view_seconds: row.averageViewDuration ?? null,
      average_view_percent: row.averageViewPercentage ?? null,
      subscribers_gained: row.subscribersGained ?? null,
      traffic: [],
    };
  }
  for (const row of sources) {
    const entry = byVideo[row.video];
    if (!entry) continue;
    entry.traffic.push({
      source: SOURCE_NAMES[row.insightTrafficSourceType] || row.insightTrafficSourceType,
      views: row.views ?? 0,
    });
  }
  // Share of that video's own traffic, which is the thing worth reading --
  // a feed share of 96% against 20% is the signal, not the raw count.
  for (const entry of Object.values(byVideo)) {
    const total = entry.traffic.reduce((sum, item) => sum + (item.views || 0), 0);
    entry.traffic.sort((a, b) => b.views - a.views);
    for (const item of entry.traffic) {
      item.share = total ? Math.round((item.views / total) * 1000) / 10 : 0;
    }
  }
  return { byVideo, since: start, until };
}
