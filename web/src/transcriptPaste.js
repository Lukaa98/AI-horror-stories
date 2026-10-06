/* A transcript pasted out of YouTube's own panel.
 *
 * Fetching these on a runner is not possible: YouTube answers a datacenter
 * IP with playability LOGIN_REQUIRED and hands over no player at all. The
 * browser you are reading this in is the one machine that can see them --
 * signed in, on a home address -- so the copying is manual and only the
 * analysis is automatic.
 *
 * The panel copies as a start time and then its line, either on two lines
 * or on one. There are no end times, so a cue runs until the next one
 * starts; the last is given the median of the others, which is better than
 * zero and honest about being a guess.
 */

// Matching the Python in youtube_tools/shorts_transcript.py, which is what
// a run from a home machine would use.
const BEAT_GAP_SECONDS = 0.45;
const STAMP = /^(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.\d+)?$/;
const LEADING_STAMP = /^(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.\d+)?\s+(.*)$/;

function seconds(hours, minutes, secs) {
  return Number(hours || 0) * 3600 + Number(minutes) * 60 + Number(secs);
}

function median(numbers) {
  if (!numbers.length) return 0;
  const sorted = [...numbers].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

export function parseTranscript(text) {
  const lines = String(text || "").split("\n").map((l) => l.trim()).filter(Boolean);
  const cues = [];
  let pending = null;
  for (const line of lines) {
    const alone = line.match(STAMP);
    if (alone) {
      // A timestamp on its own: the words are on the next line.
      pending = seconds(alone[1], alone[2], alone[3]);
      continue;
    }
    const inline = line.match(LEADING_STAMP);
    if (inline) {
      cues.push({ start: seconds(inline[1], inline[2], inline[3]), text: inline[4] });
      pending = null;
      continue;
    }
    if (pending !== null) {
      cues.push({ start: pending, text: line });
      pending = null;
    } else if (cues.length) {
      // A wrapped line with no stamp belongs to the cue above it.
      cues[cues.length - 1].text += " " + line;
    }
  }
  // No end times come out of the panel, so a cue lasts until the next.
  const gaps = cues.slice(1).map((cue, i) => cue.start - cues[i].start).filter((g) => g > 0);
  const typical = median(gaps) || 2;
  return cues.map((cue, i) => ({
    text: cue.text.replace(/\s+/g, " ").trim(),
    start: cue.start,
    duration: i + 1 < cues.length ? Math.max(0.1, cues[i + 1].start - cue.start) : typical,
  })).filter((cue) => cue.text);
}

/* Pasted cues run continuously -- each one ends exactly where the next
 * starts -- so there are no pauses to group on, and pause-grouping put a
 * whole script in one beat. The timed-text the Python reads has real gaps;
 * this does not.
 *
 * What it does have is punctuation, because the tracks worth reading are
 * written or well-punctuated ASR. So beats are sentences here, timed by
 * where their words fall across the cues.
 */
export function beats(cues) {
  // One entry per word: when it was said, and which cue it came from.
  const words = [];
  for (const cue of cues) {
    const parts = cue.text.split(/\s+/).filter(Boolean);
    parts.forEach((word, i) => {
      words.push({ word, at: cue.start + (cue.duration * i) / Math.max(1, parts.length) });
    });
  }
  if (!words.length) return [];

  const grouped = [];
  let current = [];
  for (let i = 0; i < words.length; i += 1) {
    current.push(words[i]);
    const ends = /[.!?]["')\]]?$/.test(words[i].word);
    if (ends || i === words.length - 1) {
      const last = cues[cues.length - 1];
      const end = i + 1 < words.length ? words[i + 1].at : last.start + last.duration;
      grouped.push({ text: current.map((w) => w.word).join(" "), start: current[0].at, end });
      current = [];
    }
  }
  return grouped.map((beat) => {
    const count = beat.text.split(/\s+/).filter(Boolean).length;
    const secs = Math.round((beat.end - beat.start) * 100) / 100;
    return { ...beat, words: count, seconds: secs, rate: secs ? Math.round((count / secs) * 100) / 100 : 0 };
  });
}

export function summarise(videoId, cues) {
  if (!cues.length) return { video_id: videoId, skipped: "nothing parsed out of that paste" };
  const grouped = beats(cues);
  const spoken = cues.map((c) => c.text).join(" ");
  const words = spoken.split(/\s+/).filter(Boolean).length;
  const last = cues[cues.length - 1];
  const span = last.start + last.duration - cues[0].start;
  const round2 = (n) => Math.round(n * 100) / 100;
  return {
    video_id: videoId,
    words,
    seconds: round2(span),
    words_per_second: span ? round2(words / span) : 0,
    beats: grouped.length,
    beat_words: grouped.map((b) => b.words),
    beat_seconds: grouped.map((b) => b.seconds),
    beat_rates: grouped.map((b) => b.rate),
    beats_text: grouped.map((b) => b.text),
    // The opening is the whole retention decision, so it is kept out of the
    // average rather than buried in it.
    opening_words: grouped[0].words,
    opening_rate: grouped[0].rate,
    opening_has_number: /\d/.test(grouped[0].text)
      || /\b(one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand)\b/i.test(grouped[0].text),
    closes_on_question: /\?\s*$/.test(spoken.trim()),
  };
}

/** One pasted transcript, with an optional URL or id to label it. */
export function fromPaste(text, label) {
  const id = (String(label || "").match(/(?:shorts\/|watch\?v=)([A-Za-z0-9_-]{11})/)
    || String(label || "").match(/^([A-Za-z0-9_-]{11})$/) || [])[1] || (label || "pasted");
  return summarise(id, parseTranscript(text));
}
