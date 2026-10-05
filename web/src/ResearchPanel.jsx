import { useState } from "react";
import { signIn, signedIn } from "./googleAuth";
import { channelReport } from "./youtubeResearch";
import { dispatchWorkflow, readOutputJson } from "./githubDispatch";

/* What another channel's numbers look like from outside.
 *
 * Reading a channel page tells you today's view counts and nothing about
 * the shape underneath them -- whether it broke out once or climbed, which
 * titles travelled, whether the hits are supercars or hot hatches. The
 * Data API answers all three, and the browser is already signed in, so
 * this needs no key and no workflow.
 */
const HISTOGRAM_BUCKETS = [1000, 5000, 20000, 50000, 100000];

function median(numbers) {
  if (!numbers.length) return 0;
  const sorted = [...numbers].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

function Cohorts({ videos }) {
  // Oldest to newest in four groups: a channel that compounded looks
  // different from one that got lucky, and only the trend shows which.
  const byAge = [...videos].sort((a, b) => a.published.localeCompare(b.published));
  const size = Math.max(1, Math.ceil(byAge.length / 4));
  const groups = [0, 1, 2, 3]
    .map((i) => byAge.slice(i * size, (i + 1) * size))
    .filter((group) => group.length);
  const peak = Math.max(...groups.map((g) => median(g.map((v) => v.views))), 1);
  return (
    <table className="research-table">
      <thead><tr><th>quarter</th><th>videos</th><th>median views</th><th /></tr></thead>
      <tbody>
        {groups.map((group, index) => {
          const mid = median(group.map((v) => v.views));
          return (
            <tr key={index}>
              <td>{index === 0 ? "oldest" : index === groups.length - 1 ? "newest" : `${index + 1}${index === 1 ? "nd" : "rd"}`}</td>
              <td>{group.length}</td>
              <td>{mid.toLocaleString()}</td>
              <td><span className="research-bar" style={{ width: `${(mid / peak) * 100}%` }} /></td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/* What the numbers alone could not show.
 *
 * Our own builds run 126-152 words at 2.4-2.8 words a second. Whether that
 * is right has so far been judged against a measurement of our own voice,
 * with nothing outside to compare it to. The medians here are that
 * comparison; the beat columns are how a script is built.
 */
const OURS = { words: 139, rate: 2.6, seconds: 54, beats: 7 };

function Scripts({ rows }) {
  const usable = rows.filter((r) => r.words);
  if (!usable.length) return <p className="yt-note">No captions came back for any of those.</p>;
  const med = (pick) => median(usable.map(pick));
  const cells = [
    ["words", med((r) => r.words), OURS.words],
    ["seconds", med((r) => r.seconds), OURS.seconds],
    ["words / sec", med((r) => r.words_per_second), OURS.rate],
    ["beats", med((r) => r.beats), OURS.beats],
    ["opening words", med((r) => r.opening_words), null],
    ["opening w/s", med((r) => r.opening_rate), null],
  ];
  const withNumber = usable.filter((r) => r.opening_has_number).length;
  const asQuestion = usable.filter((r) => r.closes_on_question).length;

  return (
    <div className="research-scripts">
      <table className="research-table">
        <thead><tr><th /><th>theirs (median)</th><th>ours</th></tr></thead>
        <tbody>
          {cells.map(([name, theirs, ours]) => (
            <tr key={name}>
              <td>{name}</td>
              <td>{theirs}</td>
              <td>{ours === null ? "—" : ours}</td>
            </tr>
          ))}
          <tr><td>opens on a number</td><td>{withNumber}/{usable.length}</td><td>required</td></tr>
          <tr><td>ends on a question</td><td>{asQuestion}/{usable.length}</td><td>required</td></tr>
        </tbody>
      </table>

      {usable.map((row) => (
        <details key={row.video_id} className="research-script">
          <summary>
            {row.words} words · {row.seconds}s · {row.words_per_second} w/s · {row.beats} beats
            {" — "}
            <a href={`https://youtu.be/${row.video_id}`} target="_blank" rel="noreferrer">
              {row.video_id}
            </a>
          </summary>
          <ol className="research-beats">
            {(row.beats_text || []).map((text, i) => (
              <li key={i}>
                <span className="research-beat-meta">
                  {row.beat_words[i]}w · {row.beat_seconds[i]}s · {row.beat_rates[i]} w/s
                </span>
                {text}
              </li>
            ))}
          </ol>
        </details>
      ))}
    </div>
  );
}

export default function ResearchPanel({ settings }) {
  const [handle, setHandle] = useState("");
  const [report, setReport] = useState(null);
  const [state, setState] = useState("idle");
  const [error, setError] = useState(null);
  const [videos, setVideos] = useState("");
  const [label, setLabel] = useState("");
  const [scripts, setScripts] = useState(null);
  const [scriptState, setScriptState] = useState("idle");
  const [scriptError, setScriptError] = useState(null);

  /* Reading a Short's spoken track has to happen on a runner.
   *
   * This page cannot fetch youtube.com -- no CORS header comes back -- and
   * neither can a session, whose egress proxy refuses the tunnel outright.
   * So the button starts a workflow and the answer arrives on the output
   * branch a minute or two later, which is what "Load" then reads.
   */
  async function readScripts() {
    setScriptError(null);
    setScriptState("running");
    try {
      await dispatchWorkflow({
        owner: settings.owner, repo: settings.repo, branch: settings.branch,
        token: settings.token, workflow: "shorts-transcripts.yml",
        inputs: { videos: videos.trim(), label: label.trim() || "batch", limit: "20" },
      });
      setScriptState("started");
    } catch (err) {
      setScriptError(String(err.message || err));
      setScriptState("idle");
    }
  }

  async function loadScripts() {
    setScriptError(null);
    setScriptState("loading");
    try {
      const found = await readOutputJson({
        owner: settings.owner, repo: settings.repo,
        path: `research/shorts-transcripts/${label.trim() || "batch"}/transcripts.json`,
      });
      if (!found) setScriptError("Nothing read under that label yet -- the run may still be going.");
      setScripts(found);
      setScriptState("idle");
    } catch (err) {
      setScriptError(String(err.message || err));
      setScriptState("idle");
    }
  }

  async function look() {
    setError(null);
    setState("loading");
    try {
      setReport(await channelReport(handle.trim()));
      setState("idle");
    } catch (err) {
      setError(String(err.message || err));
      setState("idle");
    }
  }

  const live = signedIn();
  const shouty = report?.videos.filter((v) => v.title === v.title.toUpperCase()) || [];
  const quiet = report?.videos.filter((v) => v.title !== v.title.toUpperCase()) || [];

  return (
    <section className="yt-panel">
      <h2>Channel research</h2>
      <p className="yt-lead">
        Any public channel, read through the API you are already signed in to. A channel page
        shows today's numbers; this shows the shape underneath them.
      </p>

      {!live && (
        <button type="button" className="yt-start"
                onClick={() => signIn().catch((e) => setError(String(e.message || e)))}>
          Sign in with Google
        </button>
      )}

      {live && (
        <div className="field-row">
          <input
            value={handle}
            onChange={(e) => setHandle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handle.trim() && look()}
            placeholder="@Driving_lab"
            disabled={state === "loading"}
          />
          <button type="button" className="yt-start" onClick={look}
                  disabled={!handle.trim() || state === "loading"}>
            {state === "loading" ? "Reading…" : "Look it up"}
          </button>
        </div>
      )}

      {error && <p className="yt-error">{error}</p>}

      <h3>Read their scripts</h3>
      <p className="yt-lead">
        Paste Shorts links, video ids, or a whole channel page&rsquo;s source &mdash; anything with
        ids in it. A runner reads the captions, because neither this page nor a session can
        reach YouTube directly. Give it a label, run it, then Load it a minute later.
      </p>
      <textarea
        className="research-videos"
        rows={4}
        value={videos}
        onChange={(e) => setVideos(e.target.value)}
        placeholder={"https://www.youtube.com/shorts/2dxa9oz1AZw\nhttps://www.youtube.com/shorts/..."}
      />
      <div className="research-row">
        <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="label, e.g. driving-lab" />
        <button type="button" onClick={readScripts} disabled={!videos.trim() || scriptState === "running"}>
          {scriptState === "running" ? "Starting…" : "Read scripts"}
        </button>
        <button type="button" className="secondary" onClick={loadScripts} disabled={scriptState === "loading"}>
          {scriptState === "loading" ? "Loading…" : "Load"}
        </button>
      </div>
      {scriptState === "started" && (
        <p className="yt-note">Started. It takes a minute or two &mdash; then press Load.</p>
      )}
      {scriptError && <p className="yt-error">{scriptError}</p>}
      {scripts?.length > 0 && <Scripts rows={scripts} />}

      {report && (
        <>
          <p className="yt-channel-line">
            <strong>{report.channel.title}</strong>{" — "}
            {report.channel.subscribers.toLocaleString()} subscribers{" · "}
            {report.channel.videos.toLocaleString()} videos{" · "}
            {report.channel.views.toLocaleString()} views
            {report.videos.length < report.channel.videos && (
              <> {" · "}newest {report.videos.length} read</>
            )}
          </p>

          <h3>Did it climb, or get lucky once?</h3>
          <Cohorts videos={report.videos} />

          <h3>Titles</h3>
          <table className="research-table">
            <tbody>
              <tr>
                <td>ALL CAPS</td><td>{shouty.length}</td>
                <td>median {median(shouty.map((v) => v.views)).toLocaleString()}</td>
              </tr>
              <tr>
                <td>sentence case</td><td>{quiet.length}</td>
                <td>median {median(quiet.map((v) => v.views)).toLocaleString()}</td>
              </tr>
            </tbody>
          </table>
          {shouty.length > 0 && quiet.length > 0 && (
            <p className="yt-note">
              Style and age usually move together on a channel like this, so treat the gap as a
              hint rather than a cause.
            </p>
          )}

          <h3>Spread</h3>
          <table className="research-table">
            <tbody>
              {HISTOGRAM_BUCKETS.map((floor, i) => {
                const ceil = HISTOGRAM_BUCKETS[i + 1];
                const hits = report.videos.filter(
                  (v) => v.views >= floor && (!ceil || v.views < ceil)).length;
                return (
                  <tr key={floor}>
                    <td>{floor.toLocaleString()}{ceil ? `–${ceil.toLocaleString()}` : "+"}</td>
                    <td>{hits}</td>
                    <td><span className="research-bar"
                              style={{ width: `${(hits / report.videos.length) * 100}%` }} /></td>
                  </tr>
                );
              })}
              <tr>
                <td>under 1,000</td>
                <td>{report.videos.filter((v) => v.views < 1000).length}</td>
                <td />
              </tr>
            </tbody>
          </table>

          <h3>What travelled</h3>
          <ol className="research-top">
            {[...report.videos].sort((a, b) => b.views - a.views).slice(0, 12).map((v) => (
              <li key={v.id}>
                <a href={`https://youtu.be/${v.id}`} target="_blank" rel="noreferrer">{v.title}</a>
                {" — "}{v.views.toLocaleString()} views
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
