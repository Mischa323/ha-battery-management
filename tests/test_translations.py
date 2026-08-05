"""Structure of the translation files.

hassfest rejects an unexpected key outright, which costs a CI round trip to
find out. These checks cost half a second — a `desc` where `description` was
meant has already happened once.
"""
from __future__ import annotations

import json
import pathlib

import pytest

COMPONENT = pathlib.Path(__file__).resolve().parents[1] / (
    "custom_components/battery_management"
)
FILES = [
    COMPONENT / "strings.json",
    COMPONENT / "translations/en.json",
    COMPONENT / "translations/nl.json",
]

#: what Home Assistant allows inside a config/options flow step
STEP_KEYS = {
    "title",
    "description",
    "data",
    "data_description",
    "menu_options",
    "submit",
    "section",
}


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_steps_use_only_keys_home_assistant_accepts(path):
    data = load(path)
    for section in ("config", "options"):
        for name, step in data.get(section, {}).get("step", {}).items():
            unexpected = set(step) - STEP_KEYS
            assert not unexpected, f"{section}.{name}: {unexpected}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_step_has_a_title(path):
    data = load(path)
    for section in ("config", "options"):
        for name, step in data.get(section, {}).get("step", {}).items():
            assert step.get("title"), f"{section}.{name}"


def test_the_languages_describe_the_same_thing():
    """A step or field that exists in one language and not the other shows up
    as a raw key in the interface."""
    english = load(FILES[1])
    dutch = load(FILES[2])

    for section in ("config", "options"):
        en_steps = english.get(section, {}).get("step", {})
        nl_steps = dutch.get(section, {}).get("step", {})
        assert set(en_steps) == set(nl_steps), section
        for name in en_steps:
            assert set(en_steps[name].get("data", {})) == set(
                nl_steps[name].get("data", {})
            ), f"{section}.{name}.data"


def test_english_and_dutch_offer_the_same_entity_states():
    english = load(FILES[1])["entity"]
    dutch = load(FILES[2])["entity"]

    assert set(english) == set(dutch)
    for domain, entities in english.items():
        for key, entity in entities.items():
            assert set(entity.get("state", {})) == set(
                dutch[domain][key].get("state", {})
            ), f"{domain}.{key}"
