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
