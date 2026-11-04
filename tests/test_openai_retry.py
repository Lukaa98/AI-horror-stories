"""Which OpenAI failures are worth waiting out, and which are not."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))

from openai_retry import is_permanent, with_openai_retry  # noqa: E402

QUOTA = ("Error code: 429 - {'error': {'message': 'You have no credits remaining. Add credits "
         "to continue using the API.', 'type': 'insufficient_quota', "
         "'code': 'credit_balance_exhausted'}}")
BUSY = "Error code: 429 - {'error': {'type': 'rate_limit_exceeded'}}"


def _raises(message, attempts):
    def call():
        attempts.append(1)
        raise RuntimeError(message)
    return call


def test_an_exhausted_balance_is_not_waited_out(monkeypatch):
    """An empty balance and a spent quota are the same status code as being
    briefly over the per-minute budget. Matching on 429 alone retried them
    too, so the SLR build backed off fifteen seconds before each of dozens
    of calls and took forty minutes to report a failure it knew about
    immediately."""
    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))

    attempts = []
    with pytest.raises(RuntimeError):
        with_openai_retry(_raises(QUOTA, attempts))

    assert attempts == [1], "it should give up on the first answer"
    assert not slept


def test_being_briefly_over_the_budget_still_is(monkeypatch):
    """Several stages share one per-minute budget, so a real rate limit is
    normal load and worth a short wait -- which is what this is for."""
    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))

    attempts = []
    with pytest.raises(RuntimeError):
        with_openai_retry(_raises(BUSY, attempts), max_retries=3)

    assert len(attempts) == 4
    assert slept == [1.0, 2.0, 4.0]


def test_the_permanent_failures_are_named_by_their_code():
    assert is_permanent(QUOTA)
    assert is_permanent("billing_not_active")
    assert not is_permanent(BUSY)
    # An error that is not a 429 at all was never retried and still is not.
    assert not is_permanent("Error code: 500 - server had a problem")


def test_a_call_that_works_is_not_retried():
    calls = []
    assert with_openai_retry(lambda: calls.append(1) or "ok") == "ok"
    assert len(calls) == 1


# The exact text the API answered with on 2026-11-04, which carried none of
# the machine-readable codes.
NO_CREDITS = ("Error code: 429 - You have no credits remaining. Add credits to continue "
              "using the API at https://platform.openai.com/settings/organization/billing/.")


def test_an_empty_balance_is_permanent_however_it_is_worded():
    """Run #356 backed off through all four retries on this message before
    giving up, because the list only matched the codes and this one is
    prose. The whole point of the list is that the status code cannot say
    which kind of 429 it is, so the wording has to be matched too."""
    assert is_permanent(NO_CREDITS)
    assert is_permanent("You exceeded your current quota, please check your plan and "
                        "billing details.")
    assert not is_permanent(BUSY)


def test_the_credit_check_stops_a_build_only_for_an_empty_balance(monkeypatch):
    """Runs #347, #348 and #356 each scraped the listing, researched the car
    and wrote the whole script before finding out the balance was empty --
    sixteen minutes in the last one. Anything other than a billing failure
    must let the build run: a network blip here cannot be allowed to stop a
    build that would have worked."""
    import openai_retry

    class _Client:
        def __init__(self, error):
            self._error = error
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **_kwargs):
            if self._error:
                raise RuntimeError(self._error)
            return "ok"

    def answering(error):
        monkeypatch.setattr(openai_retry, "OpenAI", lambda: _Client(error), raising=False)
        import sys
        import types
        fake = types.ModuleType("openai")
        fake.OpenAI = lambda: _Client(error)
        monkeypatch.setitem(sys.modules, "openai", fake)
        models = types.ModuleType("openai_models")
        models.cheap_model = lambda: "gpt-6-luna"
        monkeypatch.setitem(sys.modules, "openai_models", models)
        return openai_retry.credits_available()

    funded, reason = answering(NO_CREDITS)
    assert funded is False
    assert "no credits remaining" in reason

    # A paid call that works, and a failure that is not about money.
    assert answering(None) == (True, "")
    assert answering("Connection reset by peer") == (True, "")
    assert answering(BUSY) == (True, "")
