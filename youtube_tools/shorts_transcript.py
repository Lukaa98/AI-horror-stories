"""Read a Short's spoken track, so a script's shape can be compared to ours.

Word counts and durations we can already guess at. What they do not show is
the thing worth learning: where the hook ends, what the second beat is, how
often the car is named, what the last line does. That only comes from the
words in order.

This runs in Actions rather than in a session or the dashboard. The session's
egress proxy refuses to tunnel to youtube.com at all (ERR_TUNNEL_CONNECTION_
FAILED, with or without a browser), and a page on github.io cannot fetch
youtube.com because YouTube sends no CORS header. The runner has neither
problem.

Captions are fetched for our own analysis -- rhythm, beat order, how a hook
is built -- and the text is kept, because the shape of a script is not
visible from word counts alone. It is reference material for working out
what to write, not something to republish.

Fragile on purpose-built ground: the player response is an internal shape
that YouTube changes when it likes, and some videos carry no caption track
at all. Every failure here is a skipped video, never a crashed run.
"""
import json
import re
import urllib.request

PLAYER_RESPONSE_RE = re.compile(r"ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;\s*(?:var|</script>)", re.S)
# Auto-captions carry no punctuation, so sentences are not recoverable from
# them. Pauses are: a gap this long between cues is where a beat ended.
BEAT_GAP_SECONDS = 0.45
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


def _get(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def video_ids_from_text(text):
    """Every Shorts/watch id in a blob of pasted page source or URLs."""
    found = re.findall(r"(?:shorts/|watch\?v=|\"videoId\":\")([A-Za-z0-9_-]{11})", text)
    seen, ids = set(), []
    for value in found:
        if value not in seen:
            seen.add(value)
            ids.append(value)
    return ids


def caption_track(video_id):
    """(url, why) -- the timed-text URL, and what happened either way.

    "No captions" is three different problems wearing one face: the video
    genuinely has none, YouTube changed the shape of the page, or a
    datacenter IP got a consent wall instead of a watch page. The first
    needs nothing, the second needs a new regex, the third needs cookies.
    Returning the reason is what makes them tellable apart from a log.
    """
    try:
        page = _get(f"https://www.youtube.com/watch?v={video_id}")
    except OSError as exc:
        # URLError and HTTPError are both OSError, but a bare socket timeout
        # or DNS failure is not a URLError -- catching only those would kill
        # a twenty-video run on the first flaky request.
        return None, f"fetch failed: {str(exc)[:80]}"
    match = PLAYER_RESPONSE_RE.search(page)
    if not match:
        if "consent.youtube.com" in page or "CONSENT" in page[:4000]:
            return None, f"consent wall, not a watch page ({len(page)} bytes)"
        if len(page) < 20000:
            return None, f"page too small to be a watch page ({len(page)} bytes)"
        return None, f"no ytInitialPlayerResponse in {len(page)} bytes"
    try:
        player = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None, "player response is not valid JSON"
    status = (player.get("playabilityStatus") or {}).get("status")
    tracks = (player.get("captions", {})
              .get("playerCaptionsTracklistRenderer", {})
              .get("captionTracks") or [])
    if not tracks:
        return None, f"no caption track on this video (playability: {status})"
    # An uploader's own track says what they meant; ASR is a guess at it.
    chosen = next((t for t in tracks if t.get("kind") != "asr"), tracks[0])
    url = chosen.get("baseUrl")
    if not url:
        return None, "caption track carries no url"
    kind = chosen.get("kind") or "written"
    return f"{url}&fmt=json3", f"ok ({kind}, {len(tracks)} track(s))"


def caption_track_url(video_id):
    """The timed-text URL for a video's captions, or None if it has none."""
    return caption_track(video_id)[0]


def cues(video_id, report_why=None):
    """[{text, start, duration}] in speaking order; [] when unavailable.

    report_why, if given a list, collects the reason -- so a run that comes
    back with nothing can say which of the three things went wrong.
    """
    url, why = caption_track(video_id)
    if report_why is not None:
        report_why.append(why)
    if not url:
        return []
    try:
        payload = json.loads(_get(url))
    except (OSError, json.JSONDecodeError) as exc:
        if report_why is not None:
            report_why.append(f"timed text failed: {str(exc)[:80]}")
        return []
    out = []
    for event in payload.get("events") or []:
        text = "".join(seg.get("utf8", "") for seg in event.get("segs") or []).strip()
        if not text or text == "\n":
            continue
        out.append({
            "text": re.sub(r"\s+", " ", text),
            "start": (event.get("tStartMs") or 0) / 1000.0,
            "duration": (event.get("dDurationMs") or 0) / 1000.0,
        })
    return out


def beats(entries):
    """Cues regrouped at the pauses, which is as close to sentences as an
    unpunctuated auto-caption track gets."""
    grouped = []
    for cue in entries:
        gap = cue["start"] - (grouped[-1]["end"] if grouped else cue["start"])
        if grouped and gap < BEAT_GAP_SECONDS:
            grouped[-1]["text"] += " " + cue["text"]
            grouped[-1]["end"] = cue["start"] + cue["duration"]
        else:
            grouped.append({"text": cue["text"], "start": cue["start"],
                            "end": cue["start"] + cue["duration"]})
    for beat in grouped:
        beat["words"] = len(beat["text"].split())
        beat["seconds"] = round(beat["end"] - beat["start"], 2)
        beat["rate"] = round(beat["words"] / beat["seconds"], 2) if beat["seconds"] else 0
    return grouped


def summarise(video_id, entries, keep_text=True):
    """The shape of one script: how long, how fast, how it is built."""
    if not entries:
        return {"video_id": video_id, "cues": 0}
    grouped = beats(entries)
    spoken = " ".join(cue["text"] for cue in entries)
    words = len(spoken.split())
    span = entries[-1]["start"] + entries[-1]["duration"] - entries[0]["start"]
    shape = {
        "video_id": video_id,
        "words": words,
        "seconds": round(span, 2),
        "words_per_second": round(words / span, 2) if span else 0,
        "lead_in_seconds": round(entries[0]["start"], 2),
        "beats": len(grouped),
        "beat_words": [beat["words"] for beat in grouped],
        "beat_seconds": [beat["seconds"] for beat in grouped],
        "beat_rates": [beat["rate"] for beat in grouped],
        # The opening is the whole retention decision, so it is measured on
        # its own rather than averaged into the rest.
        "opening_words": grouped[0]["words"],
        "opening_rate": grouped[0]["rate"],
        "opening_has_number": bool(re.search(r"\d", grouped[0]["text"])),
        "closes_on_question": spoken.rstrip().endswith("?"),
    }
    if keep_text:
        shape["beats_text"] = [beat["text"] for beat in grouped]
    return shape


def report(video_ids, keep_text=True):
    return [summarise(vid, cues(vid), keep_text=keep_text) for vid in video_ids]


def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ids", nargs="*", help="Video ids, Shorts URLs, or both.")
    parser.add_argument("--from-text", help="A file of pasted page source to pull ids out of.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--out", default="-")
    parser.add_argument("--no-text", dest="keep_text", action="store_false",
                        help="Shape only, without the words.")
    args = parser.parse_args()

    blob = " ".join(args.ids)
    if args.from_text:
        blob += " " + open(args.from_text, encoding="utf-8").read()
    ids = video_ids_from_text(blob) or [v for v in args.ids if len(v) == 11]
    if not ids:
        parser.error("no video ids found")
    ids = ids[:args.limit]

    rows, skipped = [], []
    for video_id in ids:
        why = []
        shape = summarise(video_id, cues(video_id, report_why=why),
                          keep_text=args.keep_text)
        reason = "; ".join(why) or "unknown"
        if not shape.get("words"):
            print(f"[shorts] {video_id}: SKIPPED -- {reason}", file=sys.stderr)
            skipped.append({"video_id": video_id, "skipped": reason})
            continue
        print(f"[shorts] {video_id}: {shape['words']} words in {shape['seconds']}s "
              f"({shape['words_per_second']} w/s), {shape['beats']} beats. [{reason}]",
              file=sys.stderr)
        rows.append(shape)

    # The skips go in the file too. A run that read nothing used to write an
    # empty list, which told the dashboard nothing and looked like success.
    rows.extend(skipped)

    payload = json.dumps(rows, indent=2, ensure_ascii=False)
    if args.out == "-":
        print(payload)
    else:
        from pathlib import Path
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"[shorts] Wrote {len(rows)} of {len(ids)} to {args.out}.", file=sys.stderr)
    read = len([r for r in rows if r.get("words")])
    if not read:
        # Exiting green on nothing is how three runs looked fine while the
        # dashboard showed an empty box.
        print(f"[shorts] Read 0 of {len(ids)} videos.", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
