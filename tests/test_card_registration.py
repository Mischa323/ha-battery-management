"""Serving the Lovelace card, and saying so.

"Custom element doesn't exist" is a browser message with a dozen possible
causes on the server: the file was never copied, the static path was never
registered, the frontend was not up yet, the browser is holding an old copy.
Nothing on the server side distinguished them, so the only way to investigate
was to guess - twice, wrongly.

These tests pin the reporting, not the plumbing: whichever way it goes, the
server must be able to say which way it went.
"""
from __future__ import annotations

import pytest

from custom_components.battery_management import (
    CARD_FILENAME,
    CARD_URL,
    _CARD_KEY,
    _async_register_card,
)

pytestmark = pytest.mark.asyncio


class FakeHttp:
    def __init__(self, fail: bool = False) -> None:
        self.registered: list = []
        self.fail = fail

    async def async_register_static_paths(self, configs):
        if self.fail:
            raise RuntimeError("no static paths here")
        self.registered.extend(configs)

    def register_static_path(self, url, path, cache):
        if self.fail:
            raise RuntimeError("nor here")
        self.registered.append((url, path, cache))


class CardHass:
    """Just enough Home Assistant for the registration path."""

    def __init__(self, http_fails: bool = False) -> None:
        self.data: dict = {}
        self.http = FakeHttp(http_fails)

    async def async_add_executor_job(self, func, *args):
        return func(*args)


@pytest.fixture
def offered(monkeypatch):
    """Capture what gets handed to the frontend.

    The suite runs both against real Home Assistant and against the stub, and
    the stub has no `homeassistant.components` at all - so stand one up rather
    than skipping. What is being tested is our reporting either way.
    """
    import sys
    import types

    seen: list[str] = []
    try:
        import homeassistant.components.frontend as frontend
    except ImportError:
        components = sys.modules.setdefault(
            "homeassistant.components", types.ModuleType("homeassistant.components")
        )
        frontend = types.ModuleType("homeassistant.components.frontend")
        monkeypatch.setitem(sys.modules, "homeassistant.components.frontend", frontend)
        monkeypatch.setattr(components, "frontend", frontend, raising=False)
    monkeypatch.setattr(
        frontend, "add_extra_js_url", lambda hass, url: seen.append(url), raising=False
    )
    return seen


async def test_it_reports_exactly_what_it_registered(offered):
    hass = CardHass()

    await _async_register_card(hass)

    report = hass.data[_CARD_KEY]
    assert report["url"] == CARD_URL
    assert report["path"].endswith(CARD_FILENAME)
    assert report["bytes"] > 0, "served an empty file without noticing"
    assert report["served"] in {"async", "legacy"}
    assert report["offered_to_frontend"].startswith(f"{CARD_URL}?v=")
    assert "error" not in report


async def test_the_url_carries_the_version(offered):
    """An unversioned URL means an update silently does nothing."""
    hass = CardHass()

    await _async_register_card(hass)

    assert offered, "nothing was offered to the frontend at all"
    assert "?v=" in offered[0]


async def test_a_missing_file_is_named_rather_than_silently_404ing(
    monkeypatch, offered, caplog
):
    """A missing file registers happily and then 404s, and the user sees the
    same message as for every other cause."""
    import custom_components.battery_management as pkg

    monkeypatch.setattr(pkg.Path, "is_file", lambda self: False)
    hass = CardHass()

    await _async_register_card(hass)

    report = hass.data[_CARD_KEY]
    assert report["error"] == "file_missing"
    assert report["path"] in caplog.text, "the log did not say where it looked"
    assert not offered, "offered a file it knew was not there"


async def test_a_failing_static_path_is_recorded(offered):
    hass = CardHass(http_fails=True)

    await _async_register_card(hass)

    report = hass.data[_CARD_KEY]
    assert report["error"].startswith("static_path")
    assert not offered


async def test_it_registers_once(offered):
    hass = CardHass()

    await _async_register_card(hass)
    await _async_register_card(hass)

    assert len(offered) == 1
    assert len(hass.http.registered) == 1


async def test_the_frontend_and_http_are_hard_dependencies():
    """Soft ones only order the setup *if* the other is being set up anyway,
    so on a cold boot the card could be offered to a frontend that then reset
    the list - which looks exactly like the card never existing."""
    import json
    import pathlib

    manifest = json.loads(
        (
            pathlib.Path(__file__).resolve().parents[1]
            / "custom_components/battery_management/manifest.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest.get("dependencies") == ["http", "frontend"]
    assert "after_dependencies" not in manifest
