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
PERMANENT = ("insufficient_quota", "credit_balance_exhausted", "billing_not_active",
             "account_deactivated")


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
