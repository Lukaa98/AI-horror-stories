"""Which OpenAI model each stage asks for.

Twenty-six call sites across thirteen files each named "gpt-4o" or
"gpt-4o-mini" as a literal, so moving off 4o meant editing all of them and
hoping none was missed. They ask here instead.

Resolved against what the account can actually serve rather than pinned to
a string somebody typed. The names move -- 4o, then the 5 family, then the
6 family -- and a hard-coded id that the account does not have is not a
degraded build, it is every build failing on the first call. So the
preference is expressed as a ladder of patterns, matched against the
model list the API itself returns, and anything unmatched falls back to
the 4o pair that is known to work.

The resolved choice is printed once per process, with the newer ids that
were on offer. That log line is the only reliable way to find out what the
current family is actually called from inside a build.
"""
import os
import re

# Known-good, and the floor. Every stage here worked on these.
FALLBACK_TEXT = "gpt-4o"
FALLBACK_CHEAP = "gpt-4o-mini"

# The writing and research calls. Sol, not Astra and not Luna.
#
# The three GPT-6 tiers are Astra at $10/$50 per million tokens, Sol at
# $2/$10 and Luna at $0.10/$0.50. Sol is the one to write on: it is
# slightly CHEAPER than the gpt-4o it replaces ($2.50/$10) while being two
# families newer, so this is not a trade at all. Astra at five times the
# price is not worth it for a 150-word script, and Luna -- which a
# shortest-name tiebreak picked at first -- is the wrong end of the range
# for the one job in this pipeline where the writing quality is the
# product.
#
# Named tiers first because they are what the API actually serves today;
# the generic patterns behind them are for the next rename, so this still
# resolves to something sensible when Sol and Luna are superseded.
_TEXT_LADDER = (
    r"^gpt-6(?:\.\d+)?-sol$",
    r"^gpt-6(?:\.\d+)?-(?:mini|medium)$",
    r"^gpt-6(?:\.\d+)?$",
    r"^gpt-5\.6$",
    r"^gpt-5(?:\.\d+)?-(?:sol|mini)$",
    r"^gpt-5(?:\.\d+)?$",
    r"^gpt-4o$",
)
# The classification and review calls: short prompts, one-word answers,
# called many times. Luna at $0.50 per million output is 20x cheaper than
# the gpt-4o-mini-class work it replaces and the job is not hard.
_CHEAP_LADDER = (
    r"^gpt-6(?:\.\d+)?-luna$",
    r"^gpt-6(?:\.\d+)?-(?:nano|mini)$",
    r"^gpt-5(?:\.\d+)?-(?:luna|nano|mini)$",
    r"^gpt-4o-mini$",
)

_resolved = {}


def _available():
    """Every model id the account can serve, or None if the list cannot be
    read. An unreadable list is not an empty one -- it means fall back."""
    try:
        from openai import OpenAI
        return sorted({item.id for item in OpenAI().models.list()})
    except Exception as error:  # noqa: BLE001 - a model list is not worth a build
        print(f"[models] Could not read the model list ({error}); using {FALLBACK_TEXT}.")
        return None


def _version(name):
    """The numeric version in a model id, for ranking inside one tier.

    "gpt-6.1-sol" is (6, 1) and "gpt-6-sol" is (6, 0), so the point release
    wins. The first run of this picked gpt-6-sol on an account that had
    gpt-6.1-sol too, because the tiebreak was the shorter name -- which
    prefers an older release, the opposite of what was wanted. Dated
    snapshots are already excluded by the anchors in the ladder, so length
    was never doing the job it was there for.
    """
    found = re.search(r"gpt-(\d+)(?:\.(\d+))?", name)
    if not found:
        return (0, 0)
    return (int(found.group(1)), int(found.group(2) or 0))


def _pick(ladder, available, fallback):
    for pattern in ladder:
        matches = [name for name in available if re.match(pattern, name)]
        if matches:
            # Highest version inside the tier, then the shorter name.
            return min(matches, key=lambda name: (tuple(-n for n in _version(name)), len(name)))
    return fallback


def _resolve():
    if "text" in _resolved:
        return
    override_text = os.environ.get("CARS_TEXT_MODEL", "").strip()
    override_cheap = os.environ.get("CARS_CHEAP_MODEL", "").strip()
    if override_text and override_cheap:
        # Both pinned: nothing to look up, so the call is not made.
        _resolved["text"], _resolved["cheap"] = override_text, override_cheap
        print(f"[models] Pinned by environment: {override_text} / {override_cheap}.")
        return
    available = _available()
    if available is None:
        _resolved["text"] = override_text or FALLBACK_TEXT
        _resolved["cheap"] = override_cheap or FALLBACK_CHEAP
        return
    _resolved["text"] = override_text or _pick(_TEXT_LADDER, available, FALLBACK_TEXT)
    _resolved["cheap"] = override_cheap or _pick(_CHEAP_LADDER, available, FALLBACK_CHEAP)
    # The ids that exist beyond 4o, named in the log on purpose: this is
    # how the next build tells us what the current family is called.
    # Newest first, not alphabetical. Sorted ascending and cut at twelve,
    # this line reported a dozen gpt-5 ids and no gpt-6 ones on an account
    # that had both and was writing on gpt-6-sol -- it hid exactly the
    # names it exists to reveal.
    newer = sorted((name for name in available if re.match(r"^gpt-[6-9]", name)),
                   key=lambda name: (tuple(-n for n in _version(name)), name))
    print(f"[models] Writing with {_resolved['text']}, classifying with "
          f"{_resolved['cheap']}."
          + (f" Newest ids on this account: {', '.join(newer[:12])}." if newer
             else " Nothing newer than gpt-5 on this account."))


def text_model():
    """The model for writing and researching a script."""
    _resolve()
    return _resolved["text"]


def cheap_model():
    """The model for classification, image review and other short calls."""
    _resolve()
    return _resolved["cheap"]


# An error that means the model is the problem, not the request. Falling
# back on any exception would hide a real failure and pay twice for it.
_MODEL_ERRORS = ("model_not_found", "does not exist", "do not have access",
                 "is not supported", "unsupported_model", "unsupported_value",
                 "invalid_model", "deprecated")


def is_model_error(message):
    text = str(message).lower()
    return any(code in text for code in _MODEL_ERRORS)


def with_text_model(call):
    """Run `call(model_id)` on the writing model, retrying on the 4o floor
    if that model turns out to be the problem.

    The resolver cannot tell whether a model accepts the shape of these
    requests -- the strict JSON schema, the web-search tool -- only that
    the account lists it. This is what makes it safe to resolve a model
    nobody has tried: if the first call comes back saying the model is
    wrong, the build continues on the one that is known to work.

    The downgrade sticks for the rest of the process, so a model that does
    not fit is paid for once rather than at every call site.
    """
    return _with_fallback(call, "text", FALLBACK_TEXT)


def with_cheap_model(call):
    """The same, for the classification and review calls."""
    return _with_fallback(call, "cheap", FALLBACK_CHEAP)


def _with_fallback(call, slot, fallback):
    chosen = text_model() if slot == "text" else cheap_model()
    try:
        return call(chosen)
    except Exception as error:  # noqa: BLE001 - re-raised unless it is the model
        if chosen == fallback or not is_model_error(error):
            raise
        print(f"[models] {chosen} will not take this call ({str(error)[:200]}); "
              f"falling back to {fallback} for the rest of this build.")
        _resolved[slot] = fallback
        return call(fallback)
