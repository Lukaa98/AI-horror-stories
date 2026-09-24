import { useCallback, useEffect, useRef, useState } from "react";
import "./UploadPanel.css";

const UPLOAD_WORKFLOW = "youtube-upload.yml";
const OUTPUT_BRANCH = "cars-output";
const BUILD_ROOT = "cars/single-car-shorts";

function rawUrl(settings, buildId, name) {
  return `https://raw.githubusercontent.com/${settings.owner}/${settings.repo}`
    + `/${OUTPUT_BRANCH}/${BUILD_ROOT}/${buildId}/${name}`;
}
const POLL_MS = 6000;
// The workflow gives up at 30 minutes; past that there is nothing coming.
const GIVE_UP_MS = 31 * 60 * 1000;

async function readJson(settings, path) {
  const { owner, repo, token } = settings;
  const res = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/contents/${path}?ref=${encodeURIComponent(OUTPUT_BRANCH)}`,
    { headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json" }, cache: "no-store" }
  );
  if (!res.ok) return null;
  const data = await res.json();
  const bytes = Uint8Array.from(atob(data.content.replace(/\n/g, "")), (c) => c.charCodeAt(0));
  return JSON.parse(new TextDecoder("utf-8").decode(bytes));
}

// The listing for one finished build, and the button that sends it. The
// title and description are shown as they will appear, because the whole
// reason upload.json is written at build time is so nobody publishes text
// they have not read.
export default function UploadPanel({ settings, buildId }) {
  const [listing, setListing] = useState(null);
  const [loading, setLoading] = useState(true);
  const [state, setState] = useState("idle");   // idle | sending | watching | done | error
  const [error, setError] = useState(null);
  const [scheduled, setScheduled] = useState(false);
  // Tomorrow at 00:15, which is the slot the channel posts in. Prefilled
  // rather than blank so scheduling is one click when it is the usual time.
  const [publishAt, setPublishAt] = useState(() => {
    const at = new Date();
    at.setDate(at.getDate() + 1);
    at.setHours(0, 15, 0, 0);
    const pad = (n) => String(n).padStart(2, "0");
    return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`
      + `T${pad(at.getHours())}:${pad(at.getMinutes())}`;
  });
  const startedAt = useRef(0);
  const timer = useRef(null);

  const path = `${BUILD_ROOT}/${buildId}/upload.json`;
  const ready = Boolean(settings.token && settings.owner && settings.repo);

  const stop = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
  }, []);

  useEffect(() => stop, [stop]);

  useEffect(() => {
    if (!ready) return undefined;
    let live = true;
    setLoading(true);
    readJson(settings, path)
      .then((found) => {
        if (!live) return;
        setListing(found);
        if (found?.video_id) setState("done");
      })
      .catch(() => {})
      .finally(() => live && setLoading(false));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, path, settings.owner, settings.repo, settings.token]);

  const poll = useCallback(async () => {
    try {
      const found = await readJson(settings, path);
      if (found?.video_id) {
        stop();
        setListing(found);
        setState("done");
        return;
      }
      if (Date.now() - startedAt.current > GIVE_UP_MS) {
        stop();
        setState("error");
        setError("The upload did not finish. Check the Actions log.");
      }
    } catch (err) {
      stop();
      setState("error");
      setError(String(err.message || err));
    }
  }, [settings, path, stop]);

  // A local datetime-local value carries no offset, so it is turned into
  // one here using this browser's own zone. Midnight means a different
  // instant in every zone, and YouTube is being told an instant.
  function withLocalOffset(value) {
    if (!value) return "";
    const at = new Date(value);
    if (Number.isNaN(at.getTime())) return "";
    const minutes = -at.getTimezoneOffset();
    const sign = minutes < 0 ? "-" : "+";
    const pad = (n) => String(Math.floor(Math.abs(n))).padStart(2, "0");
    return `${value}:00${sign}${pad(minutes / 60)}:${pad(minutes % 60)}`;
  }

  async function upload() {
    setError(null);
    setState("sending");
    startedAt.current = Date.now();
    try {
      const { owner, repo, branch, token } = settings;
      const res = await fetch(
        `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${UPLOAD_WORKFLOW}/dispatches`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: "application/vnd.github+json",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            ref: branch || "v11",
            inputs: {
              build_id: buildId,
              ...(publishAt ? { publish_at: withLocalOffset(publishAt) } : {}),
            },
          }),
        }
      );
      if (!res.ok) throw new Error(`Dispatch failed (${res.status}): ${await res.text()}`);
      setState("watching");
      stop();
      timer.current = setInterval(poll, POLL_MS);
    } catch (err) {
      setState("error");
      setError(String(err.message || err));
    }
  }

  if (loading) return <p className="hint">Checking the listing…</p>;

  if (!listing) {
    return (
      <p className="hint">
        No upload.json for this build — it was rendered before listings were written.
        Re-run the pipeline to get one.
      </p>
    );
  }

  return (
    <section className="upload-panel">
      <h3>YouTube</h3>

      {/* Everything that is about to be published, in one place: the still
          a browsing viewer sees first, then the words under it. */}
      <div className="upload-review">
        {listing.thumbnail && (
          <a className="upload-thumb" href={rawUrl(settings, buildId, listing.thumbnail)}
             target="_blank" rel="noreferrer">
            <img src={rawUrl(settings, buildId, listing.thumbnail)} alt="Channel thumbnail" />
          </a>
        )}
        <div className="upload-review-body">

      {state === "done" && listing.video_id ? (
        <p className="upload-done">
          Uploaded as <a href={`https://youtu.be/${listing.video_id}`} target="_blank" rel="noreferrer">
            youtu.be/{listing.video_id}</a>{" "}
          — {listing.publish_at
              ? `scheduled for ${new Date(listing.publish_at).toLocaleString()}`
              : "it is private. Review it, then publish from YouTube Studio."}
          {listing.thumbnail_set === false && " The custom thumbnail was refused; the channel may not be verified yet."}
        </p>
      ) : (
        <>
          <dl className="upload-fields">
            <dt>Title</dt>
            <dd>{listing.title}</dd>
            <dt>Description</dt>
            <dd className="upload-description">{listing.description}</dd>
            <dt>Tags</dt>
            <dd>{(listing.tags || []).join(", ")}</dd>
          </dl>
          <label className="upload-schedule">
            <span>Publish</span>
            <select value={scheduled ? "at" : "now"}
                    onChange={(e) => setScheduled(e.target.value === "at")}
                    disabled={state === "sending" || state === "watching"}>
              <option value="now">Keep private</option>
              <option value="at">Schedule</option>
            </select>
            {scheduled && (
              <input type="datetime-local" value={publishAt}
                     onChange={(e) => setPublishAt(e.target.value)}
                     disabled={state === "sending" || state === "watching"} />
            )}
          </label>
          {scheduled && (
            <p className="hint">
              YouTube makes it public at that moment, in this computer's timezone.
              It stays private until then.
            </p>
          )}
          <button type="button" className="upload-go" onClick={upload}
                  disabled={!ready || state === "sending" || state === "watching"}>
            {state === "watching" ? "Uploading…" : state === "sending" ? "Starting…"
              : scheduled ? "Upload and schedule" : "Upload to YouTube (private)"}
          </button>
          {state === "watching" && (
            <p className="hint">Sending the video. This usually takes a couple of minutes.</p>
          )}
        </>
      )}
      {state === "error" && <p className="upload-error">{error}</p>}
        </div>
      </div>
    </section>
  );
}
