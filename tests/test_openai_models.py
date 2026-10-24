import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cars" / "automation")]

import openai_models  # noqa: E402


def _fresh(monkeypatch, available, env=None):
    """A clean resolver over a pretend account."""
    monkeypatch.setattr(openai_models, "_resolved", {})
    monkeypatch.setattr(openai_models, "_available", lambda: available)
    for name in ("CARS_TEXT_MODEL", "CARS_CHEAP_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)


def test_the_real_account_resolves_to_sol_for_writing_and_luna_for_the_rest(monkeypatch):
    """The ids OpenAI actually serves, and the one choice in here that is
    about money rather than patterns.

    Astra is $10/$50 per million tokens, Sol $2/$10, Luna $0.10/$0.50.
    Sol is slightly cheaper than the gpt-4o it replaces ($2.50/$10) and two
    families newer, so writing on it is not a trade. Luna is 20x cheaper
    again and fine for one-word classifications. Astra is left out of
    automatic selection on purpose -- five times the price for a 150-word
    script -- and reachable with CARS_TEXT_MODEL.
    """
    _fresh(monkeypatch, ["gpt-4o", "gpt-4o-mini", "gpt-5.5", "gpt-5.6",
                         "gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna"])
    assert openai_models.text_model() == "gpt-6.1-sol"
    assert openai_models.cheap_model() == "gpt-6-luna"


def test_it_takes_the_cheaper_tier_of_the_newest_family(monkeypatch):
    """The writing stages are called several times a build with web search
    on, so the mid and small tiers of a newer family are both cheaper and
    better than the top of an older one. The flagship is reachable by
    setting the environment variable, not by default."""
    _fresh(monkeypatch, ["gpt-4o", "gpt-4o-mini", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna"])
    assert openai_models.text_model() == "gpt-6-sol"
    assert openai_models.cheap_model() == "gpt-6-luna"

    # A family that names its tiers differently resolves just as well --
    # the point of matching patterns rather than pinning a string.
    _fresh(monkeypatch, ["gpt-4o", "gpt-4o-mini", "gpt-6", "gpt-6-mini", "gpt-6-nano"])
    assert openai_models.text_model() == "gpt-6-mini"
    assert openai_models.cheap_model() == "gpt-6-mini"


def test_a_point_release_beats_the_release_it_followed(monkeypatch):
    """Run #339 resolved gpt-6-sol on an account that also had gpt-6.1-sol,
    because the tiebreak inside a tier was the shorter name -- which
    prefers the OLDER release. Dated snapshots are excluded by the ladder's
    anchors, so length was never doing the job it was there for."""
    _fresh(monkeypatch, ["gpt-4o", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-6.1-sol"])
    assert openai_models.text_model() == "gpt-6.1-sol"
    assert openai_models._version("gpt-6.1-sol") > openai_models._version("gpt-6-sol")
    assert openai_models._version("gpt-6-sol") > openai_models._version("gpt-5-pro")


def test_the_log_line_shows_the_ids_it_exists_to_reveal(monkeypatch, capsys):
    """Sorted ascending and cut at twelve, it reported a dozen gpt-5 ids and
    no gpt-6 ones on an account that had both and was writing on a gpt-6
    model. That line is the only way to learn the current family names from
    inside a build, and it hid them."""
    account = (["gpt-4o", "gpt-4o-mini"]
               + [f"gpt-5{tail}" for tail in
                  ("", "-2025-08-07", "-chat-latest", "-codex", "-mini",
                   "-mini-2025-08-07", "-nano", "-nano-2025-08-07", "-pro",
                   "-pro-2025-10-06", "-search-api", "-search-api-2025-10-14")]
               + ["gpt-6-astra", "gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol"])
    _fresh(monkeypatch, account)
    openai_models._resolve()
    line = capsys.readouterr().out
    assert "Writing with gpt-6.1-sol" in line
    for name in ("gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna", "gpt-6-sol"):
        assert name in line, name
    # Twelve gpt-5 ids no longer crowd the newest family out of the list.
    assert "gpt-5" not in line.split("Newest ids on this account:")[1]


def test_a_dated_snapshot_never_wins_over_the_name_that_outlives_it(monkeypatch):
    """Pinning gpt-6-mini-2026-10-01 works until it is retired under the
    build."""
    _fresh(monkeypatch, ["gpt-4o", "gpt-6-mini-2026-10-01", "gpt-6-mini", "gpt-6"])
    assert openai_models.text_model() == "gpt-6-mini"


def test_an_account_with_nothing_newer_stays_where_it_works(monkeypatch):
    """A hard-coded id the account does not have is not a degraded build,
    it is every build failing on the first call."""
    _fresh(monkeypatch, ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"])
    assert openai_models.text_model() == "gpt-4o"
    assert openai_models.cheap_model() == "gpt-4o-mini"

    # And a model list that cannot be read is not an empty one.
    _fresh(monkeypatch, None)
    assert openai_models.text_model() == "gpt-4o"
    assert openai_models.cheap_model() == "gpt-4o-mini"


def test_the_environment_pins_it_exactly(monkeypatch):
    """So a model can be tried, or a bad resolution overridden, without a
    code change."""
    _fresh(monkeypatch, ["gpt-4o", "gpt-6-sol"], env={"CARS_TEXT_MODEL": "gpt-6-astra"})
    assert openai_models.text_model() == "gpt-6-astra"
    # Still resolved for the tier that was not pinned.
    assert openai_models.cheap_model() == "gpt-4o-mini"

    # With both pinned there is nothing to look up, so the list is not read.
    asked = []
    monkeypatch.setattr(openai_models, "_resolved", {})
    monkeypatch.setattr(openai_models, "_available", lambda: asked.append(1) or [])
    monkeypatch.setenv("CARS_TEXT_MODEL", "a")
    monkeypatch.setenv("CARS_CHEAP_MODEL", "b")
    assert (openai_models.text_model(), openai_models.cheap_model()) == ("a", "b")
    assert not asked


def test_a_model_the_account_lists_but_will_not_serve_falls_back(monkeypatch):
    """The resolver only knows the account lists a model, not that it
    accepts the shape of these requests -- the strict JSON schema and the
    web-search tool. This is what makes it safe to resolve a model nobody
    has tried."""
    _fresh(monkeypatch, ["gpt-4o", "gpt-6-sol"])
    tried = []

    def call(model):
        tried.append(model)
        if model == "gpt-6-sol":
            raise RuntimeError("The model `gpt-6-sol` does not exist or you do not have access")
        return "written"

    assert openai_models.with_text_model(call) == "written"
    assert tried == ["gpt-6-sol", "gpt-4o"]

    # Sticky: the rest of the build does not pay for that discovery again.
    assert openai_models.text_model() == "gpt-4o"
    tried.clear()
    assert openai_models.with_text_model(call) == "written"
    assert tried == ["gpt-4o"]


def test_a_failure_that_is_not_about_the_model_is_not_swallowed(monkeypatch):
    """Falling back on any exception would hide a real failure and pay
    twice for it."""
    _fresh(monkeypatch, ["gpt-4o", "gpt-6-sol"])
    tried = []

    def rate_limited(model):
        tried.append(model)
        raise RuntimeError("Rate limit reached for gpt-6-sol")

    try:
        openai_models.with_text_model(rate_limited)
    except RuntimeError as error:
        assert "Rate limit" in str(error)
    else:
        raise AssertionError("a rate limit must reach the retry helper, not the fallback")
    assert tried == ["gpt-6-sol"], "tried once, not twice"

    assert openai_models.is_model_error("model_not_found")
    assert openai_models.is_model_error("The model `x` is not supported")
    assert not openai_models.is_model_error("insufficient_quota")
    assert not openai_models.is_model_error("429 Too Many Requests")
