import json
import re
import subprocess


def _tokenize(value):
    return re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?", str(value or ""))


def infer_search_params(search_hint):
    tokens = _tokenize(search_hint)
    if len(tokens) < 2:
        return None
    make = tokens[0].lower()
    model = tokens[1].lower()
    return {"make": make, "model": model}


def parse_year_range(value):
    years = [int(part) for part in re.findall(r"\b(?:19|20)\d{2}\b", str(value or ""))]
    if not years:
        return None, None
    if len(years) == 1:
        return years[0], years[0]
    return min(years), max(years)


def load_manifest(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def scrape_entry_images(scraper_dir, draft_images_dir, entry):
    params = infer_search_params(entry.get("search_hint", ""))
    if not params:
        return [], {}

    start_year, end_year = parse_year_range(entry.get("years"))
    topic_slug = re.sub(r"[^a-z0-9]+", "-", entry["name"].lower()).strip("-") or "entry"
    dest = draft_images_dir / topic_slug
    manifest_path = dest / "carsandbids-manifest.json"
    dest.mkdir(parents=True, exist_ok=True)

    cmd = [
        "node",
        "src/scrape-carsandbids-gallery.js",
        f"--make={params['make']}",
        f"--model={params['model']}",
        f"--query={entry.get('search_hint', '')}",
        f"--out-dir={dest}",
        f"--out-json={manifest_path}",
        f"--visual-highlight={entry.get('visual_highlight', '')}",
    ]
    if start_year:
        cmd.append(f"--start-year={start_year}")
    if end_year:
        cmd.append(f"--end-year={end_year}")

    subprocess.run(cmd, cwd=scraper_dir, check=False)
    manifest = load_manifest(manifest_path)
    images = [
        f"images/{topic_slug}/{item['path']}"
        for item in manifest.get("downloaded_images", [])
        if item.get("path") and (dest / item["path"]).exists()
    ]
    return images, manifest
