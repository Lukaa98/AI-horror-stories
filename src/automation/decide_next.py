import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
AUTOMATION_DIR = ROOT / "automation"
PLAN_PATH = AUTOMATION_DIR / "content_plan.json"
STATE_PATH = AUTOMATION_DIR / "state.json"
RUNTIME_PATH = AUTOMATION_DIR / "runtime_config.json"
HISTORY_PATH = AUTOMATION_DIR / "history.json"
LOCAL_TZ = ZoneInfo("America/New_York")
MIN_UPLOAD_GAP_HOURS = 24
MAX_UPLOAD_GAP_HOURS = 72
EARLY_RELEASE_SCORE = 75.0


def _load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _next_publish_at():
    now_local = datetime.now(timezone.utc).astimezone(LOCAL_TZ)
    publish_at_local = now_local.replace(hour=12, minute=0, second=0, microsecond=0)
    if now_local >= publish_at_local:
        publish_at_local = publish_at_local + timedelta(days=1)
    return publish_at_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso8601(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _reference_time(entry):
    for key in ("publish_at", "published_at", "uploaded_at"):
        parsed = _parse_iso8601(entry.get(key))
        if parsed is not None:
            return parsed
    return None


def _is_mature(entry, min_age_hours=24):
    reference_time = _reference_time(entry)
    if reference_time is None:
        return False
    return datetime.now(timezone.utc) - reference_time >= timedelta(hours=min_age_hours)


def _entry_score(entry):
    latest_stats = entry.get("latest_stats") or {}
    if not latest_stats or latest_stats.get("error"):
        return 0.0
    views = float(latest_stats.get("views") or 0)
    likes = float(latest_stats.get("likes") or 0)
    comments = float(latest_stats.get("comments") or 0)
    return views + (likes * 8.0) + (comments * 20.0)


def _latest_video(history):
    candidates = [entry for entry in history.get("videos", []) if _reference_time(entry) is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda entry: _reference_time(entry))


def _publish_decision(history):
    latest_entry = _latest_video(history)
    if latest_entry is None:
        return True, "No previous uploads in history."

    reference_time = _reference_time(latest_entry)
    age = datetime.now(timezone.utc) - reference_time
    age_hours = age.total_seconds() / 3600.0
    latest_score = _entry_score(latest_entry)
    has_stats = bool(latest_entry.get("latest_stats"))

    if age_hours < 0:
        return False, f"Latest upload is scheduled {abs(age_hours):.1f}h from now."

    if age_hours < MIN_UPLOAD_GAP_HOURS:
        return False, f"Latest upload is only {age_hours:.1f}h old."

    if age_hours >= MAX_UPLOAD_GAP_HOURS:
        return True, f"Latest upload is {age_hours:.1f}h old, forcing release by {MAX_UPLOAD_GAP_HOURS}h cap."

    if has_stats and latest_score >= EARLY_RELEASE_SCORE:
        return True, f"Latest upload score {latest_score:.1f} cleared early-release threshold."

    if not has_stats:
        return False, "Latest upload has not accumulated enough stats yet."

    return False, f"Latest upload score {latest_score:.1f} is below the early-release threshold."


def _choose_plan_index(plan, state, history):
    entries = plan["entries"]
    baseline_index = int(state.get("next_plan_index", 0)) % len(entries)
    mature_entries = [
        entry for entry in history.get("videos", [])
        if entry.get("plan_index") is not None and _is_mature(entry)
    ]

    per_index = {index: [] for index in range(len(entries))}
    for entry in mature_entries:
        try:
            entry_index = int(entry["plan_index"])
        except (TypeError, ValueError):
            continue
        if entry_index in per_index:
            per_index[entry_index].append(entry)

    untested = [index for index in range(len(entries)) if not per_index[index]]
    if untested:
        return untested[baseline_index % len(untested)]

    best_index = baseline_index
    best_score = None
    for index, samples in per_index.items():
        average_score = sum(_entry_score(sample) for sample in samples) / max(1, len(samples))
        exploration_bonus = 25.0 / len(samples)
        total_score = average_score + exploration_bonus
        if best_score is None or total_score > best_score:
            best_score = total_score
            best_index = index
    return best_index


def main():
    plan = _load_json(PLAN_PATH, {"entries": []})
    if not plan.get("entries"):
        raise RuntimeError(f"No automation entries found in {PLAN_PATH}")

    state = _load_json(STATE_PATH, {"next_plan_index": 0})
    history = _load_json(HISTORY_PATH, {"videos": []})

    index = _choose_plan_index(plan, state, history)
    entry = plan["entries"][index]
    should_publish, publish_reason = _publish_decision(history)

    runtime_config = {
        "CHARACTER_NAME": plan.get("character_name", "Leo"),
        "SHORT_TARGET_SECONDS": str(plan.get("target_seconds", 30)),
        "SHORT_NUM_SCENES": str(plan.get("scene_count", 7)),
        "THEME": entry["theme"],
        "STORY_TONE": entry["story_tone"],
        "STYLE_BIBLE": entry.get("style_bible", ""),
        "PIPELINE_PLAN_INDEX": str(index),
        "PIPELINE_HISTORY_COUNT": str(len(history.get("videos", []))),
        "YOUTUBE_PRIVACY": "private",
        "YOUTUBE_PUBLISH_AT": _next_publish_at(),
        "SHOULD_PUBLISH": "1" if should_publish else "0",
        "PUBLISH_DECISION_REASON": publish_reason,
        "DECIDED_AT": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    AUTOMATION_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_PATH.write_text(json.dumps(runtime_config, indent=2), encoding="utf-8")
    next_plan_index = (index + 1) % len(plan["entries"]) if should_publish else int(state.get("next_plan_index", 0)) % len(plan["entries"])
    STATE_PATH.write_text(
        json.dumps(
            {
                "next_plan_index": next_plan_index,
                "last_selected_index": index,
                "last_should_publish": should_publish,
                "last_decision_reason": publish_reason,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(json.dumps(runtime_config, indent=2))


if __name__ == "__main__":
    main()
