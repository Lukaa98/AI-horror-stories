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

// YouTube's own limits. Enforced here so an over-long title is a disabled
// Save rather than a failed upload after the video has gone across.
const TITLE_LIMIT = 100;
const DESCRIPTION_LIMIT = 5000;

async function readJson(settings, path) {
  const found = await readListing(settings, path);
  return found ? found.listing : null;
}

async function readListing(settings, path) {
  const { owner, repo, token } = settings;
  const res = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/contents/${path}?ref=${encodeURIComponent(OUTPUT_BRANCH)}`,
    { headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json" }, cache: "no-store" }
  );
  if (!res.ok) return null;
  const data = await res.json();
  const bytes = Uint8Array.from(atob(data.content.replace(/\n/g, "")), (c) => c.charCodeAt(0));
  // The blob sha, so a later write can say which version it is replacing --
  // without it GitHub refuses the update rather than clobbering.
  return { listing: JSON.parse(new TextDecoder("utf-8").decode(bytes)), sha: data.sha };
}

async function writeJson(settings, path, sha, value) {
  const { owner, repo, token } = settings;
  const text = JSON.stringify(value, null, 2) + "\n";
  const encoded = btoa(String.fromCharCode(...new TextEncoder().encode(text)));
  const res = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/contents/${path}`,
    {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message: `cars: edit listing for ${path.split("/").slice(-2)[0]}`,
        content: encoded,
        sha,
        branch: OUTPUT_BRANCH,
      }),
    }
  );
  if (!res.ok) throw new Error(`Saving failed (${res.status}): ${await res.text()}`);
  const data = await res.json();
  return data.content.sha;
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
  const [sha, setSha] = useState(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ title: "", description: "", tags: "" });
  const [saving, setSaving] = useState(false);
  // The next 12:15, which is the slot the channel posts in -- today if that
  // has not happened yet, otherwise tomorrow. Prefilled rather than blank so
  // scheduling is one click on the usual day.
  const [publishAt, setPublishAt] = useState(() => {
    const at = new Date();
    at.setHours(12, 15, 0, 0);
    if (at <= new Date()) at.setDate(at.getDate() + 1);
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
    readListing(settings, path)
      .then((found) => {
        if (!live) return;
        setListing(found ? found.listing : null);
        setSha(found ? found.sha : null);
        if (found?.listing?.video_id) setState("done");
        if (found) setDraft({
          title: found.listing.title || "",
          description: found.listing.description || "",
          tags: (found.listing.tags || []).join(", "),
        });
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

  // Edits are written back to upload.json rather than passed to the
  // workflow, so the file stays the one source of what gets published --
  // the dashboard shows the edited text afterwards, and a re-upload sends
  // the same words rather than the ones the build first wrote.
  async function save() {
    setError(null);
    setSaving(true);
    try {
      const tags = draft.tags.split(",").map((t) => t.trim()).filter(Boolean);
      const updated = { ...listing, title: draft.title.trim(),
                        description: draft.description, tags };
      const nextSha = await writeJson(settings, path, sha, updated);
      setListing(updated);
      setSha(nextSha);
      setEditing(false);
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setSaving(false);
    }
  }

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

  async function upload(extraInputs = {}) {
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
            ref: branch || "v12",
            inputs: {
              build_id: buildId,
              ...(scheduled && publishAt ? { publish_at: withLocalOffset(publishAt) } : {}),
              ...extraInputs,
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
      ) : null}
      {state === "done" && listing.video_id ? (
        <>
          {listing.publish_at && (
            <p className="hint">
              <button type="button" className="secondary"
                      onClick={() => {
                        if (window.confirm("Make this public right now, ignoring its schedule?")) {
                          upload({ publish_now: true });
                        }
                      }}>
                Publish now
              </button>{" "}
              Overrides the scheduled time and takes it public immediately.
            </p>
          )}
          <p className="hint">
            <button type="button" className="secondary"
                    onClick={() => upload({ update_metadata: true })}>
              Push the listing to YouTube
            </button>{" "}
            Applies this build's title, description, tags, category, language and AI
            answer to the video that is already up. Edit it first if you want it
            changed -- the listing is what gets pushed.
          </p>
          {editing ? null : (
            <p className="hint">
              <button type="button" className="secondary" onClick={() => setEditing(true)}>
                Edit the listing
              </button>{" "}
              Changing it here does not touch YouTube until you push it.
            </p>
          )}
          {editing && (
            <div className="upload-edit">
              <label>
                <span>Title <em>{draft.title.length}/{TITLE_LIMIT}</em></span>
                <input value={draft.title} maxLength={TITLE_LIMIT}
                       onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
              </label>
              <label>
                <span>Description <em>{draft.description.length}/{DESCRIPTION_LIMIT}</em></span>
                <textarea rows={8} value={draft.description} maxLength={DESCRIPTION_LIMIT}
                          onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
              </label>
              <label>
                <span>Tags <em>comma separated</em></span>
                <input value={draft.tags}
                       onChange={(e) => setDraft({ ...draft, tags: e.target.value })} />
              </label>
              <div className="upload-edit-actions">
                <button type="button" className="upload-go" onClick={save}
                        disabled={saving || !draft.title.trim()}>
                  {saving ? "Saving…" : "Save"}
                </button>
                <button type="button" className="secondary" disabled={saving}
                        onClick={() => setEditing(false)}>
                  Cancel
                </button>
              </div>
            </div>
          )}
          {listing.thumbnail_set === false ? (
          <p className="hint">
            <button type="button" className="secondary"
                    onClick={() => upload({ thumbnail_only: true })}>
              Set the thumbnail now
            </button>{" "}
            Verify the channel first, then this attaches the build's own thumbnail without
            uploading the video again.
            </p>
          ) : null}
        </>
      ) : (
        <>
          {editing ? (
            <div className="upload-edit">
              <label>
                <span>Title <em>{draft.title.length}/{TITLE_LIMIT}</em></span>
                <input value={draft.title} maxLength={TITLE_LIMIT}
                       onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
              </label>
              <label>
                <span>Description <em>{draft.description.length}/{DESCRIPTION_LIMIT}</em></span>
                <textarea rows={10} value={draft.description} maxLength={DESCRIPTION_LIMIT}
                          onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
              </label>
              <label>
                <span>Tags <em>comma separated</em></span>
                <input value={draft.tags}
                       onChange={(e) => setDraft({ ...draft, tags: e.target.value })} />
              </label>
              <div className="upload-edit-actions">
                <button type="button" className="upload-go" onClick={save}
                        disabled={saving || !draft.title.trim()}>
                  {saving ? "Saving…" : "Save"}
                </button>
                <button type="button" className="secondary" disabled={saving}
                        onClick={() => {
                          setDraft({
                            title: listing.title || "",
                            description: listing.description || "",
                            tags: (listing.tags || []).join(", "),
                          });
                          setEditing(false);
                        }}>
                  Cancel
                </button>
              </div>
            </div>
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
              <button type="button" className="secondary upload-edit-open"
                      onClick={() => setEditing(true)}
                      disabled={state === "sending" || state === "watching"}>
                Edit title & description
              </button>
            </>
          )}
          {!editing && <label className="upload-schedule">
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
          </label>}
          {!editing && scheduled && (
            <p className="hint">
              YouTube makes it public at that moment, in this computer's timezone.
              It stays private until then.
            </p>
          )}
          <button type="button" className="upload-go" onClick={upload}
                  disabled={!ready || editing || state === "sending" || state === "watching"}>
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
