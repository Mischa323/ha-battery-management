"""Structure of the translation files.

hassfest rejects an unexpected key outright, which costs a CI round trip to
find out. These checks cost half a second — a `desc` where `description` was
meant has already happened once.
"""
from __future__ import annotations

import json
import pathlib
import re

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


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_field_is_explained(path):
    """A setting nobody can explain in a year is a setting nobody dares touch.

    Home Assistant renders `data_description` under each field, so this is the
    place the explanation belongs - not a wiki that drifts.
    """
    data = load(path)
    for section in ("config", "options"):
        for name, step in data.get(section, {}).get("step", {}).items():
            fields = set(step.get("data", {}))
            explained = set(step.get("data_description", {}))
            assert fields <= explained, f"{section}.{name}: {fields - explained}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_explanation_without_a_field(path):
    """A leftover explanation means a field was renamed and this was missed."""
    data = load(path)
    for section in ("config", "options"):
        for name, step in data.get(section, {}).get("step", {}).items():
            orphans = set(step.get("data_description", {})) - set(step.get("data", {}))
            assert not orphans, f"{section}.{name}: {orphans}"


def test_the_two_languages_explain_the_same_fields():
    english = load(FILES[1])
    dutch = load(FILES[2])
    for section in ("config", "options"):
        for name, step in english.get(section, {}).get("step", {}).items():
            assert set(step.get("data_description", {})) == set(
                dutch[section]["step"][name].get("data_description", {})
            ), f"{section}.{name}"


def menu_options() -> set[str]:
    """The options menu as the flow actually declares it.

    Read out of the source rather than imported: this file runs against the
    stubbed Home Assistant too, where the config flow cannot be imported at all.
    """
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    listing = re.search(r"menu_options=\[(.*?)\]", source, re.S)
    assert listing, "could not find the options menu"
    return set(re.findall(r'"([^"]+)"', listing.group(1)))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_menu_entry_has_a_label(path):
    """An untranslated menu entry renders as its raw key, which looks broken."""
    labels = load(path)["options"]["step"]["init"].get("menu_options", {})
    steps = load(path)["options"]["step"]

    assert menu_options() == set(labels)
    for name in labels:
        assert name in steps, f"menu points at a step that does not exist: {name}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_policy_and_detection_state_is_named(path):
    """These are enum sensors: an untranslated state shows the bare slug on a
    dashboard, which is where somebody is trying to work out what the packs are
    doing."""
    from custom_components.battery_management.const import (
        PHASE_DETECT_STATES,
        POLICIES,
    )

    sensors = load(path)["entity"]["sensor"]
    assert set(POLICIES) <= set(sensors["active_policy"]["state"])
    assert set(PHASE_DETECT_STATES) <= set(sensors["phase_detection"]["state"])


def test_translated_entities_are_reached_by_a_translation_key():
    """`_attr_name` bypasses the translation file entirely, so a state listed
    here would never be applied and the dashboard would show the raw slug."""
    source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    keys = set(re.findall(r'_attr_translation_key = "([^"]+)"', source))

    for key, entity in load(FILES[0])["entity"]["sensor"].items():
        if "state" in entity:
            assert key in keys, f"{key} has state translations but no entity uses it"
