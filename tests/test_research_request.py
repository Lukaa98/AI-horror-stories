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
