import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


HISTORY_PATH = Path("automation/history.json")


def _load_history():
    if not HISTORY_PATH.exists():
        return {"videos": []}
    return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))


def _write_history(history):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(history, indent=2), encoding="utf-8")


def _upsert(history, video_id, patch):
    for entry in history["videos"]:
        if entry.get("video_id") == video_id:
            entry.update(patch)
            return
    history["videos"].append(patch)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--upload-metadata")
    parser.add_argument("--stats-metadata")
    parser.add_argument("--stats-batch-metadata")
    args = parser.parse_args()

    history = _load_history()

    if args.upload_metadata:
        upload = json.loads(Path(args.upload_metadata).read_text(encoding="utf-8"))
        upload["history_updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _upsert(history, upload["video_id"], upload)

    if args.stats_metadata:
        stats = json.loads(Path(args.stats_metadata).read_text(encoding="utf-8"))
        stats_patch = {
            "video_id": stats["video_id"],
            "latest_stats": stats,
            "last_polled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "history_updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        _upsert(history, stats["video_id"], stats_patch)

    if args.stats_batch_metadata:
        stats_batch = json.loads(Path(args.stats_batch_metadata).read_text(encoding="utf-8"))
        now_value = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for stats in stats_batch:
            stats_patch = {
                "video_id": stats["video_id"],
                "latest_stats": stats,
                "last_polled_at": now_value,
                "history_updated_at": now_value,
            }
            _upsert(history, stats["video_id"], stats_patch)

    _write_history(history)
    print(json.dumps(history, indent=2))


if __name__ == "__main__":
    main()
