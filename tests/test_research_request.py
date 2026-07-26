import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cars" / "automation"))

import research_request  # noqa: E402


class FakeRateLimitError(Exception):
    code = "insufficient_quota"


def test_run_research_explains_insufficient_quota(monkeypatch):
    class FakeClient:
        responses = SimpleNamespace(create=lambda **kwargs: (_ for _ in ()).throw(FakeRateLimitError()))

    fake_openai = SimpleNamespace(OpenAI=FakeClient, RateLimitError=FakeRateLimitError)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    with pytest.raises(SystemExit, match="GitHub OPENAI_API_KEY"):
        research_request.run_research("Rank Audi R8 generations")


def test_research_prompt_separates_fact_ranking_from_image_availability():
    prompt = research_request.RESEARCH_PROMPT_TEMPLATE

    assert "Do NOT prefer or exclude a candidate" in prompt
    assert "manufacturer press rooms" in prompt
    assert "Hagerty" in prompt
    assert "research_sources" in prompt


def test_final_narration_template_keeps_prices_but_avoids_repetition():
    prompt = research_request.NARRATION_PROMPT_TEMPLATE

    assert "Mention every entry's original price naturally" in prompt
    assert "Connect price to meaning" in prompt
    assert "At number four" in prompt
    assert "Today, clean examples trade around" in prompt


def test_ranking_total_rewards_enthusiast_merit_not_model_year():
    manual = {
        "introduced_year": 2015,
        "ranking_scores": {
            "enthusiast_desirability": 10,
            "driving_engagement": 10,
            "historical_significance": 8,
            "performance": 8,
            "collectibility": 10,
            "value": 6,
        },
    }
    newer = {
        "introduced_year": 2023,
        "ranking_scores": {
            "enthusiast_desirability": 7,
            "driving_engagement": 7,
            "historical_significance": 6,
            "performance": 10,
            "collectibility": 8,
            "value": 4,
        },
    }

    assert research_request.calculate_ranking_total(manual) > research_request.calculate_ranking_total(newer)


def test_run_research_preserves_source_metadata(monkeypatch):
    payload = {
        "title": "RANKING EUROPEAN ICONS",
        "highlight_word": "EUROPEAN",
        "close_narration": "Which icon are you taking?",
        "order_rationale": "Ranked by impact.",
        "entries": [
            {
                "name": f"Car {index}",
                "research_sources": [{
                    "url": f"https://example.com/car-{index}",
                    "title": f"Car {index}",
                    "publisher": "Example",
                    "source_type": "specialist",
                    "supports": ["history"],
                }],
            }
            for index in range(4)
        ],
    }

    class FakeClient:
        responses = SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(output_text=__import__("json").dumps(payload))
        )

    fake_openai = SimpleNamespace(OpenAI=FakeClient, RateLimitError=FakeRateLimitError)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    result = research_request.run_research("Rank European icons")

    assert result["entries"][0]["research_sources"][0]["source_type"] == "specialist"


def test_run_research_requests_strict_schema_and_retries_incomplete_json(monkeypatch):
    payload = {
        "title": "RANKING R8S",
        "highlight_word": "R8S",
        "close_narration": "Which one?",
        "order_rationale": "Ranked by enthusiast merit.",
        "entries": [{"name": f"R8 {index}", "research_sources": []} for index in range(4)],
    }
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return SimpleNamespace(
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                output_text='{"entries": [{"name": "unfinished',
            )
        return SimpleNamespace(status="completed", output_text=__import__("json").dumps(payload))

    class FakeClient:
        responses = SimpleNamespace(create=create)

    fake_openai = SimpleNamespace(OpenAI=FakeClient, RateLimitError=FakeRateLimitError)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    result = research_request.run_research("Rank Audi R8s")

    assert len(calls) == 2
    assert calls[0]["max_output_tokens"] == 12000
    assert calls[1]["max_output_tokens"] == 10000
    assert calls[0]["text"]["format"]["type"] == "json_schema"
    assert calls[0]["text"]["format"]["strict"] is True
    assert "RETRY MODE" in calls[1]["input"]
    assert len(result["entries"]) == 4


def test_compose_final_narration_updates_each_entry(monkeypatch):
    entries = [
        {
            "name": f"R8 Version {index}",
            "years": "2007-2012",
            "price_usd": 100000 + index,
            "horsepower": 400 + index,
            "current_value_display": f"${60 + index}K",
        }
        for index in range(4)
    ]
    payload = {
        "entries": [
            {"name": entry["name"], "narration": f"Human paragraph for {entry['name']}."}
            for entry in entries
        ],
        "close_narration": "Which R8 are you taking home?",
    }

    class FakeClient:
        responses = SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(output_text=__import__("json").dumps(payload))
        )

    fake_openai = SimpleNamespace(OpenAI=FakeClient)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    close = research_request.compose_final_narration("Rank Audi R8s", entries)

    assert close == "Which R8 are you taking home?"
    assert entries[0]["narration"] == "Human paragraph for R8 Version 0."
    assert entries[0]["one_line_fact"] == entries[0]["narration"]
