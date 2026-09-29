import { useState } from "react";
import { signIn, signedIn } from "./googleAuth";
import { channelReport } from "./youtubeResearch";

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

export default function ResearchPanel() {
  const [handle, setHandle] = useState("");
  const [report, setReport] = useState(null);
  const [state, setState] = useState("idle");
  const [error, setError] = useState(null);

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
