"""Every structured-output schema, against the rule OpenAI enforces.

Run #216 died 48 seconds in with "Missing 'youtube_title'" because a new
property was added to PACKAGE_SCHEMA's properties and not to its required
list. Strict structured output rejects that outright, and no test covered
it -- the schema is data, so it stays valid-looking right up until the API
sees it. This checks the rule for every schema in the pipeline so the next
field added cannot repeat it.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cars" / "automation"))


def _schemas():
    import battle_request
    import research_request
    import single_car_short
    import suggest_rivals

    return [
        ("single_car_short.PACKAGE_SCHEMA", single_car_short.PACKAGE_SCHEMA),
        ("research_request.NARRATION_OUTPUT_SCHEMA", research_request.NARRATION_OUTPUT_SCHEMA),
        ("research_request.RESEARCH_OUTPUT_SCHEMA", research_request.RESEARCH_OUTPUT_SCHEMA),
        ("battle_request.GENERATION_SCHEMA", battle_request.GENERATION_SCHEMA),
        ("suggest_rivals.RIVALS_SCHEMA", suggest_rivals.RIVALS_SCHEMA),
    ]


def _walk(node, path="root"):
    """Every object in the schema that declares properties."""
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            yield path, node
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")


@pytest.mark.parametrize("name,schema", _schemas(), ids=lambda value: value if isinstance(value, str) else "")
def test_every_property_is_listed_as_required(name, schema):
    """Strict mode demands required list every key in properties, exactly.
    A property missing from it fails the request before a single token is
    generated -- which is cheap, but only if someone is watching the run."""
    for path, node in _walk(schema, name):
        properties = set(node.get("properties") or {})
        required = set(node.get("required") or [])
        assert properties == required, (
            f"{path}: required must list every property.\n"
            f"  missing from required: {sorted(properties - required)}\n"
            f"  required but not a property: {sorted(required - properties)}"
        )


@pytest.mark.parametrize("name,schema", _schemas(), ids=lambda value: value if isinstance(value, str) else "")
def test_every_object_forbids_extra_properties(name, schema):
    """Strict mode also requires additionalProperties: false on every object."""
    for path, node in _walk(schema, name):
        assert node.get("additionalProperties") is False, \
            f"{path}: strict structured output needs additionalProperties: false"
