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


def main():
    plan = _load_json(PLAN_PATH, {"entries": []})
    if not plan.get("entries"):
        raise RuntimeError(f"No automation entries found in {PLAN_PATH}")

    state = _load_json(STATE_PATH, {"next_plan_index": 0})
    history = _load_json(HISTORY_PATH, {"videos": []})

    index = int(state.get("next_plan_index", 0)) % len(plan["entries"])
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
        json.dumps({"next_plan_index": (index + 1) % len(plan["entries"])}, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(runtime_config, indent=2))


if __name__ == "__main__":
    main()
