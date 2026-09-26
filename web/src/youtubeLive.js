/* The channel snapshot, built in the browser.
 *
 * channel_status.py builds this same object on a runner and commits it to
 * the output branch, because the refresh token lives in Actions secrets.
 * With a signed-in user there is no token to hide, so the same calls can be
 * made from here and the round trip through a workflow disappears.
 *
 * The shape is copied from collect() deliberately: every consumer reads the
 * snapshot, so matching it means nothing downstream has to know which of
 * the two produced it. Whichever is newer wins.
 */
import { youtube } from "./googleAuth";

const MAX_VIDEOS = 50;
const CATEGORY_NAMES = {
  2: "Autos & Vehicles", 24: "Entertainment", 22: "People & Blogs",
  17: "Sports", 28: "Science & Technology",
};

const count = (value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
};

/** videos.list, falling back if a part is refused.
 *  "suggestions" needs ownership and is not always granted; losing the
 *  whole snapshot over an optional part would be a bad trade. */
async function listVideos(ids, parts) {
  try {
    return await youtube("videos", { params: { part: parts, id: ids.join(",") } });
  } catch {
    const reduced = parts.split(",").filter((part) => part !== "suggestions").join(",");
    return youtube("videos", { params: { part: reduced, id: ids.join(",") } });
  }
}

export async function collect() {
  const channels = await youtube("channels", {
    params: { part: "snippet,contentDetails,statistics", mine: "true" },
  });
  const channel = (channels.items || [])[0];
  if (!channel) throw new Error("This Google account is not attached to a channel.");
  const stats = channel.statistics || {};

  // The uploads playlist carries private and scheduled videos too, for the
  // owner -- which is the whole point, since those are the ones that cannot
  // be checked any other way.
  const playlist = await youtube("playlistItems", {
    params: {
      part: "contentDetails",
      playlistId: channel.contentDetails.relatedPlaylists.uploads,
      maxResults: String(MAX_VIDEOS),
    },
  });
  const ids = (playlist.items || []).map((row) => row.contentDetails.videoId);

  const videos = [];
  if (ids.length) {
    const detail = await listVideos(ids, "snippet,status,statistics,contentDetails,suggestions");
    for (const row of detail.items || []) {
      const snippet = row.snippet || {};
      const status = row.status || {};
      const counts = row.statistics || {};
      const thumbnails = snippet.thumbnails || {};
      const suggestions = row.suggestions || {};
      videos.push({
        id: row.id,
        title: snippet.title || "",
        privacy: status.privacyStatus || "",
        publish_at: status.publishAt || "",
        published_at: snippet.publishedAt || "",
        duration: (row.contentDetails || {}).duration || "",
        views: count(counts.viewCount),
        likes: count(counts.likeCount),
        comments: count(counts.commentCount),
        category: CATEGORY_NAMES[snippet.categoryId] || String(snippet.categoryId || ""),
        language: snippet.defaultAudioLanguage || snippet.defaultLanguage || "",
        synthetic: Boolean(status.containsSyntheticMedia),
        thumbnail: (thumbnails.medium || thumbnails.default || {}).url || "",
        // Whether YouTube is happy with the file itself. "processed" is the
        // only good answer; "rejected" and "failed" carry a reason, and a
        // video stuck on "uploaded" never finished.
        upload_status: status.uploadStatus || "",
        problem: status.rejectionReason || status.failureReason || "",
        made_for_kids: Boolean(status.madeForKids),
        license: status.license || "",
        embeddable: Boolean(status.embeddable),
        tags: (snippet.tags || []).length,
        described: Boolean((snippet.description || "").trim()),
        warnings: [...(suggestions.processingWarnings || []),
                   ...(suggestions.processingErrors || [])],
        suggestions: [...(suggestions.editorSuggestions || [])],
      });
    }
  }
  videos.sort((a, b) => String(b.published_at).localeCompare(String(a.published_at)));

  return {
    channel: {
      title: (channel.snippet || {}).title || "",
      subscribers: count(stats.subscriberCount),
      views: count(stats.viewCount),
      videos: count(stats.videoCount),
      hidden_subscribers: Boolean(stats.hiddenSubscriberCount),
    },
    videos,
    checked_at: new Date().toISOString(),
    // Which of the two built this. The workflow's snapshot knows which
    // build made each video; this one cannot, so the panel needs to tell
    // them apart rather than assume a missing build id means "no build".
    source: "browser",
  };
}

/* Acting on a video, signed in.
 *
 * videos.update REPLACES a part rather than merging into it, so sending a
 * status with only privacyStatus set blanks everything else on it -- the
 * kids flag, the licence, the synthetic-media declaration. Every one of
 * these therefore reads the current status first and writes it back with
 * only the field that is meant to change. upload_build.py does the same on
 * the runner, for the same reason.
 */
async function currentStatus(videoId) {
  const found = await youtube("videos", { params: { part: "status", id: videoId } });
  const video = (found.items || [])[0];
  if (!video) throw new Error(`YouTube no longer has ${videoId}.`);
  return video.status || {};
}

async function writeStatus(videoId, status) {
  return youtube("videos", {
    method: "PUT",
    params: { part: "status" },
    body: { id: videoId, status },
  });
}

/** Take a scheduled video public now, whatever it was scheduled for. */
export async function publishNow(videoId) {
  const status = await currentStatus(videoId);
  delete status.publishAt;
  return writeStatus(videoId, { ...status, privacyStatus: "public" });
}

/** Cancel the schedule and leave it private. */
export async function unschedule(videoId) {
  const status = await currentStatus(videoId);
  delete status.publishAt;
  return writeStatus(videoId, { ...status, privacyStatus: "private" });
}

/** Move the schedule. YouTube ignores publishAt on anything but a private
 *  video, so the privacy is set alongside it rather than assumed. */
export async function setPublishTime(videoId, publishAt) {
  const status = await currentStatus(videoId);
  return writeStatus(videoId, { ...status, privacyStatus: "private", publishAt });
}

export async function deleteVideo(videoId) {
  return youtube("videos", { method: "DELETE", params: { id: videoId } });
}
