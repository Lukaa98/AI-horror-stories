import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from channel_tools.youtube_client import get_authenticated_service
from automation.pull_video_stats import fetch_video_stats


HISTORY_PATH = Path("automation/history.json")


def _load_history():
    if not HISTORY_PATH.exists():
        return {"videos": []}
    return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))


def _parse_iso8601(value):
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _reference_time(entry):
    for key in ("publish_at", "published_at", "uploaded_at"):
        parsed = _parse_iso8601(entry.get(key))
        if parsed is not None:
            return parsed
    return None


def _should_poll(entry, min_age_hours, repoll_after_hours):
    now = datetime.now(timezone.utc)
    reference_time = _reference_time(entry)
    if reference_time is None:
        return False
    if now - reference_time < timedelta(hours=min_age_hours):
        return False

    latest_stats = entry.get("latest_stats")
    if not latest_stats:
        return True

    last_polled_at = _parse_iso8601(entry.get("last_polled_at"))
    if last_polled_at is None:
        return True
    return now - last_polled_at >= timedelta(hours=repoll_after_hours)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-age-hours", type=float, default=24.0)
    parser.add_argument("--repoll-after-hours", type=float, default=24.0)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    history = _load_history()
    candidates = [
        entry for entry in history.get("videos", [])
        if entry.get("video_id") and _should_poll(entry, args.min_age_hours, args.repoll_after_hours)
    ]
    candidates.sort(key=lambda entry: _reference_time(entry) or datetime.min.replace(tzinfo=timezone.utc))
    candidates = candidates[: args.limit]

    youtube = get_authenticated_service()
    results = []
    for entry in candidates:
        try:
            results.append(fetch_video_stats(youtube, entry["video_id"]))
        except Exception as exc:
            results.append(
                {
                    "video_id": entry["video_id"],
                    "error": str(exc),
                }
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
