"""The Battery Management integration."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import BatteryCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
]

SERVICE_SET_SETPOINT = "set_setpoint"
SERVICE_START_FAST_CHARGE = "start_fast_charge"
SERVICE_STOP_FAST_CHARGE = "stop_fast_charge"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_SETPOINT = "setpoint"

_SERVICES_KEY = f"{DOMAIN}_services_registered"

_TARGET_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})
_SET_SETPOINT_SCHEMA = _TARGET_SCHEMA.extend(
    {vol.Required(ATTR_SETPOINT): vol.Coerce(float)}
)

CARD_FILENAME = "battery-management-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILENAME}"
_CARD_KEY = f"{DOMAIN}_card_registered"


async def _card_version(hass: HomeAssistant) -> str:
    """This release's version, for busting the browser's cache of the card.

    Read from the manifest Home Assistant has already loaded - opening the file
    again would be blocking I/O in the event loop for a query string.
    """
    try:
        from homeassistant.loader import async_get_integration

        integration = await async_get_integration(hass, DOMAIN)
        return str(integration.version or "0")
    except Exception:  # noqa: BLE001 - never block setup over a query string
        return "0"


async def _async_card_url(hass: HomeAssistant) -> str:
    """The cache-stamped URL for the card.

    Both registrations need it and either can go first - `frontend` and
    `lovelace` set up independently - so it cannot live inside only one of
    them. Same recipe on both sides, so the two can never disagree about which
    URL the card is at.
    """
    version = await _card_version(hass)
    card_file = Path(__file__).parent / "www" / CARD_FILENAME

    def _digest() -> str:
        try:
            return hashlib.sha256(card_file.read_bytes()).hexdigest()[:12]
        except OSError:
            return "0"

    digest = await hass.async_add_executor_job(_digest)
    return f"{CARD_URL}?v={version}-{digest}"


async def _async_schedule_card(hass: HomeAssistant) -> None:
    """Register the card once the frontend exists - whenever that is.

    The ordering problem is real: on a cold boot this integration can be set
    up before the frontend, and a card offered to a frontend that has not
    initialised is simply lost. But making `frontend` a *hard* dependency,
    which was the first fix, is a worse trade - the batteries then stop being
    coordinated at all if the frontend fails to start, and a battery
    controller has no business depending on a web interface.

    `async_when_setup` is the middle: it fires immediately if the frontend is
    already up, and waits for it if it is not. If the frontend never comes,
    the card never registers and everything else carries on.
    """
    try:
        from homeassistant.setup import async_when_setup
    except ImportError:      # pragma: no cover - very old cores
        await _async_register_card(hass)
        return

    async def _ready(hass: HomeAssistant, _component: str) -> None:
        await _async_register_card(hass)

    async def _lovelace_ready(hass: HomeAssistant, _component: str) -> None:
        await _async_register_resource(hass)

    async_when_setup(hass, "frontend", _ready)
    async_when_setup(hass, "lovelace", _lovelace_ready)


async def _async_register_resource(hass: HomeAssistant) -> None:
    """Also register the card as a Lovelace *resource*, not just an extra module.

    These are not two ways of saying the same thing, and the difference is the
    whole bug. `add_extra_js_url` puts a `<script type="module">` on the page,
    which the browser loads whenever it gets round to it - Lovelace does not
    wait for it. Resources it does wait for, before it builds any card.

    So on a cold load the module wins the race and everything is fine, and on
    an ordinary reload the dashboard comes out of cache, renders immediately,
    finds no `battery-management-card` registered yet and draws an error card
    in its place. Reported from the primary site as "fine after Ctrl+Shift+R,
    broken on F5", on desktop and phone alike, and confirmed by hand: adding
    the resource made it stop. Every other custom card on that dashboard was
    already a resource, which is why ours was the only one failing.

    The extra module stays as well. Loading twice is harmless - `defineCard`
    checks `customElements.get` first, and `double_load.mjs` pins that - and it
    is the only mechanism left if this one cannot run, which is a real case:
    a YAML-mode dashboard has no writable resource collection at all.
    """
    report = hass.data.get(_CARD_KEY) or {}
    url = report.get("offered_to_frontend") or await _async_card_url(hass)
    try:
        data = hass.data.get("lovelace")
        resources = getattr(data, "resources", None)
        if resources is None and isinstance(data, dict):
            resources = data.get("resources")
        if resources is None:
            report["resource"] = "no_lovelace"
            return
        # YAML-mode dashboards have a read-only collection: the user declares
        # resources in their own file, and writing there is not ours to do.
        if not hasattr(resources, "async_create_item"):
            report["resource"] = "yaml_mode"
            _LOGGER.info(
                "Lovelace is in YAML mode, so the card cannot be registered as "
                "a resource automatically. Add %s as a module under "
                "`lovelace: resources:` if cards intermittently fail to load.",
                url,
            )
            return
        if hasattr(resources, "loaded") and not resources.loaded:
            await resources.async_load()

        # Match on the path and ignore the query, so the cache-busting stamp
        # can change without leaving a second stale entry behind - and so a
        # hand-added one gets adopted instead of duplicated.
        mine = [
            item
            for item in resources.async_items()
            if str(item.get("url", "")).split("?")[0] == CARD_URL
        ]
        if not mine:
            await resources.async_create_item({"res_type": "module", "url": url})
            report["resource"] = "created"
        elif mine[0].get("url") != url:
            await resources.async_update_item(mine[0]["id"], {"url": url})
            report["resource"] = "updated"
        else:
            report["resource"] = "present"
        _LOGGER.debug("card resource %s: %s", report["resource"], url)
    except Exception as err:  # noqa: BLE001 - a dashboard nicety, never a blocker
        report["resource"] = f"error: {err}"
        _LOGGER.warning(
            "Could not register the card as a Lovelace resource (%s). Cards may "
            "show an error until the page is hard-refreshed; adding %s manually "
            "under Settings > Dashboards > Resources fixes that.",
            err,
            url,
        )


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the Lovelace card and add it as an extra frontend module (once).

    Every step says what it did. "Custom element doesn't exist" is reported
    from the browser, and until now nothing on the server side said whether
    the file had been served, which version, or whether it had been offered to
    the frontend at all - so the only way to investigate was to guess.

    The outcome is stashed in `hass.data` and travels with the diagnostics, so
    the question "is the card registered" has an answer that does not depend on
    anyone having had debug logging switched on at the right moment.
    """
    if hass.data.get(_CARD_KEY):
        return
    card_file = Path(__file__).parent / "www" / CARD_FILENAME
    card_path = str(card_file)
    version = await _card_version(hass)
    report: dict = {"path": card_path, "url": CARD_URL, "version": version}
    hass.data[_CARD_KEY] = report

    # A missing file registers perfectly happily and then 404s, which reaches
    # the user as "custom element doesn't exist" - the same message as a dozen
    # unrelated causes. Check it here, where the path is known.
    if not await hass.async_add_executor_job(card_file.is_file):
        report["error"] = "file_missing"
        _LOGGER.error(
            "The Battery Management card is missing from %s, so no card can load. "
            "Reinstall the integration through HACS.",
            card_path,
        )
        return

    def _fingerprint() -> tuple[int, str]:
        """Size and a short content hash, in one trip to the disk."""
        data = card_file.read_bytes()
        return len(data), hashlib.sha256(data).hexdigest()[:12]

    report["bytes"], report["fingerprint"] = await hass.async_add_executor_job(
        _fingerprint
    )

    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, card_path, False)]
        )
        report["served"] = "async"
    except Exception as err:  # noqa: BLE001  -- fall back to the legacy sync API
        try:
            hass.http.register_static_path(CARD_URL, card_path, False)
            report["served"] = "legacy"
        except Exception as err2:  # noqa: BLE001
            report["error"] = f"static_path: {err2}"
            _LOGGER.warning(
                "Could not serve the Battery Management card (%s; legacy path also "
                "failed: %s). No card will load.",
                err,
                err2,
            )
            return

    try:
        from homeassistant.components.frontend import add_extra_js_url

        # Stamped, because browsers cache this file hard and an unversioned URL
        # means an update silently does nothing: the old script keeps running,
        # so a card added in a new release never appears in the card list
        # however many times you look for it. Found exactly that way.
        #
        # The version alone was not enough, and that gap was reported from the
        # primary site: the cards errored on an ordinary reload and rendered
        # fine after Ctrl+Shift+R, on desktop and phone alike. The card file
        # changes far more often than the manifest version does - every fix
        # between releases, and every checkout during development - so the URL
        # stayed identical while the contents moved underneath it, and every
        # browser that had ever loaded the page kept serving its stale copy.
        # The content hash closes that: same bytes, same URL, cache still
        # works; different bytes, different URL, no stale copy can survive.
        url = f"{CARD_URL}?v={version}-{report['fingerprint']}"
        add_extra_js_url(hass, url)
        report["offered_to_frontend"] = url
        _LOGGER.info(
            "Battery Management card registered: %s (%s bytes, served %s). "
            "The query string carries a hash of the file, so a stale copy "
            "cannot survive a reload.",
            url,
            report.get("bytes"),
            report.get("served"),
        )
    except Exception as err:  # noqa: BLE001
        report["error"] = f"add_extra_js_url: {err}"
        _LOGGER.warning(
            "Could not auto-add the card resource (%s); add %s manually under "
            "Settings > Dashboards > Resources",
            err,
            CARD_URL,
        )


def _targets(hass: HomeAssistant, call: ServiceCall) -> list[BatteryCoordinator]:
    """Coordinators a service call applies to; all of them unless narrowed."""
    stored: dict = hass.data.get(DOMAIN, {})
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if entry_id is None:
        return list(stored.values())
    coordinator = stored.get(entry_id)
    return [coordinator] if coordinator else []


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Register the domain services once, not per config entry."""
    if hass.data.get(_SERVICES_KEY):
        return

    async def _set_setpoint(call: ServiceCall) -> None:
        for coordinator in _targets(hass, call):
            await coordinator.async_set_setpoint(call.data[ATTR_SETPOINT])

    async def _start_fast_charge(call: ServiceCall) -> None:
        for coordinator in _targets(hass, call):
            await coordinator.async_set_fast_charge(True)

    async def _stop_fast_charge(call: ServiceCall) -> None:
        for coordinator in _targets(hass, call):
            await coordinator.async_set_fast_charge(False)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_SETPOINT, _set_setpoint, schema=_SET_SETPOINT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_START_FAST_CHARGE, _start_fast_charge, schema=_TARGET_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP_FAST_CHARGE, _stop_fast_charge, schema=_TARGET_SCHEMA
    )
    hass.data[_SERVICES_KEY] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Battery Management from a config entry."""
    await _async_schedule_card(hass)
    _async_register_services(hass)

    coordinator = BatteryCoordinator(hass, entry)
    await coordinator.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and revert the batteries to a safe mode."""
    coordinator: BatteryCoordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        await coordinator.async_stop(revert=True)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
