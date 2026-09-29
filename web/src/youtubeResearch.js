/* Reading another channel through the Data API.
 *
 * Public data, fetched with the signed-in token the dashboard already
 * holds. No key, no workflow, no scraping -- and quota is a handful of
 * units per channel, because uploads come back fifty at a time.
 */
import { youtube } from "./googleAuth";

// One page of a channel's uploads is fifty. Four pages is enough to see a
// year of daily posting, and enough to tell a climb from a lucky break.
const MAX_PAGES = 4;

const count = (value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
};

async function findChannel(handle) {
  const cleaned = handle.replace(/^@/, "").trim();
  // forHandle is exact and cheap; search costs a hundred units and guesses.
  const byHandle = await youtube("channels", {
    params: { part: "snippet,contentDetails,statistics", forHandle: `@${cleaned}` },
  });
  if ((byHandle.items || []).length) return byHandle.items[0];

  // A channel id pasted instead of a handle still works.
  if (/^UC[\w-]{20,}$/.test(cleaned)) {
    const byId = await youtube("channels", {
      params: { part: "snippet,contentDetails,statistics", id: cleaned },
    });
    if ((byId.items || []).length) return byId.items[0];
  }
  throw new Error(`No channel called ${handle}. Use the @handle from its URL.`);
}

export async function channelReport(handle) {
  const channel = await findChannel(handle);
  const uploads = channel.contentDetails.relatedPlaylists.uploads;

  const ids = [];
  let pageToken;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const listed = await youtube("playlistItems", {
      params: { part: "contentDetails", playlistId: uploads, maxResults: "50",
                ...(pageToken ? { pageToken } : {}) },
    });
    ids.push(...(listed.items || []).map((row) => row.contentDetails.videoId));
    pageToken = listed.nextPageToken;
    if (!pageToken) break;
  }

  const videos = [];
  for (let i = 0; i < ids.length; i += 50) {
    const detail = await youtube("videos", {
      params: { part: "snippet,statistics,contentDetails", id: ids.slice(i, i + 50).join(",") },
    });
    for (const row of detail.items || []) {
      videos.push({
        id: row.id,
        title: (row.snippet || {}).title || "",
        published: (row.snippet || {}).publishedAt || "",
        views: count((row.statistics || {}).viewCount),
        likes: count((row.statistics || {}).likeCount),
        comments: count((row.statistics || {}).commentCount),
        duration: (row.contentDetails || {}).duration || "",
      });
    }
  }

  const stats = channel.statistics || {};
  return {
    channel: {
      title: (channel.snippet || {}).title || "",
      subscribers: count(stats.subscriberCount),
      views: count(stats.viewCount),
      videos: count(stats.videoCount),
    },
    videos,
    read_at: new Date().toISOString(),
  };
}
