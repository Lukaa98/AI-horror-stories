import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUTOMATION_DIR = ROOT / "automation"
PLAN_PATH = AUTOMATION_DIR / "content_plan.json"
STATE_PATH = AUTOMATION_DIR / "state.json"
RUNTIME_PATH = AUTOMATION_DIR / "runtime_config.json"
HISTORY_PATH = AUTOMATION_DIR / "history.json"


def _load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _next_publish_at():
    publish_at = datetime.now(timezone.utc) + timedelta(hours=6)
    return publish_at.replace(minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


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
        "DECIDED_AT": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    AUTOMATION_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_PATH.write_text(json.dumps(runtime_config, indent=2), encoding="utf-8")
    STATE_PATH.write_text(
        json.dumps({"next_plan_index": (index + 1) % len(plan["entries"]), "last_selected_index": index}, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(runtime_config, indent=2))


if __name__ == "__main__":
    main()
