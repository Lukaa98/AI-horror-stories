from pathlib import Path


def main():
    output_root = Path("src/output")
    candidates = [path for path in output_root.iterdir() if path.is_dir() and path.name.startswith("video_")]
    if not candidates:
        raise RuntimeError(f"No generated runs found in {output_root}")
    latest = max(candidates, key=lambda path: path.name)
    print(str(latest))


if __name__ == "__main__":
    main()
