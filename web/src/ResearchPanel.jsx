import { useEffect, useState } from "react";
import { completeSignIn, signIn, signedIn } from "./googleAuth";
import { channelReport } from "./youtubeResearch";
import { fromPaste } from "./transcriptPaste";

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
  const skipped = rows.filter((r) => !r.words);
  const skips = skipped.length ? (
    <ul className="research-skips">
      {skipped.map((row) => (
        <li key={row.video_id}>
          <code>{row.video_id}</code> — {row.skipped || "no captions"}
        </li>
      ))}
    </ul>
  ) : null;
  if (!usable.length) {
    return (
      <div className="research-scripts">
        <p className="yt-note">No captions came back for any of those. Why:</p>
        {skips}
      </div>
    );
  }
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
      {skips}
    </div>
  );
}

export default function ResearchPanel() {
  const [handle, setHandle] = useState("");
  const [report, setReport] = useState(null);
  const [state, setState] = useState("idle");
  const [error, setError] = useState(null);
  const [paste, setPaste] = useState("");
  const [pasteLabel, setPasteLabel] = useState("");
  const [scripts, setScripts] = useState([]);
  const [scriptError, setScriptError] = useState(null);

  /* Reading these on a runner is not possible. YouTube answers a datacenter
   * IP with playability LOGIN_REQUIRED and hands over no player at all, so
   * the workflow that tried it is gone. This browser is the one machine
   * that can see them -- signed in, on a home address -- which makes the
   * copying manual and leaves only the analysis automatic.
   */
  function addPaste() {
    setScriptError(null);
    const shape = fromPaste(paste, pasteLabel);
    if (shape.skipped) {
      setScriptError(`${shape.skipped} -- the panel copies as a time, then its line.`);
      return;
    }
    setScripts((previous) => [...previous.filter((r) => r.video_id !== shape.video_id), shape]);
    setPaste("");
    setPasteLabel("");
  }

  const [live, setLive] = useState(() => signedIn());

  /* Google comes back with the token in the address bar, and somebody has
   * to take it out of there. Only the YouTube panel did, so signing in from
   * this tab could never work: the grant arrived, nothing read it, and the
   * button came straight back -- looking exactly like a sign-in that had
   * not happened. The two panels are never mounted at once, so whichever
   * one the redirect lands on handles it.
   */
  useEffect(() => {
    let onPage = true;
    completeSignIn()
      .then((token) => { if (onPage && token) { setLive(true); setError(null); } })
      .catch((err) => { if (onPage) setError(String(err.message || err)); });
    return () => { onPage = false; };
  }, []);

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
        <>
          <button type="button" className="yt-start"
                  onClick={() => signIn().catch((e) => setError(String(e.message || e)))}>
            Sign in with Google
          </button>
          <p className="yt-note">
            A Google sign-in here lasts about an hour and cannot be refreshed &mdash; the page
            holds no secret, so there is no refresh token. Seeing this again usually means the
            last one simply aged out.
          </p>
        </>
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
        Open a Short, <strong>&hellip; &rarr; Show transcript</strong>, copy the whole thing and
        paste it here. YouTube will not serve these to a server &mdash; it answers a datacenter
        address with <code>LOGIN_REQUIRED</code> &mdash; so this browser, signed in and at home,
        is the only thing that can see them. Add as many as you like; they stack up.
      </p>
      <textarea
        className="research-videos"
        rows={6}
        value={paste}
        onChange={(e) => setPaste(e.target.value)}
        placeholder={"0:00\nfour hundred horsepower and nobody\n0:03\nknows it came from a factory"}
      />
      <div className="research-row">
        <input value={pasteLabel} onChange={(e) => setPasteLabel(e.target.value)}
               placeholder="its link or id (optional)" />
        <button type="button" onClick={addPaste} disabled={!paste.trim()}>Add it</button>
        {scripts.length > 0 && (
          <button type="button" className="secondary" onClick={() => setScripts([])}>
            Clear {scripts.length}
          </button>
        )}
      </div>
      {scriptError && <p className="yt-error">{scriptError}</p>}
      {scripts.length > 0 && <Scripts rows={scripts} />}

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
