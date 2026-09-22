import { useCallback, useEffect, useRef, useState } from "react";
import "./YouTubePanel.css";

const AUTH_WORKFLOW = "youtube-auth.yml";
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

// The channel's OAuth app is in Testing, which caps a refresh token at seven
// days. This is the weekly renewal, reduced to reading a code off the screen:
// the workflow publishes it here, the approval happens on Google, and the new
// token is written into Actions secrets without ever passing through here.
export default function YouTubePanel({ settings }) {
  const [state, setState] = useState("idle");   // idle | starting | waiting | stored | error
  const [code, setCode] = useState(null);
  const [error, setError] = useState(null);
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

  const poll = useCallback(async () => {
    try {
      const payload = await readJson(settings, CODE_PATH);
      if (payload?.status === "stored") {
        stop();
        setCode(null);
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

  return (
    <section className="yt-panel">
      <h2>YouTube</h2>
      <p className="yt-lead">
        The channel's OAuth app is in Testing, so its token lasts seven days. Renew it here —
        no terminal, and the token itself never reaches this page.
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
        <p className="yt-ok">Done. A fresh token is in GitHub Secrets and uploads will work for another 7 days.</p>
      )}
      {state === "error" && <p className="yt-error">{error}</p>}
    </section>
  );
}
