import { useCallback, useEffect, useRef, useState } from "react";
import "./YouTubePanel.css";
import { completeSignIn, signIn, signOut, signedIn } from "./googleAuth";
import {
  collect as collectLive, deleteVideo, publishNow, setPublishTime, unschedule as unscheduleLive,
} from "./youtubeLive";

const AUTH_WORKFLOW = "youtube-auth.yml";
const STATUS_WORKFLOW = "youtube-status.yml";
const STATUS_PATH = "youtube/channel.json";
const UPLOAD_WORKFLOW = "youtube-upload.yml";

// A datetime-local value carries no offset, and the runner is in UTC.
function withLocalOffset(value) {
  if (!value) return "";
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "";
  const minutes = -at.getTimezoneOffset();
  const sign = minutes < 0 ? "-" : "+";
  const pad = (n) => String(Math.floor(Math.abs(n))).padStart(2, "0");
  return `${value}:00${sign}${pad(minutes / 60)}:${pad(minutes % 60)}`;
}
const CODE_PATH = "youtube/auth-code.json";
const OUTPUT_BRANCH = "cars-output";
const POLL_MS = 4000;
// Google gives a device code half an hour; the workflow gives up at 32
// minutes. Past that there is nothing left to wait for.
const GIVE_UP_MS = 33 * 60 * 1000;

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

// Only the workflow's snapshot knows which build made each video -- the
// YouTube API has never heard of builds, and the link lives in each build's
// upload.json on the output branch. A browser snapshot would therefore drop
// every action button, so the ids from the last workflow snapshot are
// carried across by video id instead.
function carryBuildIds(fresh, previous) {
  const known = new Map(
    (previous?.videos || []).filter((v) => v.build_id).map((v) => [v.id, v.build_id])
  );
  if (!known.size) return fresh;
  return {
    ...fresh,
    videos: fresh.videos.map((video) =>
      video.build_id ? video : { ...video, build_id: known.get(video.id) || "" }
    ),
  };
}

const DAY_MS = 86400000;

// PT1M34S -> 1:34. Shorts are always under a minute, but the channel will
// not always be only Shorts.
function readDuration(iso) {
  const match = /^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/.exec(String(iso || ""));
  if (!match) return "";
  const [h, m, sec] = [Number(match[1] || 0), Number(match[2] || 0), Number(match[3] || 0)];
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

// What the person actually wants to know about a video, in one phrase:
// is it out, is it coming, or is it sitting there.
function describeVideo(video) {
  if (video.privacy === "public") return { tone: "live", text: "Public" };
  if (video.publish_at) {
    const when = new Date(video.publish_at);
    const soon = when.getTime() - Date.now();
    const label = when.toLocaleString(undefined,
      { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    return {
      tone: soon > 0 ? "scheduled" : "live",
      text: soon > 0 ? `Scheduled ${label}` : `Was due ${label}`,
    };
  }
  if (video.privacy === "unlisted") return { tone: "scheduled", text: "Unlisted" };
  return { tone: "private", text: "Private" };
}

// What the stored expiry means, in the terms the person cares about: is it
// still good, and for how long. 0 means the OAuth app was published and the
// token does not expire at all.
function describeToken(info) {
  if (!info) return null;
  const expiresAt = Number(info.token_expires_at || 0);
  if (!expiresAt) return { tone: "ok", text: "Token does not expire." };
  const when = new Date(expiresAt * 1000);
  // Kept fractional: flooring turned a token issued seven days ago-to-the-
  // second into "6 days left", which is both wrong and alarming.
  const remaining = (expiresAt * 1000 - Date.now()) / DAY_MS;
  const days = Math.round(remaining);
  const date = when.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  const time = when.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (remaining < 0) return { tone: "dead", text: `Token expired on ${date}. Uploads will fail until you re-authorise.` };
  if (remaining < 1) return { tone: "soon", text: `Token expires today, ${time}. Re-authorise now.` };
  if (remaining <= 2) return { tone: "soon", text: `Token works until ${date}, ${time} — ${days} day${days === 1 ? "" : "s"} left.` };
  return { tone: "ok", text: `Token works until ${date}, ${time} — ${days} days left.` };
}

// The channel's OAuth app is in Testing, which caps a refresh token at seven
// days. This is the weekly renewal, reduced to reading a code off the screen:
// the workflow publishes it here, the approval happens on Google, and the new
// token is written into Actions secrets without ever passing through here.
export default function YouTubePanel({ settings }) {
  const [status, setStatus] = useState(null);
  const [statusState, setStatusState] = useState("idle");
  const [live, setLive] = useState(() => signedIn());
  const statusTimer = useRef(null);
  const [acting, setActing] = useState("");
  const [state, setState] = useState("idle");   // idle | starting | waiting | stored | error
  const [code, setCode] = useState(null);
  const [error, setError] = useState(null);
  const [tokenInfo, setTokenInfo] = useState(null);
  const startedAt = useRef(0);
  // A ref, not state: setInterval captures the callback once, so a state
  // read here would be the value from the first tick forever.
  const sawCode = useRef(false);
  const timer = useRef(null);

  const ready = Boolean(settings.token && settings.owner && settings.repo);

  const stop = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
  }, []);

  useEffect(() => stop, [stop]);

  // Google sends the browser back here with a ?code=. Swapping it for a
  // token has to happen before anything reads the address bar, and exactly
  // once -- the code is spent on use.
  useEffect(() => {
    let livePage = true;
    completeSignIn()
      .then((token) => {
        if (!livePage || !token) return;
        setLive(true);
        setError(null);
      })
      .catch((err) => { if (livePage) setError(String(err.message || err)); });
    return () => { livePage = false; };
  }, []);

  // Read on mount so the expiry survives a refresh or a tab switch. The
  // token lives seven days while the OAuth app is in Testing and nothing
  // announces its death -- an upload just starts failing -- so the date is
  // the only warning there is.
  useEffect(() => {
    if (!ready) return;
    let live = true;
    readJson(settings, CODE_PATH)
      .then((payload) => {
        if (live && payload?.status === "stored") setTokenInfo(payload);
      })
      .catch(() => {});
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, settings.owner, settings.repo, settings.token]);

  const poll = useCallback(async () => {
    try {
      const payload = await readJson(settings, CODE_PATH);
      if (payload?.status === "stored") {
        stop();
        setCode(null);
        setTokenInfo(payload);
        setState("stored");
        return;
      }
      // A code from an earlier run is still sitting on the branch until the
      // new one lands, so anything older than this click is ignored.
      const fresh = payload?.status === "waiting"
        && Number(payload.expires_at || 0) * 1000 > startedAt.current;
      if (fresh) {
        sawCode.current = true;
        setCode(payload);
        return;
      }
      // The workflow clears the code when it fails. Without noticing that,
      // run #1's crash left this spinning for its full 33 minutes with a
      // dead code on screen -- the one failure mode a person cannot debug
      // from here.
      if (sawCode.current && payload?.status !== "stored") {
        stop();
        setState("error");
        setError("The run ended before the code was approved. Check the Actions log, then try again.");
        return;
      }
      if (Date.now() - startedAt.current > GIVE_UP_MS) {
        stop();
        setState("error");
        setError("The code expired before it was approved. Start again.");
      }
    } catch (err) {
      stop();
      setState("error");
      setError(String(err.message || err));
    }
  }, [settings, stop]);

  async function start() {
    setError(null);
    setCode(null);
    setState("starting");
    startedAt.current = Date.now();
    sawCode.current = false;
    try {
      const { owner, repo, branch, token } = settings;
      const res = await fetch(
        `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${AUTH_WORKFLOW}/dispatches`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: "application/vnd.github+json",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ ref: branch || "v10" }),
        }
      );
      if (!res.ok) throw new Error(`Dispatch failed (${res.status}): ${await res.text()}`);
      setState("waiting");
      stop();
      timer.current = setInterval(poll, POLL_MS);
    } catch (err) {
      setState("error");
      setError(String(err.message || err));
    }
  }

  useEffect(() => {
    if (!ready) return undefined;
    let live = true;
    readJson(settings, STATUS_PATH)
      .then((found) => { if (live && found) setStatus(found); })
      .catch(() => {});
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, settings.owner, settings.repo, settings.token]);

  // Every action on a video is addressed by the build that made it, which
  // is why the snapshot carries build_id. Without one there is nothing to
  // dispatch against, so the buttons are not offered.
  const act = useCallback(async (video, inputs, label) => {
    const { owner, repo, branch, token } = settings;
    setActing(`${video.id}:${label}`);
    try {
      // Signed in, this is one call to YouTube and the row is right again
      // before the button finishes animating. The workflow does exactly the
      // same thing; it just has to be dispatched, queued and checked out
      // first. The build id is what the workflow needs, not YouTube, so a
      // video with no build behind it can still be acted on here.
      if (signedIn()) {
        if (inputs.publish_now) await publishNow(video.id);
        else if (inputs.unschedule) await unscheduleLive(video.id);
        else if (inputs.reschedule) await setPublishTime(video.id, inputs.publish_at);
        else if (inputs.delete_video) await deleteVideo(video.id);
        else throw new Error(`${label} has no direct equivalent.`);
        await refreshStatus();
        return;
      }
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
            inputs: { build_id: video.build_id, ...inputs },
          }),
        }
      );
      if (!res.ok) throw new Error(`${label} failed (${res.status}): ${await res.text()}`);
      // The channel is re-read rather than the row being edited in place,
      // so what is shown is what YouTube says, not what we hoped.
      setTimeout(() => refreshStatus(), 8000);
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setActing("");
    }
    // refreshStatus is declared below; it is stable enough for this and
    // naming it here would be a use-before-declaration.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings]);

  // Signed in, the browser can ask YouTube itself and the answer is back in
  // a moment. Signed out there is no credential here, so the question has
  // to be asked where the refresh token lives: dispatch the workflow and
  // watch the output branch for a newer snapshot.
  const refreshStatus = useCallback(async () => {
    const { owner, repo, branch, token } = settings;
    const before = status?.checked_at || "";
    setStatusState("refreshing");

    if (signedIn()) {
      try {
        setStatus(carryBuildIds(await collectLive(), status));
        setStatusState("idle");
        setError(null);
        return;
      } catch (err) {
        // An expired sign-in is not a dead end -- the workflow still works.
        setLive(signedIn());
        setError(String(err.message || err));
      }
    }

    try {
      const res = await fetch(
        `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${STATUS_WORKFLOW}/dispatches`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: "application/vnd.github+json",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ ref: branch || "v12", inputs: {} }),
        }
      );
      if (!res.ok) throw new Error(`Dispatch failed (${res.status}): ${await res.text()}`);
      const startedAt = Date.now();
      if (statusTimer.current) clearInterval(statusTimer.current);
      statusTimer.current = setInterval(async () => {
        const found = await readJson(settings, STATUS_PATH).catch(() => null);
        if (found && found.checked_at !== before) {
          clearInterval(statusTimer.current);
          statusTimer.current = null;
          setStatus(found);
          setStatusState("idle");
        } else if (Date.now() - startedAt > 4 * 60 * 1000) {
          clearInterval(statusTimer.current);
          statusTimer.current = null;
          setStatusState("idle");
        }
      }, POLL_MS);
    } catch (err) {
      setStatusState("idle");
      setError(String(err.message || err));
    }
  }, [settings, status]);

  useEffect(() => () => { if (statusTimer.current) clearInterval(statusTimer.current); }, []);

  return (
    <section className="yt-panel">
      <h2>YouTube</h2>
      <p className="yt-lead">
        The channel's OAuth app is in Testing, so its token lasts seven days. Renew it here —
        no terminal, and the token itself never reaches this page.
      </p>

      <div className="yt-signin">
        {live ? (
          <>
            <span className="yt-live">Signed in — reading YouTube directly.</span>
            <button type="button" className="secondary"
                    onClick={() => { signOut(); setLive(false); }}>Sign out</button>
          </>
        ) : (
          <button type="button" className="yt-start"
                  onClick={() => signIn().catch((err) => setError(String(err.message || err)))}>
            Sign in with Google
          </button>
        )}
      </div>
      <p className="yt-note">
        Signing in makes this page ask YouTube itself, so the tab answers in a moment instead of
        waiting on a workflow. It lasts about an hour and covers only what you do here — the
        pipeline keeps using its own token to upload on schedule.
      </p>

      <button type="button" className="yt-start" onClick={start}
              disabled={!ready || state === "starting" || state === "waiting"}>
        {state === "waiting" ? "Waiting for approval…" : "Re-authorise YouTube"}
      </button>
      {!ready && <p className="yt-warn">Set the repo owner, name and token in Settings first.</p>}

      {state === "waiting" && !code && (
        <p className="yt-step">Starting the workflow and asking Google for a code…</p>
      )}

      {code && (
        <div className="yt-code-box">
          <p className="yt-step">1. Open <a href={code.verification_url} target="_blank" rel="noreferrer">
            {code.verification_url}</a> and sign in as the channel.</p>
          <p className="yt-step">2. Enter this code:</p>
          <p className="yt-code">{code.user_code}</p>
          <p className="yt-note">
            You'll see an "unverified app" warning — that's expected. Choose Advanced, then continue.
          </p>
        </div>
      )}

      {state === "stored" && (
        <p className="yt-ok">Done. A fresh token is in GitHub Secrets.</p>
      )}

      {(() => {
        const described = describeToken(tokenInfo);
        if (!described) return null;
        return <p className={`yt-token yt-token-${described.tone}`}>{described.text}</p>;
      })()}
      {state === "error" && <p className="yt-error">{error}</p>}

      <div className="yt-videos">
        <div className="yt-videos-head">
          <h3>Videos</h3>
          <button type="button" className="secondary" onClick={refreshStatus}
                  disabled={!ready || statusState === "refreshing"}>
            {statusState === "refreshing" ? "Asking YouTube…" : "Refresh from YouTube"}
          </button>
        </div>

        {!status && statusState !== "refreshing" && (
          <p className="yt-note">
            No snapshot yet. Press refresh — signed in, the page asks YouTube directly;
            otherwise the workflow is dispatched and its answer read back.
          </p>
        )}

        {status?.channel && (
          <p className="yt-channel-line">
            <strong>{status.channel.title}</strong>
            {" — "}
            {status.channel.hidden_subscribers
              ? "subscribers hidden"
              : `${status.channel.subscribers.toLocaleString()} subscribers`}
            {" · "}{status.channel.views.toLocaleString()} views
            {" · "}{status.channel.videos.toLocaleString()} videos
            {status.checked_at && (
              <span className="yt-note"> (read {new Date(status.checked_at).toLocaleString()})</span>
            )}
          </p>
        )}

        {(status?.videos || []).map((video) => {
          const described = describeVideo(video);
          return (
            <div className="yt-video" key={video.id}>
              {video.thumbnail && <img src={video.thumbnail} alt="" />}
              <div className="yt-video-body">
                <a href={`https://youtu.be/${video.id}`} target="_blank" rel="noreferrer">
                  {video.title}
                </a>
                <div className="yt-video-meta">
                  <span className={`yt-badge yt-badge-${described.tone}`}>{described.text}</span>
                  {readDuration(video.duration) && <span>{readDuration(video.duration)}</span>}
                  <span>{video.views.toLocaleString()} views</span>
                  <span>{video.likes.toLocaleString()} likes</span>
                  <span>{video.comments.toLocaleString()} comments</span>
                </div>
                {video.build_id || live ? (
                  <div className="yt-video-actions">
                    {video.privacy !== "public" && (
                      <button type="button" className="secondary"
                              disabled={Boolean(acting)}
                              onClick={() => {
                                if (window.confirm("Make this public right now?")) {
                                  act(video, { publish_now: "true" }, "publish");
                                }
                              }}>
                        Publish now
                      </button>
                    )}
                    {video.publish_at && (
                      <button type="button" className="secondary"
                              disabled={Boolean(acting)}
                              onClick={() => act(video, { unschedule: "true" }, "unschedule")}>
                        Cancel schedule
                      </button>
                    )}
                    <button type="button" className="secondary"
                            disabled={Boolean(acting)}
                            onClick={() => {
                              const when = window.prompt(
                                "New publish time, in this computer's timezone (YYYY-MM-DDTHH:MM)",
                                video.publish_at
                                  ? new Date(video.publish_at).toISOString().slice(0, 16)
                                  : "");
                              const stamped = withLocalOffset(when);
                              if (stamped) {
                                act(video, { reschedule: "true", publish_at: stamped }, "reschedule");
                              }
                            }}>
                      Reschedule
                    </button>
                    <button type="button" className="danger"
                            disabled={Boolean(acting)}
                            onClick={() => {
                              if (window.confirm(
                                `Delete "${video.title}" from YouTube? There is no undo. `
                                + "The build keeps its files and can be uploaded again.")) {
                                act(video, { delete_video: "true" }, "delete");
                              }
                            }}>
                      Delete
                    </button>
                    {acting.startsWith(`${video.id}:`) && (
                      <span className="yt-note">{acting.split(":")[1]}…</span>
                    )}
                  </div>
                ) : (
                  <p className="yt-note">
                    No build on this branch made this video, so the workflow has nothing to act
                    on. Sign in above and these work anyway — YouTube is addressed by video id.
                  </p>
                )}
                <div className="yt-video-meta yt-video-settings">
                  <span className={video.category === "Autos & Vehicles" ? "" : "yt-flag"}>
                    {video.category || "no category"}
                  </span>
                  <span className={video.language ? "" : "yt-flag"}>
                    {video.language || "no language"}
                  </span>
                  <span className={video.synthetic ? "yt-flag" : ""}>
                    {video.synthetic ? "AI label on" : "no AI label"}
                  </span>
                  {video.upload_status && video.upload_status !== "processed" && (
                    <span className="yt-flag">{video.upload_status}</span>
                  )}
                  {video.made_for_kids && <span className="yt-flag">made for kids</span>}
                  {video.described === false && <span className="yt-flag">no description</span>}
                  {typeof video.tags === "number" && (
                    <span>{video.tags} tags</span>
                  )}
                  {video.embeddable === false && <span>not embeddable</span>}
                </div>
                {video.problem && (
                  <p className="yt-video-problem">YouTube rejected this: {video.problem}</p>
                )}
                {(video.warnings || []).length > 0 && (
                  <p className="yt-video-problem">
                    {video.warnings.join(", ").replace(/([A-Z])/g, " $1").toLowerCase()}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
