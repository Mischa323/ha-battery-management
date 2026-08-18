"""Pytest bootstrap.

Home Assistant itself is only installable on Python 3.12 and is not supported on
Windows, so it is deliberately *not* required to run this test suite. When the
real package is missing we register a minimal stub that provides the handful of
symbols ``coordinator.py`` imports; the devcontainer and CI have the real thing
and use it instead. That keeps ``pytest`` runnable on the maintainer's Windows
box while still exercising the genuine control logic.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _homeassistant_available() -> bool:
    try:
        return importlib.util.find_spec("homeassistant.core") is not None
    except (ImportError, ValueError):
        return False


def _install_homeassistant_stub() -> None:
    """Register just enough of `homeassistant` for coordinator.py to import."""
    ha = types.ModuleType("homeassistant")
    ha.__path__ = []  # mark as a package

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        """Stand-in used only for type annotations."""

    def callback(func):
        """HA's @callback is a no-op marker outside the event loop."""
        return func

    class ServiceCall:
        """Stand-in carrying just the service data."""

        def __init__(self, data: dict | None = None) -> None:
            self.data = data or {}

    core.HomeAssistant = HomeAssistant
    core.callback = callback
    core.ServiceCall = ServiceCall

    # importing the integration package runs its __init__, which needs these
    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:
        """Stand-in used only for type annotations."""

    config_entries.ConfigEntry = ConfigEntry

    ha_const = types.ModuleType("homeassistant.const")

    class Platform(StrEnum):
        SWITCH = "switch"
        BUTTON = "button"
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"
        NUMBER = "number"
        SELECT = "select"

    ha_const.Platform = Platform

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    event = types.ModuleType("homeassistant.helpers.event")

    def async_track_time_interval(hass, action, interval, *args, **kwargs):
        """Return an unsubscribe callable. The stub timer never fires; tests
        drive `_async_tick` directly so ticks stay deterministic."""
        return lambda: None

    event.async_track_time_interval = async_track_time_interval

    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")

    def async_get_clientsession(hass, *args, **kwargs):
        """Whatever the fake hass was given. The stub never reaches a network:
        tests that care hand `hass.session` a fake, and the rest never ask."""
        return getattr(hass, "session", None)

    aiohttp_client.async_get_clientsession = async_get_clientsession

    storage = types.ModuleType("homeassistant.helpers.storage")

    class Store:
        """In-memory stand-in for HA's JSON-on-disk Store."""

        def __init__(self, hass, version, key, **kwargs) -> None:
            self.key = key
            self.data = None

        async def async_load(self):
            return self.data

        async def async_save(self, data) -> None:
            self.data = data

        def async_delay_save(self, data_func, delay=0) -> None:
            # the real one debounces; tests want the value immediately
            self.data = data_func()

    storage.Store = Store

    issue_registry = types.ModuleType("homeassistant.helpers.issue_registry")

    class IssueSeverity(StrEnum):
        CRITICAL = "critical"
        ERROR = "error"
        WARNING = "warning"

    def async_create_issue(hass, domain, issue_id, **kwargs):
        hass.data.setdefault("_issues", {})[issue_id] = kwargs

    def async_delete_issue(hass, domain, issue_id):
        hass.data.setdefault("_issues", {}).pop(issue_id, None)

    issue_registry.IssueSeverity = IssueSeverity
    issue_registry.async_create_issue = async_create_issue
    issue_registry.async_delete_issue = async_delete_issue

    config_validation = types.ModuleType("homeassistant.helpers.config_validation")
    config_validation.string = str

    # The card is served by a view of our own, so that the cache headers are
    # ours to set rather than guessed. Both of these are HA-only imports, done
    # lazily inside the factory - stubbed here so the stubbed run still covers
    # the registration path rather than skipping it.
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    http = types.ModuleType("homeassistant.components.http")

    class HomeAssistantView:
        url = None
        name = None
        requires_auth = True

    http.HomeAssistantView = HomeAssistantView
    components.http = http

    util = types.ModuleType("homeassistant.util")
    util.__path__ = []
    dt = types.ModuleType("homeassistant.util.dt")
    dt.utcnow = lambda: datetime.now(timezone.utc)
    # the real one is local time; tests that care pin it themselves
    dt.now = lambda: datetime.now(timezone.utc)
    # the real one converts to the configured zone; the stub has none,
    # so tests that care about a day boundary pin the clock themselves
    dt.as_local = lambda value: value

    # bind submodules as attributes too, so `from homeassistant.util import dt`
    # resolves without touching the real import machinery
    ha.core = core
    ha.components = components
    ha.config_entries = config_entries
    ha.const = ha_const
    ha.helpers = helpers
    ha.util = util
    helpers.event = event
    helpers.aiohttp_client = aiohttp_client
    helpers.storage = storage
    helpers.config_validation = config_validation
    helpers.issue_registry = issue_registry
    util.dt = dt

    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.core": core,
            "homeassistant.components": components,
            "homeassistant.components.http": http,
            "homeassistant.config_entries": config_entries,
            "homeassistant.const": ha_const,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.event": event,
            "homeassistant.helpers.aiohttp_client": aiohttp_client,
            "homeassistant.helpers.storage": storage,
            "homeassistant.helpers.config_validation": config_validation,
            "homeassistant.helpers.issue_registry": issue_registry,
            "homeassistant.util": util,
            "homeassistant.util.dt": dt,
        }
    )


if not _homeassistant_available():
    _install_homeassistant_stub()
