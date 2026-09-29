import { useEffect, useRef, useState } from "react";

/* Hearing the squeeze before committing to it.
 *
 * The pipeline fits more words into a shorter video by compressing the
 * finished take with ffmpeg's atempo, which changes tempo and leaves pitch
 * alone. An audio element does the same thing: playbackRate with
 * preservesPitch is the same operation, so this is a real preview rather
 * than an approximation of one.
 *
 * It plays narration.mp3 rather than the video on purpose. Speeding the
 * video up speeds the pictures too, which is not what the pipeline does --
 * it shortens the audio and builds the video to match.
 */
const RATES = [1.0, 1.09, 1.2, 1.3];

export default function TempoTester({ src, seconds }) {
  const audio = useRef(null);
  const [rate, setRate] = useState(1.0);
  const [supported, setSupported] = useState(true);

  useEffect(() => {
    const el = audio.current;
    if (!el) return;
    // Safari spells it with a prefix, and an older engine may not have it
    // at all -- in which case this changes pitch too and is worth saying.
    if ("preservesPitch" in el) el.preservesPitch = true;
    else if ("webkitPreservesPitch" in el) el.webkitPreservesPitch = true;
    else setSupported(false);
    el.playbackRate = rate;
  }, [rate]);

  return (
    <div className="tempo">
      <audio ref={audio} controls src={src} preload="metadata" />
      <div className="tempo-rates">
        {RATES.map((value) => (
          <button
            key={value}
            type="button"
            className={`secondary${value === rate ? " on" : ""}`}
            onClick={() => setRate(value)}
          >
            {value === 1 ? "As recorded" : `${value}×`}
            {seconds ? <span className="tempo-seconds">{(seconds / value).toFixed(1)}s</span> : null}
          </button>
        ))}
      </div>
      <p className="yt-note">
        {supported
          ? "Tempo only — the pitch does not move, same as the pipeline's own squeeze. "
            + "Whichever is still comfortable is what the word budget should be built around."
          : "This browser cannot change tempo without moving pitch, so these will sound wrong "
            + "in a way the pipeline would not."}
      </p>
    </div>
  );
}
