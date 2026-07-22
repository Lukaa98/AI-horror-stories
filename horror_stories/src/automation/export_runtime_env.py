import json
from pathlib import Path


def main():
    config_path = Path("automation/runtime_config.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for key, value in config.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
