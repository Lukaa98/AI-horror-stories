"""Shared retry helper for OpenAI calls across the research/render pipeline.

Several stages (image review during research, scene/thumbnail classification
and verification during engine-clip selection, license-plate detection) share
one org-level tokens-per-minute budget. Running multiple research/video-test
workflows concurrently routinely exhausts that per-minute budget well before
any real usage cap, so a 429 here is normal load, not a fatal error - retrying
briefly avoids aborting an entire run (or silently degrading review quality)
over what is usually a sub-second wait.
"""
import time

# A 429 that waiting cannot clear. An exhausted balance and a spent quota
# are the same status code as being briefly over the per-minute budget, so
# matching on 429 alone retried them too: a build that ran out of credits
# spent fifteen seconds backing off before each of dozens of calls and took
# forty minutes to report a failure it knew about immediately.
#
# "no credits remaining" is the wording the API actually used on 2026-11-04,
# and none of the machine-readable codes were in that message -- so it was
# treated as ordinary load and backed off through all four retries before
# giving up. Matching the prose too, since this list exists precisely
# because the status code cannot be trusted to say which kind of 429 it is.
PERMANENT = ("insufficient_quota", "credit_balance_exhausted", "billing_not_active",
             "account_deactivated", "no credits remaining",
             "exceeded your current quota", "billing details")


def is_permanent(message):
    return any(code in message for code in PERMANENT)


def with_openai_retry(call, max_retries=4, initial_delay=1.0, backoff=2.0):
    delay = initial_delay
    for attempt in range(max_retries + 1):
        try:
            return call()
        except Exception as exc:
            message = str(exc)
            retryable = ("rate_limit" in message or "429" in message) and not is_permanent(message)
            if attempt < max_retries and retryable:
                time.sleep(delay)
                delay *= backoff
                continue
            raise


def credits_available():
    """Whether the API will answer a paid call at all, in about a second.

    Run #356 scraped the listing, researched the car, wrote the script and
    repaired it through four attempts -- sixteen minutes -- and then died on
    the narration call with "You have no credits remaining." Runs #347 and
    #348 did the same thing before it. The balance is knowable at second
    one, so there is no reason to find out at minute sixteen.

    Returns (True, "") when a paid call succeeds, (False, reason) when the
    balance or the billing is the problem, and (True, "") for anything else
    -- a network blip or an unexpected error must not stop a build that
    would otherwise have worked.
    """
    from openai import OpenAI
    from openai_models import cheap_model

    try:
        OpenAI().chat.completions.create(
            model=cheap_model(),
            messages=[{"role": "user", "content": "ping"}],
            max_completion_tokens=1,
        )
    except Exception as exc:  # noqa: BLE001 - any failure here is advisory
        message = str(exc)
        if is_permanent(message):
            return False, message.strip()[:300]
    return True, ""
