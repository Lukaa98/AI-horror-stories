import argparse
import html
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "automation" / "channels" / "cars" / "content_plan.json"
DEFAULT_OUT_PATH = ROOT / "automation" / "channels" / "cars" / "researched_topics.json"

KEYWORD_WEIGHTS = {
    "ferrari": 8,
    "porsche": 7,
    "lamborghini": 7,
    "tesla": 7,
    "bmw": 5,
    "mercedes": 5,
    "lexus": 5,
    "toyota": 4,
    "ev": 5,
    "electric": 5,
    "hybrid": 4,
    "manual": 5,
    "recall": 4,
    "price": 4,
    "configurator": 4,
    "revealed": 4,
    "new": 3,
    "first": 3,
    "fastest": 3,
    "cheapest": 3,
    "deal": 3,
    "auction": 3,
    "limited": 3,
    "controversy": 3,
}

FORMAT_HINTS = [
    ("car deal of the day", ("deal", "used", "auction", "cheapest", "mileage")),
    ("rich people car drama", ("ferrari", "lamborghini", "bugatti", "rolls", "controversy")),
    ("would you buy this", ("price", "expensive", "ev", "electric", "controversial")),
    ("new car explained", ("revealed", "new", "hybrid", "manual", "trim", "configurator")),
    ("one feature nobody noticed", ("feature", "interior", "button", "screen", "clutch")),
    ("3 wild facts", ("first", "fastest", "wild", "secret", "new")),
]


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _strip_tags(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _parse_date(value):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fetch_rss(source, timeout):
    request = urllib.request.Request(
        source["url"],
        headers={"User-Agent": "AI-horror-stories car topic scout/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _rss_items(source, timeout):
    raw = _fetch_rss(source, timeout)
    root = ET.fromstring(raw)
    items = []
    atom_namespace = {"atom": "http://www.w3.org/2005/Atom"}
    for item in root.findall(".//item"):
        title = _strip_tags(item.findtext("title"))
        description = _strip_tags(item.findtext("description"))
        link = (item.findtext("link") or "").strip()
        published_at = _parse_date(item.findtext("pubDate"))
        if not title or not link:
            continue
        items.append(
            {
                "title": title,
                "summary": description,
                "url": link,
                "published_at": published_at.isoformat().replace("+00:00", "Z") if published_at else None,
                "source_name": source["name"],
                "source_type": source.get("type", "rss"),
                "source_credibility": source.get("credibility", "unknown"),
            }
        )
    for entry in root.findall(".//atom:entry", atom_namespace):
        title = _strip_tags(entry.findtext("atom:title", default="", namespaces=atom_namespace))
        description = _strip_tags(entry.findtext("atom:summary", default="", namespaces=atom_namespace))
        link_element = entry.find("atom:link", atom_namespace)
        link = link_element.attrib.get("href", "").strip() if link_element is not None else ""
        published_at = _parse_date(entry.findtext("atom:published", default="", namespaces=atom_namespace))
        if not title or not link:
            continue
        items.append(
            {
                "title": title,
                "summary": description,
                "url": link,
                "published_at": published_at.isoformat().replace("+00:00", "Z") if published_at else None,
                "source_name": source["name"],
                "source_type": source.get("type", "rss"),
                "source_credibility": source.get("credibility", "unknown"),
            }
        )
    return items


def _score_item(item, now, freshness_window_days):
    text = f"{item['title']} {item.get('summary', '')}".lower()
    score = 0
    matched = []
    for keyword, weight in KEYWORD_WEIGHTS.items():
        if keyword in text:
            score += weight
            matched.append(keyword)

    published_at = item.get("published_at")
    if published_at:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        age_days = max(0.0, (now - published).total_seconds() / 86400.0)
        if age_days <= freshness_window_days:
            score += max(0, int(10 - age_days))
        else:
            score -= 8
    else:
        age_days = None
        score -= 2

    return score, matched, age_days


def _choose_format(title, summary, templates):
    text = f"{title} {summary}".lower()
    available = {template["format"]: template for template in templates}
    for format_name, hints in FORMAT_HINTS:
        if format_name in available and any(hint in text for hint in hints):
            return available[format_name]
    return available.get("3 wild facts", templates[0])


def _candidate_from_item(item, templates, now, freshness_window_days):
    score, matched_keywords, age_days = _score_item(item, now, freshness_window_days)
    template = _choose_format(item["title"], item.get("summary", ""), templates)
    topic = {
        "format": template["format"],
        "theme": template["hook_template"].format(
            vehicle_or_topic=item["title"],
            vehicle_or_feature=item["title"],
            brand_or_fans="car fans",
            used_vehicle="used car",
            comparison_vehicle="a new economy car",
        ),
        "story_tone": template["story_tone"],
        "research_need": template["research_need"],
        "source_urls": [item["url"]],
        "source_titles": [item["title"]],
        "source_names": [item["source_name"]],
        "published_at": item.get("published_at"),
        "freshness_age_days": round(age_days, 2) if age_days is not None else None,
        "score": score,
        "matched_keywords": matched_keywords,
        "notes": "Auto-discovered candidate. Add official manufacturer/configurator source before generating a car video when the format requires it.",
    }
    return topic


def discover_topics(config_path, out_path, limit, timeout):
    config = _load_json(config_path)
    pipeline = config.get("research_pipeline", {})
    freshness_window_days = int(pipeline.get("freshness_window_days", 14))
    templates = config.get("format_templates", [])
    if not templates:
        raise RuntimeError(f"No format_templates found in {config_path}")

    now = datetime.now(timezone.utc)
    min_date = now - timedelta(days=freshness_window_days)
    candidates = []
    errors = []
    seen_urls = set()

    for source in config.get("discovery_sources", []):
        if source.get("type") not in {"rss", "youtube_rss"}:
            continue
        try:
            items = _rss_items(source, timeout)
        except (ET.ParseError, TimeoutError, urllib.error.URLError, OSError) as exc:
            errors.append({"source": source.get("name"), "url": source.get("url"), "error": str(exc)})
            continue
        for item in items:
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            published_at = item.get("published_at")
            if published_at:
                published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                if published < min_date:
                    continue
            candidates.append(_candidate_from_item(item, templates, now, freshness_window_days))

    candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    payload = {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "channel_name": config.get("channel_name"),
        "mode": config.get("mode"),
        "status": config.get("status"),
        "source_policy": config.get("source_policy"),
        "topics": candidates[:limit],
        "errors": errors,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main():
    parser = argparse.ArgumentParser(description="Discover fresh car Shorts topic candidates from configured sources.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args()

    payload = discover_topics(args.config, args.out, args.limit, args.timeout)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
